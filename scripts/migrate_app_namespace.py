#!/usr/bin/env python3
"""APP-003 app-scoped migration harness for a shared PostgreSQL database.

Isolation relies on restricted migrator roles, schema privileges, transaction
boundaries, and an app-scoped ledger — not on regex sanitization of SQL.
Never reads DATABASE_URL or SUPABASE_DB_URL from the environment.

Trusted SQL limitation: migration files are reviewed operator SQL. The harness
does not sandbox COMMIT/ROLLBACK/SET TRANSACTION inside those files; a file
that issues transaction-control statements can end the harness transaction
early. Reviewers must treat transaction control in migration SQL as forbidden
unless the apply path is redesigned around single-statement execution.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# PostgreSQL NAMEDATALEN-1 = 63; longest derived suffix is "_migrator" (9).
MAX_NAMESPACE_LEN = 63 - len("_migrator")
NAMESPACE_RE = re.compile(
    rf"^[a-z][a-z0-9_]{{0,{MAX_NAMESPACE_LEN - 1}}}$"
)
MIGRATION_ID_RE = re.compile(r"^[0-9]{4}_[a-z0-9_]+$")

RESERVED_NAMESPACES = frozenset(
    {
        "public",
        "shared",
        "auth",
        "extensions",
        "graphql_public",
        "realtime",
        "storage",
        "supabase_functions",
        "supabase_migrations",
        "information_schema",
        "app_migration_control",
        "pg_catalog",
        "pg_toast",
    }
)

LOCK_RETRY_ATTEMPTS = 8
LOCK_RETRY_SLEEP_SECONDS = 0.25

_REDACT_PATTERNS = (
    re.compile(r"postgresql://[^\s]+", re.IGNORECASE),
    re.compile(r"postgres://[^\s]+", re.IGNORECASE),
    re.compile(r"password\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\bDETAIL\s*:.*", re.IGNORECASE),
    re.compile(r"\bCONTEXT\s*:.*", re.IGNORECASE),
    re.compile(r"\bHINT\s*:.*", re.IGNORECASE),
    re.compile(r"\bWHERE\b.*", re.IGNORECASE),
)


class MigrationError(RuntimeError):
    pass


class LockBusy(MigrationError):
    pass


@dataclass(frozen=True)
class Migration:
    migration_id: str
    path: Path
    sql_bytes: bytes
    checksum_sha256: str


@dataclass(frozen=True)
class MigrateResult:
    status: str
    applied_count: int
    skipped_count: int


def validate_namespace(namespace: str) -> str:
    if not NAMESPACE_RE.fullmatch(namespace):
        raise ValueError(
            f"namespace must be lowercase alphanumeric/underscore, "
            f"max {MAX_NAMESPACE_LEN} chars (PostgreSQL 63-byte role suffix limit)"
        )
    if namespace in RESERVED_NAMESPACES or namespace.startswith("pg_"):
        raise ValueError("namespace is reserved")
    for suffix in ("_owner", "_migrator", "_runtime"):
        if len(namespace) + len(suffix) > 63:
            raise ValueError(
                f"namespace too long for derived role suffix {suffix!r}"
            )
    return namespace


def resolve_database_url(explicit_url: Optional[str]) -> str:
    """Require an explicit URL; ignore inherited DATABASE_URL / SUPABASE_DB_URL."""
    if not explicit_url or not str(explicit_url).strip():
        raise SystemExit(
            "migrate_app_namespace: database URL is required "
            "(DATABASE_URL / SUPABASE_DB_URL from the environment are ignored)"
        )
    return str(explicit_url).strip()


def load_database_url_from_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"database URL file is empty: {path}")
    return resolve_database_url(text.splitlines()[0].strip())


def load_database_url_from_stdin() -> str:
    text = sys.stdin.read().strip()
    if not text:
        raise SystemExit("database URL stdin is empty")
    return resolve_database_url(text.splitlines()[0].strip())


def redact_db_error(message: str) -> str:
    """Strip credential/SQL fragments from server/driver error text for CLI output."""
    redacted = str(message)
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    if len(redacted) > 240:
        redacted = redacted[:240] + "…"
    return redacted


def discover_migrations(migrations_dir: Path) -> List[Migration]:
    """Read each migration file once; checksum and execution share the same bytes."""
    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory not found: {migrations_dir}")
    found: List[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        migration_id = path.stem
        if not MIGRATION_ID_RE.fullmatch(migration_id):
            raise MigrationError(
                f"invalid migration id {migration_id!r}; expected NNNN_slug.sql"
            )
        sql_bytes = path.read_bytes()
        digest = hashlib.sha256(sql_bytes).hexdigest()
        found.append(
            Migration(
                migration_id=migration_id,
                path=path,
                sql_bytes=sql_bytes,
                checksum_sha256=digest,
            )
        )
    found.sort(key=lambda item: item.migration_id)
    return found


def advisory_lock_keys(namespace: str) -> Tuple[int, int]:
    digest = hashlib.sha256(f"app_migration:{namespace}".encode("utf-8")).digest()
    key1 = int.from_bytes(digest[0:4], "big") & 0x7FFFFFFF
    key2 = int.from_bytes(digest[4:8], "big") & 0x7FFFFFFF
    return key1, key2


def _connect(database_url: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise MigrationError(
            "psycopg is required in the project venv "
            "(pip install -r requirements-bootstrap.txt)"
        ) from exc
    return psycopg.connect(database_url)


def _execute_script(conn, script_bytes: bytes) -> None:
    """Run a reviewed migration script (multi-statement) via libpq.

    Statement batching is a driver necessity for trusted files only — it is not
    a SQL sanitizer and must not be treated as one. Transaction-control
    statements inside script_bytes are not intercepted.
    """
    from psycopg.pq import ExecStatus

    result = conn.pgconn.exec_(script_bytes)
    if result is None:
        raise MigrationError("migration script returned no result")
    status = result.status
    if status not in (
        ExecStatus.COMMAND_OK,
        ExecStatus.TUPLES_OK,
        ExecStatus.EMPTY_QUERY,
    ):
        message = result.error_message
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        raise MigrationError(message or f"migration script failed ({status})")


def migrate_app(
    *,
    database_url: str,
    namespace: str,
    migrations_dir: Path,
) -> MigrateResult:
    namespace = validate_namespace(namespace)
    migrations = discover_migrations(Path(migrations_dir))
    applied = 0
    skipped = 0
    key1, key2 = advisory_lock_keys(namespace)

    from psycopg import sql as psql

    last_error: Optional[BaseException] = None
    with _connect(database_url) as conn:
        # Keep session GUC for record_applied / helpers; lock is transaction-scoped
        # so it is held until the real COMMIT/ROLLBACK of the apply transaction.
        conn.execute(
            "SELECT set_config('app.migration_namespace', %s, false)",
            (namespace,),
        )
        conn.commit()

        for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
            try:
                with conn.transaction():
                    locked = conn.execute(
                        "SELECT pg_try_advisory_xact_lock(%s, %s)",
                        (key1, key2),
                    ).fetchone()[0]
                    if not locked:
                        raise LockBusy(
                            f"advisory lock busy for namespace {namespace!r}"
                        )

                    conn.execute(
                        psql.SQL("SET LOCAL search_path TO {}, pg_temp").format(
                            psql.Identifier(namespace)
                        )
                    )

                    max_applied = conn.execute(
                        """
                        SELECT max(migration_id)
                        FROM app_migration_control.ledger
                        WHERE app_namespace = %s
                        """,
                        (namespace,),
                    ).fetchone()[0]

                    for migration in migrations:
                        row = conn.execute(
                            """
                            SELECT checksum_sha256
                            FROM app_migration_control.ledger
                            WHERE app_namespace = %s AND migration_id = %s
                            """,
                            (namespace, migration.migration_id),
                        ).fetchone()
                        if row is not None:
                            if row[0] != migration.checksum_sha256:
                                raise MigrationError(
                                    f"checksum mismatch for {migration.migration_id}: "
                                    "ledger differs from migration file (aborting)"
                                )
                            skipped += 1
                            continue

                        if (
                            max_applied is not None
                            and migration.migration_id < max_applied
                        ):
                            raise MigrationError(
                                f"out-of-order migration {migration.migration_id}: "
                                f"ledger max is {max_applied} (aborting)"
                            )

                        _execute_script(conn, migration.sql_bytes)
                        conn.execute(
                            "SELECT app_migration_control.record_applied(%s, %s, %s)",
                            (
                                namespace,
                                migration.migration_id,
                                migration.checksum_sha256,
                            ),
                        )
                        applied += 1
                        max_applied = migration.migration_id
                return MigrateResult(
                    status="ok", applied_count=applied, skipped_count=skipped
                )
            except LockBusy as exc:
                last_error = exc
                applied = 0
                skipped = 0
                time.sleep(LOCK_RETRY_SLEEP_SECONDS * attempt)
                continue

        raise MigrationError(
            f"could not acquire advisory lock for namespace {namespace!r} "
            f"after {LOCK_RETRY_ATTEMPTS} attempts"
        ) from last_error


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply reviewed app-namespace migrations with an app-scoped ledger. "
            "Does not use supabase db push or shared migration history. "
            "Prefer --database-url-file or --database-url-stdin so credentials "
            "are not visible in process listings."
        )
    )
    url_group = parser.add_mutually_exclusive_group(required=True)
    url_group.add_argument(
        "--database-url",
        help=(
            "Explicit PostgreSQL URL (env DATABASE_URL/SUPABASE_DB_URL ignored). "
            "Allowed only with --local-disposable-only for tests; prefer file/stdin."
        ),
    )
    url_group.add_argument(
        "--database-url-file",
        type=Path,
        help="Read database URL from the first line of this file",
    )
    url_group.add_argument(
        "--database-url-stdin",
        action="store_true",
        help="Read database URL from stdin (first line)",
    )
    parser.add_argument("--namespace", required=True, help="App schema/namespace")
    parser.add_argument(
        "--migrations-dir",
        required=True,
        type=Path,
        help="Directory of NNNN_slug.sql files",
    )
    parser.add_argument(
        "--local-disposable-only",
        action="store_true",
        help=(
            "Acknowledge this run targets a disposable/local database. "
            "Required unless APP003_ALLOW_NON_LOCAL=1 is set for reviewed ops."
        ),
    )
    return parser


def _assert_local_guard(args: argparse.Namespace) -> None:
    allow_non_local = os_environ_flag("APP003_ALLOW_NON_LOCAL")
    if args.local_disposable_only:
        return
    if allow_non_local:
        return
    raise SystemExit(
        "Refusing to run without --local-disposable-only "
        "(unattended shared-project migration remains disabled; "
        "set APP003_ALLOW_NON_LOCAL=1 only for reviewed manual apply)"
    )


def _resolve_url_from_args(args: argparse.Namespace) -> str:
    if args.database_url_file is not None:
        return load_database_url_from_file(args.database_url_file)
    if args.database_url_stdin:
        return load_database_url_from_stdin()
    if args.database_url:
        if not args.local_disposable_only:
            raise SystemExit(
                "Refusing --database-url without --local-disposable-only "
                "(use --database-url-file or --database-url-stdin to avoid "
                "credentials in process listings)"
            )
        return resolve_database_url(args.database_url)
    raise SystemExit("database URL is required")


def os_environ_flag(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _assert_local_guard(args)
    database_url = _resolve_url_from_args(args)
    try:
        result = migrate_app(
            database_url=database_url,
            namespace=args.namespace,
            migrations_dir=args.migrations_dir,
        )
    except (MigrationError, ValueError) as exc:
        print(
            f"migrate_app_namespace: {redact_db_error(str(exc))}",
            file=sys.stderr,
        )
        return 1
    print(
        f"ok applied={result.applied_count} skipped={result.skipped_count} "
        f"namespace={args.namespace}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

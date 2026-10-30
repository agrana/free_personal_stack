#!/usr/bin/env python3
"""Managed-compatible PostgreSQL app foundation (non-superuser CREATEROLE).

Library-only API: callers pass explicit trusted connections or DSNs held in
memory. Credentials never appear in argv, logs, failure codes, or reprs.
Does not read DATABASE_URL / SUPABASE_DB_URL. Importing this module performs
no network or database action.

This slice provisions per-app data + control schemas, applies reviewed
migrations as the migrator LOGIN, and checks RLS invariants. It does not
wire GitHub Secrets, managed APIs, poolers, or live workflows.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

# NAMEDATALEN-1 = 63; longest derived suffix is "_migrator" (9).
MAX_NAMESPACE_LEN = 63 - len("_migrator")
NAMESPACE_RE = re.compile(rf"^[a-z][a-z0-9_]{{0,{MAX_NAMESPACE_LEN - 1}}}$")
MIGRATION_ID_RE = re.compile(r"^[0-9]{4}_[a-z0-9_]+$")
REPOSITORY_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")
PROJECT_REF_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

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

# Predefined privileged groups that app roles must never join.
PRIVILEGED_GROUP_ROLES = frozenset(
    {
        "pg_read_all_data",
        "pg_write_all_data",
        "pg_read_all_settings",
        "pg_read_all_stats",
        "pg_stat_scan_tables",
        "pg_monitor",
        "pg_database_owner",
        "pg_signal_backend",
        "pg_checkpoint",
        "pg_maintain",
        "pg_use_reserved_connections",
        "pg_create_subscription",
        "pg_execute_server_program",
        "pg_read_server_files",
        "pg_write_server_files",
    }
)

# Table ACL privileges forbidden for PUBLIC / anon / authenticated.
_FORBIDDEN_PUBLIC_TABLE_PRIVS = frozenset("rawdDxtm")
# Runtime may hold deliberate DML only; TRUNCATE/REFERENCES/TRIGGER/MAINTAIN are unsafe.
_RUNTIME_ALLOWED_TABLE_PRIVS = frozenset("rawd")
_FORBIDDEN_PUBLIC_COLUMN_PRIVS = frozenset("rawdx")
_RUNTIME_ALLOWED_COLUMN_PRIVS = frozenset("rawd")

LOCK_RETRY_ATTEMPTS = 8
LOCK_RETRY_SLEEP_SECONDS = 0.25

_SQL_SUPPORT_DIR = (
    Path(__file__).resolve().parents[1] / "db" / "managed_database" / "sql"
)


class FailureCode:
    NAMESPACE_INVALID = "NAMESPACE_INVALID"
    REPOSITORY_ID_INVALID = "REPOSITORY_ID_INVALID"
    PROJECT_REF_INVALID = "PROJECT_REF_INVALID"
    CREDENTIAL_MISSING = "CREDENTIAL_MISSING"
    PUBLIC_CREATE_PRIVILEGE = "PUBLIC_CREATE_PRIVILEGE"
    SCHEMA_COLLISION = "SCHEMA_COLLISION"
    ROLE_COLLISION = "ROLE_COLLISION"
    CONTROL_COLLISION = "CONTROL_COLLISION"
    OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
    PRIVILEGE_DRIFT = "PRIVILEGE_DRIFT"
    ACTIVE_TRANSACTION = "ACTIVE_TRANSACTION"
    MIGRATION_PATH = "MIGRATION_PATH"
    MIGRATION_ID_INVALID = "MIGRATION_ID_INVALID"
    MIGRATION_DUPLICATE = "MIGRATION_DUPLICATE"
    MIGRATION_CHECKSUM = "MIGRATION_CHECKSUM"
    MIGRATION_ORDER = "MIGRATION_ORDER"
    MIGRATION_LOCK = "MIGRATION_LOCK"
    MIGRATION_EXEC = "MIGRATION_EXEC"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    AUTH_DENIED = "AUTH_DENIED"
    CONNECTION = "CONNECTION"
    INTERNAL = "INTERNAL"


class ManagedDatabaseError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        safe = detail.strip()
        if safe:
            super().__init__(f"{code}: {safe}")
        else:
            super().__init__(code)

    def __repr__(self) -> str:
        return f"ManagedDatabaseError(code={self.code!r})"


class LockBusy(ManagedDatabaseError):
    def __init__(self) -> None:
        super().__init__(FailureCode.MIGRATION_LOCK, "advisory lock busy")


@dataclass(frozen=True)
class RoleNames:
    owner: str
    migrator: str
    runtime: str
    control_schema: str


@dataclass(frozen=True)
class Migration:
    migration_id: str
    path: Path
    sql_bytes: bytes
    checksum_sha256: str


@dataclass(frozen=True)
class ProvisionResult:
    status: str
    namespace: str
    repository_id: str
    project_ref: str
    roles: RoleNames
    created: bool


@dataclass(frozen=True)
class MigrateResult:
    status: str
    applied_count: int
    skipped_count: int


@dataclass(frozen=True)
class _Secret:
    """In-memory credential wrapper that never reveals the value in repr/str."""

    value: str

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    def __str__(self) -> str:
        return "Secret(<redacted>)"


def _raise_public(code: str, detail: str = "") -> None:
    """Raise a public error without chaining raw driver/server causes."""
    raise ManagedDatabaseError(code, detail) from None


def validate_namespace(namespace: str) -> str:
    if not isinstance(namespace, str) or not NAMESPACE_RE.fullmatch(namespace):
        raise ManagedDatabaseError(
            FailureCode.NAMESPACE_INVALID,
            f"must be lowercase alphanumeric/underscore, max {MAX_NAMESPACE_LEN}",
        )
    if namespace in RESERVED_NAMESPACES or namespace.startswith("pg_"):
        raise ManagedDatabaseError(FailureCode.NAMESPACE_INVALID, "reserved")
    for suffix in ("_owner", "_migrator", "_runtime", "_control"):
        if len(namespace) + len(suffix) > 63:
            raise ManagedDatabaseError(
                FailureCode.NAMESPACE_INVALID,
                "derived identifier exceeds PostgreSQL limit",
            )
    return namespace


def validate_repository_id(repository_id: str) -> str:
    if not isinstance(repository_id, str) or not REPOSITORY_ID_RE.fullmatch(
        repository_id
    ):
        raise ManagedDatabaseError(FailureCode.REPOSITORY_ID_INVALID)
    return repository_id


def validate_project_ref(project_ref: str) -> str:
    if not isinstance(project_ref, str) or not PROJECT_REF_RE.fullmatch(project_ref):
        raise ManagedDatabaseError(FailureCode.PROJECT_REF_INVALID)
    return project_ref


def derive_role_names(namespace: str) -> RoleNames:
    namespace = validate_namespace(namespace)
    return RoleNames(
        owner=f"{namespace}_owner",
        migrator=f"{namespace}_migrator",
        runtime=f"{namespace}_runtime",
        control_schema=f"{namespace}_control",
    )


def split_sql_statements(script: str) -> List[str]:
    """Split trusted operator SQL into statements for library-owned bootstrap SQL.

    Migration apply prefers a single PostgreSQL EXECUTE of the full reviewed
    text. This splitter remains for control_objects bootstrap and tests.
    Respects nested block comments, E-strings, dollar-quotes, and quoted
    identifiers. Not a security sandbox.
    """
    statements: List[str] = []
    buf: List[str] = []
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            while i < n and script[i] not in "\n\r":
                buf.append(script[i])
                i += 1
            continue
        if ch == "/" and nxt == "*":
            depth = 1
            buf.append(ch)
            buf.append(nxt)
            i += 2
            while i < n and depth > 0:
                if i + 1 < n and script[i] == "/" and script[i + 1] == "*":
                    buf.append(script[i])
                    buf.append(script[i + 1])
                    i += 2
                    depth += 1
                    continue
                if i + 1 < n and script[i] == "*" and script[i + 1] == "/":
                    buf.append(script[i])
                    buf.append(script[i + 1])
                    i += 2
                    depth -= 1
                    continue
                buf.append(script[i])
                i += 1
            continue
        if (ch == "E" or ch == "e") and nxt == "'":
            buf.append(ch)
            buf.append(nxt)
            i += 2
            while i < n:
                buf.append(script[i])
                if script[i] == "\\":
                    if i + 1 < n:
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                if script[i] == "'":
                    if i + 1 < n and script[i + 1] == "'":
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == "'":
                    if i + 1 < n and script[i + 1] == "'":
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == '"':
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == '"':
                    if i + 1 < n and script[i + 1] == '"':
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] == "_"):
                j += 1
            if j < n and script[j] == "$":
                tag = script[i : j + 1]
                buf.append(tag)
                i = j + 1
                end = script.find(tag, i)
                if end < 0:
                    buf.append(script[i:])
                    i = n
                    break
                buf.append(script[i:end])
                buf.append(tag)
                i = end + len(tag)
                continue
        if ch == ";":
            text = "".join(buf).strip()
            if text:
                statements.append(text)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    trailing = "".join(buf).strip()
    if trailing:
        statements.append(trailing)
    return statements


def discover_migrations(migrations_dir: Path) -> List[Migration]:
    root = Path(migrations_dir)
    if not root.is_dir():
        raise ManagedDatabaseError(FailureCode.MIGRATION_PATH, "directory not found")
    try:
        resolved_root = root.resolve(strict=True)
    except FileNotFoundError:
        _raise_public(FailureCode.MIGRATION_PATH, "directory not found")

    found: List[Migration] = []
    seen_ids: set[str] = set()
    for path in sorted(root.glob("*.sql")):
        if path.is_symlink() or not path.is_file():
            raise ManagedDatabaseError(
                FailureCode.MIGRATION_PATH, "symlink or non-file rejected"
            )
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            _raise_public(FailureCode.MIGRATION_PATH, "unresolvable migration path")
        if not str(resolved).startswith(str(resolved_root) + "/") and resolved != resolved_root:
            raise ManagedDatabaseError(
                FailureCode.MIGRATION_PATH, "path escapes migrations directory"
            )
        migration_id = path.stem
        if not MIGRATION_ID_RE.fullmatch(migration_id):
            raise ManagedDatabaseError(FailureCode.MIGRATION_ID_INVALID, migration_id)
        if migration_id in seen_ids:
            raise ManagedDatabaseError(FailureCode.MIGRATION_DUPLICATE, migration_id)
        seen_ids.add(migration_id)
        sql_bytes = path.read_bytes()
        found.append(
            Migration(
                migration_id=migration_id,
                path=path,
                sql_bytes=sql_bytes,
                checksum_sha256=hashlib.sha256(sql_bytes).hexdigest(),
            )
        )
    found.sort(key=lambda item: item.migration_id)
    return found


def advisory_lock_keys(namespace: str) -> Tuple[int, int]:
    digest = hashlib.sha256(
        f"managed_database_migration:{namespace}".encode("utf-8")
    ).digest()
    key1 = int.from_bytes(digest[0:4], "big") & 0x7FFFFFFF
    key2 = int.from_bytes(digest[4:8], "big") & 0x7FFFFFFF
    return key1, key2


def _require_secret(value: Optional[str], label: str) -> _Secret:
    if not isinstance(value, str) or not value:
        raise ManagedDatabaseError(FailureCode.CREDENTIAL_MISSING, label)
    if "\x00" in value:
        raise ManagedDatabaseError(FailureCode.CREDENTIAL_MISSING, label)
    return _Secret(value)


def _connect(dsn: str):
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        _raise_public(FailureCode.INTERNAL, "psycopg is required in the project venv")
    try:
        conn = psycopg.connect(dsn)
        # Autocommit so read probes do not open an implicit transaction that
        # would turn later conn.transaction() into a nested savepoint whose
        # outer rollback on close would discard DDL/role work.
        conn.autocommit = True
        return conn
    except Exception:
        _raise_public(FailureCode.CONNECTION)


def _connection_from(
    *,
    dsn: Optional[str] = None,
    connection: Any = None,
):
    if connection is not None and dsn is not None:
        raise ManagedDatabaseError(FailureCode.INTERNAL, "pass dsn or connection")
    if connection is not None:
        return connection, False
    if not dsn or not str(dsn).strip():
        raise ManagedDatabaseError(FailureCode.CONNECTION, "explicit dsn required")
    return _connect(str(dsn).strip()), True


def _reject_active_caller_transaction(conn, *, owned: bool) -> None:
    """Require provided connections to be idle and autocommit-enabled.

    Never rolls back caller-owned work. DSN-owned connections are opened in
    autocommit and are idle at entry.
    """
    if owned:
        return
    try:
        status = conn.info.transaction_status
    except Exception:
        _raise_public(FailureCode.CONNECTION)
    # 0 == IDLE in libpq / psycopg TransactionStatus
    if int(status) != 0:
        raise ManagedDatabaseError(
            FailureCode.ACTIVE_TRANSACTION,
            "provided connection has an active transaction",
        )
    # A read probe on a non-autocommit connection would open an outer implicit
    # transaction. Later conn.transaction() would only release a SAVEPOINT,
    # reporting success before DDL/ledger are committed. Require the same
    # transaction ownership contract as DSN-owned connections, without changing
    # caller configuration or committing/rolling back caller-owned work.
    if not conn.autocommit:
        raise ManagedDatabaseError(
            FailureCode.CONNECTION,
            "provided connection requires autocommit",
        )


def _read_sql_support(name: str) -> str:
    path = _SQL_SUPPORT_DIR / name
    return path.read_text(encoding="utf-8")


def _assert_no_public_create(conn) -> None:
    has_create = conn.execute(
        "SELECT has_schema_privilege('public', 'public', 'CREATE')"
    ).fetchone()[0]
    if has_create:
        raise ManagedDatabaseError(FailureCode.PUBLIC_CREATE_PRIVILEGE)


def _role_exists(conn, role_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
            (role_name,),
        ).fetchone()[0]
    )


def _schema_exists(conn, schema_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
            (schema_name,),
        ).fetchone()[0]
    )


def _schema_owner(conn, schema_name: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT r.rolname
        FROM pg_namespace n
        JOIN pg_roles r ON r.oid = n.nspowner
        WHERE n.nspname = %s
        """,
        (schema_name,),
    ).fetchone()
    return None if row is None else row[0]


def provision_app(
    *,
    namespace: str,
    repository_id: str,
    project_ref: str,
    migrator_password: str,
    runtime_password: str,
    provisioner_dsn: Optional[str] = None,
    provisioner_connection: Any = None,
) -> ProvisionResult:
    """Create or verify an owned app schema + per-app control schema.

    Must run as a NOSUPERUSER CREATEROLE login with CREATE on the database.
    Never resets existing role passwords. Never adopts unmarked schemas/roles.
    Provided connections must be idle and autocommit-enabled; rejection never
    rolls back caller-owned work or changes connection configuration.
    """
    namespace = validate_namespace(namespace)
    repository_id = validate_repository_id(repository_id)
    project_ref = validate_project_ref(project_ref)
    mig_secret = _require_secret(migrator_password, "migrator_password")
    run_secret = _require_secret(runtime_password, "runtime_password")
    roles = derive_role_names(namespace)

    conn, owned = _connection_from(
        dsn=provisioner_dsn, connection=provisioner_connection
    )
    try:
        _reject_active_caller_transaction(conn, owned=owned)
        return _provision_on_connection(
            conn,
            namespace=namespace,
            repository_id=repository_id,
            project_ref=project_ref,
            migrator_password=mig_secret,
            runtime_password=run_secret,
            roles=roles,
        )
    except ManagedDatabaseError:
        raise
    except Exception:
        _raise_public(FailureCode.INTERNAL)
    finally:
        if owned:
            conn.close()


def _provision_on_connection(
    conn,
    *,
    namespace: str,
    repository_id: str,
    project_ref: str,
    migrator_password: _Secret,
    runtime_password: _Secret,
    roles: RoleNames,
) -> ProvisionResult:
    from psycopg import sql as psql

    _assert_no_public_create(conn)

    provisioner = conn.execute("SELECT session_user").fetchone()[0]
    flags = conn.execute(
        """
        SELECT rolsuper, rolcreaterole, rolbypassrls
        FROM pg_roles WHERE rolname = session_user
        """
    ).fetchone()
    if flags is None or flags[0] or not flags[1] or flags[2]:
        raise ManagedDatabaseError(
            FailureCode.AUTH_DENIED,
            "provisioner must be NOSUPERUSER CREATEROLE NOBYPASSRLS",
        )
    if not conn.execute(
        "SELECT has_database_privilege(session_user, current_database(), 'CREATE')"
    ).fetchone()[0]:
        raise ManagedDatabaseError(
            FailureCode.AUTH_DENIED, "provisioner needs CREATE on database"
        )

    control_present = _schema_exists(conn, roles.control_schema)
    data_present = _schema_exists(conn, namespace)
    owner_present = _role_exists(conn, roles.owner)
    migrator_present = _role_exists(conn, roles.migrator)
    runtime_present = _role_exists(conn, roles.runtime)

    if control_present:
        owner_of_control = _schema_owner(conn, roles.control_schema)
        if owner_of_control != provisioner:
            raise ManagedDatabaseError(FailureCode.CONTROL_COLLISION)
        if not data_present:
            raise ManagedDatabaseError(
                FailureCode.OWNERSHIP_MISMATCH,
                "data schema missing while control binding remains",
            )
        if _schema_owner(conn, namespace) != roles.owner:
            raise ManagedDatabaseError(FailureCode.OWNERSHIP_MISMATCH)
        binding = conn.execute(
            psql.SQL(
                """
                SELECT namespace, repository_id, project_ref,
                       owner_role, migrator_role, runtime_role, provisioner_role
                FROM {}.ownership_binding
                """
            ).format(psql.Identifier(roles.control_schema))
        ).fetchone()
        if binding is None:
            raise ManagedDatabaseError(FailureCode.CONTROL_COLLISION)
        if (
            binding[0] != namespace
            or binding[1] != repository_id
            or binding[2] != project_ref
            or binding[3] != roles.owner
            or binding[4] != roles.migrator
            or binding[5] != roles.runtime
            or binding[6] != provisioner
        ):
            raise ManagedDatabaseError(FailureCode.OWNERSHIP_MISMATCH)
        _verify_role_invariants(conn, roles, provisioner=provisioner)
        _verify_schema_acl_invariants(conn, namespace=namespace, roles=roles)
        return ProvisionResult(
            status="ok",
            namespace=namespace,
            repository_id=repository_id,
            project_ref=project_ref,
            roles=roles,
            created=False,
        )

    # Fail closed on unmarked collisions before creating anything.
    if data_present:
        raise ManagedDatabaseError(FailureCode.SCHEMA_COLLISION)
    for role_name, present in (
        (roles.owner, owner_present),
        (roles.migrator, migrator_present),
        (roles.runtime, runtime_present),
    ):
        if present:
            raise ManagedDatabaseError(FailureCode.ROLE_COLLISION, role_name)

    with conn.transaction():
        conn.execute(
            psql.SQL(
                """
                CREATE ROLE {} NOLOGIN
                  NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION
                """
            ).format(psql.Identifier(roles.owner))
        )
        conn.execute(
            """
            CREATE OR REPLACE FUNCTION pg_temp.md_create_login(role_name text, pw text)
            RETURNS void
            LANGUAGE plpgsql
            SECURITY INVOKER
            SET search_path = pg_catalog, pg_temp
            AS $fn$
            BEGIN
              EXECUTE format(
                'CREATE ROLE %I LOGIN PASSWORD %L
                   NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION',
                role_name, pw
              );
            END;
            $fn$;
            """
        )
        conn.execute(
            "SELECT pg_temp.md_create_login(%s, %s)",
            (roles.migrator, migrator_password.value),
        )
        conn.execute(
            "SELECT pg_temp.md_create_login(%s, %s)",
            (roles.runtime, runtime_password.value),
        )
        # PG16+/17: CREATEROLE creator gets ADMIN without SET; SET is required
        # for CREATE SCHEMA AUTHORIZATION.
        conn.execute(
            psql.SQL(
                "GRANT {} TO CURRENT_USER WITH SET TRUE, INHERIT FALSE"
            ).format(psql.Identifier(roles.owner))
        )
        conn.execute(
            psql.SQL(
                "GRANT {} TO {} WITH INHERIT TRUE, SET TRUE, ADMIN FALSE"
            ).format(
                psql.Identifier(roles.owner),
                psql.Identifier(roles.migrator),
            )
        )
        conn.execute(
            psql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                psql.Identifier(namespace),
                psql.Identifier(roles.owner),
            )
        )
        conn.execute(
            psql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(
                psql.Identifier(roles.control_schema)
            )
        )
        # After AUTHORIZATION owner, only the owner (or superuser) can grant on
        # the data schema. Provisioner holds SET on owner for this purpose.
        conn.execute(
            psql.SQL("SET ROLE {}").format(psql.Identifier(roles.owner))
        )
        conn.execute(
            psql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                psql.Identifier(namespace)
            )
        )
        conn.execute(
            psql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(
                psql.Identifier(namespace),
                psql.Identifier(roles.migrator),
            )
        )
        conn.execute(
            psql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                psql.Identifier(namespace),
                psql.Identifier(roles.runtime),
            )
        )
        conn.execute("RESET ROLE")
        conn.execute(
            psql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                psql.Identifier(roles.control_schema)
            )
        )
        conn.execute(
            psql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                psql.Identifier(roles.control_schema),
                psql.Identifier(roles.migrator),
            )
        )
        conn.execute(
            psql.SQL("ALTER ROLE {} SET search_path TO {}, pg_temp").format(
                psql.Identifier(roles.migrator),
                psql.Identifier(namespace),
            )
        )
        conn.execute(
            psql.SQL("ALTER ROLE {} SET search_path TO {}, pg_temp").format(
                psql.Identifier(roles.runtime),
                psql.Identifier(namespace),
            )
        )

        control_sql = (
            _read_sql_support("control_objects.sql")
            .replace("__CONTROL_SCHEMA__", roles.control_schema)
            .replace("__DATA_SCHEMA__", namespace)
        )
        for statement in split_sql_statements(control_sql):
            conn.execute(statement)

        conn.execute(
            psql.SQL(
                """
                INSERT INTO {}.ownership_binding (
                  namespace, repository_id, project_ref,
                  owner_role, migrator_role, runtime_role, provisioner_role
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
            ).format(psql.Identifier(roles.control_schema)),
            (
                namespace,
                repository_id,
                project_ref,
                roles.owner,
                roles.migrator,
                roles.runtime,
                provisioner,
            ),
        )
        conn.execute(
            psql.SQL(
                "GRANT SELECT ON {}.ownership_binding TO {}"
            ).format(
                psql.Identifier(roles.control_schema),
                psql.Identifier(roles.migrator),
            )
        )
        conn.execute(
            psql.SQL("GRANT SELECT ON {}.ledger TO {}").format(
                psql.Identifier(roles.control_schema),
                psql.Identifier(roles.migrator),
            )
        )
        conn.execute(
            psql.SQL(
                "GRANT EXECUTE ON FUNCTION {}.record_applied(text, text) TO {}"
            ).format(
                psql.Identifier(roles.control_schema),
                psql.Identifier(roles.migrator),
            )
        )
        conn.execute(
            psql.SQL(
                "GRANT EXECUTE ON FUNCTION {}.migrator_is_registered() TO {}"
            ).format(
                psql.Identifier(roles.control_schema),
                psql.Identifier(roles.migrator),
            )
        )

    return ProvisionResult(
        status="ok",
        namespace=namespace,
        repository_id=repository_id,
        project_ref=project_ref,
        roles=roles,
        created=True,
    )


def _membership_edges(conn) -> List[Tuple[str, str, bool, bool, bool]]:
    """Return effective membership edges aggregated across grantors.

    PostgreSQL unique-indexes (roleid, member, grantor), so CREATEROLE's
    automatic ADMIN grant and a later self-GRANT WITH SET TRUE can coexist as
    two rows. Callers must see the OR of ADMIN/INHERIT/SET options.
    """
    return conn.execute(
        """
        SELECT role.rolname AS granted_role,
               member.rolname AS member_role,
               bool_or(m.admin_option) AS admin_option,
               bool_or(m.inherit_option) AS inherit_option,
               bool_or(m.set_option) AS set_option
        FROM pg_auth_members m
        JOIN pg_roles role ON role.oid = m.roleid
        JOIN pg_roles member ON member.oid = m.member
        GROUP BY role.rolname, member.rolname
        """
    ).fetchall()


def _membership_closure(edges: Sequence[Tuple[str, str, bool, bool, bool]], start: str) -> set[str]:
    """Roles reachable by walking granted_role <- member_role from start."""
    by_member: dict[str, List[str]] = {}
    for granted, member, _a, _i, _s in edges:
        by_member.setdefault(member, []).append(granted)
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for granted in by_member.get(current, []):
            if granted not in seen:
                seen.add(granted)
                stack.append(granted)
    return seen


def _verify_role_invariants(conn, roles: RoleNames, *, provisioner: str) -> None:
    rows = conn.execute(
        """
        SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
               rolreplication, rolcanlogin
        FROM pg_roles
        WHERE rolname = ANY(%s)
        """,
        ([roles.owner, roles.migrator, roles.runtime],),
    ).fetchall()
    by_name = {r[0]: r for r in rows}
    for name in (roles.owner, roles.migrator, roles.runtime):
        if name not in by_name:
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        _rolname, super_, bypass, createrole, createdb, repl, canlogin = by_name[name]
        if super_ or bypass or createrole or createdb or repl:
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if name == roles.owner and canlogin:
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if name in (roles.migrator, roles.runtime) and not canlogin:
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)

    edges = _membership_edges(conn)
    app_roles = {roles.owner, roles.migrator, roles.runtime}
    allowed_member_of = {
        # member -> granted
        (roles.migrator, roles.owner),
        (provisioner, roles.owner),
        (provisioner, roles.migrator),
        (provisioner, roles.runtime),
    }

    for granted, member, admin, inherit, set_opt in edges:
        if member not in app_roles and granted not in app_roles:
            continue
        if (member, granted) not in allowed_member_of:
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if member == roles.migrator and granted == roles.owner:
            if admin or (not inherit) or (not set_opt):
                raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if member == provisioner and granted == roles.owner:
            # Effective options across grantors: ADMIN from CREATEROLE row,
            # SET from self-GRANT WITH SET TRUE (may be a separate grantor row).
            if (not admin) or inherit or (not set_opt):
                raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if member == provisioner and granted in (roles.migrator, roles.runtime):
            # CREATEROLE default: ADMIN TRUE, INHERIT FALSE, SET FALSE.
            if (not admin) or inherit or set_opt:
                raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)

    runtime_closure = _membership_closure(edges, roles.runtime)
    if runtime_closure != {roles.runtime}:
        raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
    owner_closure = _membership_closure(edges, roles.owner)
    if owner_closure != {roles.owner}:
        raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
    migrator_closure = _membership_closure(edges, roles.migrator)
    if migrator_closure != {roles.migrator, roles.owner}:
        raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)

    for role_name in app_roles:
        closure = _membership_closure(edges, role_name)
        if closure & PRIVILEGED_GROUP_ROLES:
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if any(name.startswith("pg_") for name in closure if name != role_name):
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)


def _verify_schema_acl_invariants(
    conn, *, namespace: str, roles: RoleNames
) -> None:
    """Reject unauthorized schema ACLs including runtime CREATE."""
    provisioner_row = conn.execute(
        """
        SELECT r.rolname
        FROM pg_namespace n
        JOIN pg_roles r ON r.oid = n.nspowner
        WHERE n.nspname = %s
        """,
        (roles.control_schema,),
    ).fetchone()
    if provisioner_row is None:
        raise ManagedDatabaseError(FailureCode.OWNERSHIP_MISMATCH)
    provisioner = provisioner_row[0]

    rows = conn.execute(
        """
        SELECT n.nspname,
               CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE gr.rolname END AS grantee,
               x.privilege_type
        FROM pg_namespace n
        CROSS JOIN LATERAL aclexplode(
          COALESCE(n.nspacl, acldefault('n', n.nspowner))
        ) AS x
        LEFT JOIN pg_roles gr ON gr.oid = x.grantee
        WHERE n.nspname = ANY(%s)
        """,
        ([namespace, roles.control_schema],),
    ).fetchall()

    for schema_name, grantee, privilege in rows:
        if grantee in ("PUBLIC", "anon", "authenticated"):
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if schema_name == namespace:
            if grantee == roles.owner:
                continue
            if grantee == roles.migrator and privilege in ("USAGE", "CREATE"):
                continue
            if grantee == roles.runtime and privilege == "USAGE":
                continue
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)
        if schema_name == roles.control_schema:
            if grantee == provisioner:
                continue
            if grantee == roles.migrator and privilege == "USAGE":
                continue
            raise ManagedDatabaseError(FailureCode.PRIVILEGE_DRIFT)


def migrate_app(
    *,
    namespace: str,
    repository_id: str,
    migrations_dir: Path,
    migrator_dsn: Optional[str] = None,
    migrator_connection: Any = None,
) -> MigrateResult:
    """Apply reviewed migrations as the restricted migrator LOGIN.

    Safety invariants (role graph, schema ACLs, table/column ACLs, RLS, and
    app-routine privilege boundaries) run inside the DDL+ledger transaction
    after migration SQL and before COMMIT so violations roll back newly
    applied work atomically. Replay with no new migrations still re-checks.
    """
    namespace = validate_namespace(namespace)
    repository_id = validate_repository_id(repository_id)
    roles = derive_role_names(namespace)
    migrations = discover_migrations(Path(migrations_dir))
    key1, key2 = advisory_lock_keys(namespace)

    conn, owned = _connection_from(dsn=migrator_dsn, connection=migrator_connection)
    try:
        _reject_active_caller_transaction(conn, owned=owned)
        session_user = conn.execute("SELECT session_user").fetchone()[0]
        if session_user != roles.migrator:
            raise ManagedDatabaseError(FailureCode.AUTH_DENIED)

        applied = 0
        skipped = 0
        last_error: Optional[BaseException] = None
        for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
            try:
                with conn.transaction():
                    locked = conn.execute(
                        "SELECT pg_try_advisory_xact_lock(%s, %s)",
                        (key1, key2),
                    ).fetchone()[0]
                    if not locked:
                        raise LockBusy()

                    provisioner = _assert_migrator_binding(
                        conn,
                        roles=roles,
                        namespace=namespace,
                        repository_id=repository_id,
                    )
                    _verify_role_invariants(conn, roles, provisioner=provisioner)
                    _verify_schema_acl_invariants(
                        conn, namespace=namespace, roles=roles
                    )
                    from psycopg import sql as psql

                    conn.execute(
                        psql.SQL("SET LOCAL search_path TO {}, pg_temp").format(
                            psql.Identifier(namespace)
                        )
                    )
                    # Recreate so a reused migrator_connection can replay safely.
                    conn.execute(
                        """
                        CREATE OR REPLACE FUNCTION pg_temp.md_exec_migration(p_sql text)
                        RETURNS void
                        LANGUAGE plpgsql
                        SECURITY INVOKER
                        SET search_path FROM CURRENT
                        AS $fn$
                        BEGIN
                          EXECUTE p_sql;
                        END;
                        $fn$;
                        """
                    )

                    max_applied = conn.execute(
                        psql.SQL(
                            "SELECT max(migration_id) FROM {}.ledger"
                        ).format(psql.Identifier(roles.control_schema))
                    ).fetchone()[0]

                    for migration in migrations:
                        row = conn.execute(
                            psql.SQL(
                                """
                                SELECT checksum_sha256
                                FROM {}.ledger
                                WHERE migration_id = %s
                                """
                            ).format(psql.Identifier(roles.control_schema)),
                            (migration.migration_id,),
                        ).fetchone()
                        if row is not None:
                            if row[0] != migration.checksum_sha256:
                                raise ManagedDatabaseError(
                                    FailureCode.MIGRATION_CHECKSUM,
                                    migration.migration_id,
                                )
                            skipped += 1
                            continue
                        if (
                            max_applied is not None
                            and migration.migration_id < max_applied
                        ):
                            raise ManagedDatabaseError(
                                FailureCode.MIGRATION_ORDER,
                                migration.migration_id,
                            )

                        _execute_migration_bytes(conn, migration.sql_bytes)
                        conn.execute(
                            psql.SQL(
                                "SELECT {}.record_applied(%s, %s)"
                            ).format(psql.Identifier(roles.control_schema)),
                            (
                                migration.migration_id,
                                migration.checksum_sha256,
                            ),
                        )
                        applied += 1
                        max_applied = migration.migration_id

                    # Atomic safety gate: violations abort DDL+ledger together.
                    _check_invariants(conn, namespace=namespace, roles=roles)
                return MigrateResult(
                    status="ok", applied_count=applied, skipped_count=skipped
                )
            except LockBusy as exc:
                last_error = exc
                applied = 0
                skipped = 0
                time.sleep(LOCK_RETRY_SLEEP_SECONDS * attempt)
                continue
            except ManagedDatabaseError:
                raise
            except Exception:
                _raise_public(FailureCode.MIGRATION_EXEC)

        raise ManagedDatabaseError(
            FailureCode.MIGRATION_LOCK, "lock retries exhausted"
        )
    except ManagedDatabaseError:
        raise
    except Exception:
        _raise_public(FailureCode.MIGRATION_EXEC)
    finally:
        if owned:
            conn.close()


def _assert_migrator_binding(
    conn,
    *,
    roles: RoleNames,
    namespace: str,
    repository_id: str,
) -> str:
    from psycopg import sql as psql

    row = conn.execute(
        psql.SQL(
            """
            SELECT namespace, repository_id, migrator_role, provisioner_role
            FROM {}.ownership_binding
            """
        ).format(psql.Identifier(roles.control_schema))
    ).fetchone()
    if (
        row is None
        or row[0] != namespace
        or row[1] != repository_id
        or row[2] != roles.migrator
    ):
        raise ManagedDatabaseError(FailureCode.OWNERSHIP_MISMATCH)
    return row[3]


def _execute_migration_bytes(conn, sql_bytes: bytes) -> None:
    """Execute reviewed migration SQL via one PostgreSQL-bound EXECUTE.

    PostgreSQL 17 parses the full text inside the SECURITY INVOKER wrapper;
    transaction-control statements raise FeatureNotSupported / invalid
    transaction termination and roll back with the surrounding transaction.
    """
    try:
        text = sql_bytes.decode("utf-8")
    except UnicodeDecodeError:
        _raise_public(FailureCode.MIGRATION_EXEC)
    try:
        conn.execute(
            "SELECT pg_temp.md_exec_migration(%s)",
            (text,),
        )
    except ManagedDatabaseError:
        raise
    except Exception:
        _raise_public(FailureCode.MIGRATION_EXEC)


def verify_post_migration_invariants(
    *,
    namespace: str,
    provisioner_dsn: Optional[str] = None,
    provisioner_connection: Any = None,
) -> None:
    """Fail closed on post-migration catalog invariants (incl. routines/ACLs).

    Always rolls the probe transaction back so checks leave no side effects.
    Violations raise ManagedDatabaseError (`PRIVILEGE_DRIFT` or
    `INVARIANT_VIOLATION`). Provided connections must be idle and
    autocommit-enabled at entry; caller-owned work is never rolled back to
    make room for the probe.
    """
    namespace = validate_namespace(namespace)
    roles = derive_role_names(namespace)
    conn, owned = _connection_from(
        dsn=provisioner_dsn, connection=provisioner_connection
    )
    try:
        _reject_active_caller_transaction(conn, owned=owned)
        conn.execute("BEGIN")
        try:
            _check_invariants(conn, namespace=namespace, roles=roles)
        except ManagedDatabaseError:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            _raise_public(FailureCode.INVARIANT_VIOLATION)
        else:
            conn.rollback()
    except ManagedDatabaseError:
        raise
    except Exception:
        _raise_public(FailureCode.INVARIANT_VIOLATION)
    finally:
        if owned:
            conn.close()


def _acl_grantee_name(oid: int, role_name: Optional[str]) -> str:
    if oid == 0:
        return "PUBLIC"
    return role_name or f"oid:{oid}"


def _check_invariants(conn, *, namespace: str, roles: RoleNames) -> None:
    from psycopg import sql as psql

    # Revalidate role graph + schema ACLs after migration SQL (not only before).
    # Schema grants such as CREATE TO PUBLIC/authenticated must not commit.
    provisioner_row = conn.execute(
        psql.SQL(
            "SELECT provisioner_role FROM {}.ownership_binding"
        ).format(psql.Identifier(roles.control_schema))
    ).fetchone()
    if provisioner_row is None:
        raise ManagedDatabaseError(FailureCode.OWNERSHIP_MISMATCH)
    _verify_role_invariants(conn, roles, provisioner=provisioner_row[0])
    _verify_schema_acl_invariants(conn, namespace=namespace, roles=roles)

    tables = conn.execute(
        """
        SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
               c.relkind, r.rolname AS owner_name, c.oid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles r ON r.oid = c.relowner
        WHERE n.nspname = ANY(%s)
          AND c.relkind IN ('r', 'p')
        """,
        ([namespace, roles.control_schema],),
    ).fetchall()
    if not tables:
        raise ManagedDatabaseError(FailureCode.INVARIANT_VIOLATION, "no tables")
    for schema_name, table_name, rls, force, relkind, owner_name, _oid in tables:
        if not rls or not force:
            raise ManagedDatabaseError(
                FailureCode.INVARIANT_VIOLATION,
                f"rls required for {schema_name}.{table_name}",
            )
        if schema_name == namespace and owner_name == roles.runtime:
            raise ManagedDatabaseError(
                FailureCode.INVARIANT_VIOLATION, "runtime owns table"
            )

    # Catalog ACL inspection (includes PUBLIC / column ACLs / partitioned parents).
    table_acls = conn.execute(
        """
        SELECT n.nspname, c.relname,
               x.grantee,
               gr.rolname AS grantee_name,
               x.privilege_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS x
        LEFT JOIN pg_roles gr ON gr.oid = x.grantee
        WHERE n.nspname = ANY(%s)
          AND c.relkind IN ('r', 'p')
        """,
        ([namespace, roles.control_schema],),
    ).fetchall()
    for schema_name, table_name, grantee_oid, grantee_name, priv in table_acls:
        grantee = _acl_grantee_name(grantee_oid, grantee_name)
        letter = {
            "SELECT": "r",
            "INSERT": "a",
            "UPDATE": "w",
            "DELETE": "d",
            "TRUNCATE": "D",
            "REFERENCES": "x",
            "TRIGGER": "t",
            "MAINTAIN": "m",
        }.get(priv)
        if letter is None:
            continue
        if grantee in ("PUBLIC", "anon", "authenticated"):
            if letter in _FORBIDDEN_PUBLIC_TABLE_PRIVS:
                raise ManagedDatabaseError(
                    FailureCode.INVARIANT_VIOLATION,
                    "accidental public/anon grants",
                )
        if grantee == roles.runtime:
            if letter not in _RUNTIME_ALLOWED_TABLE_PRIVS:
                raise ManagedDatabaseError(
                    FailureCode.INVARIANT_VIOLATION,
                    "runtime unsafe table grant",
                )

    column_acls = conn.execute(
        """
        SELECT n.nspname, c.relname, a.attname,
               x.grantee,
               gr.rolname AS grantee_name,
               x.privilege_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        CROSS JOIN LATERAL aclexplode(a.attacl) AS x
        LEFT JOIN pg_roles gr ON gr.oid = x.grantee
        WHERE n.nspname = ANY(%s)
          AND c.relkind IN ('r', 'p')
          AND a.attacl IS NOT NULL
        """,
        ([namespace, roles.control_schema],),
    ).fetchall()
    for (
        schema_name,
        table_name,
        _col,
        grantee_oid,
        grantee_name,
        priv,
    ) in column_acls:
        grantee = _acl_grantee_name(grantee_oid, grantee_name)
        letter = {
            "SELECT": "r",
            "INSERT": "a",
            "UPDATE": "w",
            "REFERENCES": "x",
        }.get(priv)
        if letter is None:
            continue
        if grantee in ("PUBLIC", "anon", "authenticated"):
            if letter in _FORBIDDEN_PUBLIC_COLUMN_PRIVS:
                raise ManagedDatabaseError(
                    FailureCode.INVARIANT_VIOLATION,
                    "accidental public/anon column grants",
                )
        if grantee == roles.runtime and letter not in _RUNTIME_ALLOWED_COLUMN_PRIVS:
            raise ManagedDatabaseError(
                FailureCode.INVARIANT_VIOLATION,
                "runtime unsafe column grant",
            )

    runtime_flags = conn.execute(
        """
        SELECT rolsuper, rolbypassrls, rolcreaterole
        FROM pg_roles WHERE rolname = %s
        """,
        (roles.runtime,),
    ).fetchone()
    if runtime_flags is None or any(runtime_flags):
        raise ManagedDatabaseError(FailureCode.INVARIANT_VIOLATION, "runtime flags")

    # Runtime must not have DDL-ish privileges on the app schema beyond USAGE.
    if conn.execute(
        "SELECT has_schema_privilege(%s, %s, 'CREATE')",
        (roles.runtime, namespace),
    ).fetchone()[0]:
        raise ManagedDatabaseError(
            FailureCode.INVARIANT_VIOLATION, "runtime schema CREATE"
        )

    _check_app_routine_invariants(conn, namespace=namespace, roles=roles)

    # Touch control ledger via identifier so search_path cannot redirect.
    conn.execute(
        psql.SQL("SELECT count(*) FROM {}.ledger").format(
            psql.Identifier(roles.control_schema)
        )
    )


def _check_app_routine_invariants(
    conn, *, namespace: str, roles: RoleNames
) -> None:
    """Reject unsafe app-schema routines; leave control-schema helpers alone.

    Core baseline: every SECURITY DEFINER function/procedure in the app schema
    is rejected (including trigger functions). Invoker routines must be owned by
    the app owner or migrator, and EXECUTE must not default to PUBLIC or reach
    anon/authenticated/unrelated roles — only deliberate grants to approved app
    roles. Privileged app routines need a future separately reviewed allowlist.
    """
    approved_execute = {roles.owner, roles.migrator, roles.runtime}
    approved_owners = {roles.owner, roles.migrator}

    routines = conn.execute(
        """
        SELECT p.oid, p.prosecdef, owner.rolname
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_roles owner ON owner.oid = p.proowner
        WHERE n.nspname = %s
        """,
        (namespace,),
    ).fetchall()
    for oid, is_definer, owner_name in routines:
        if is_definer:
            raise ManagedDatabaseError(
                FailureCode.INVARIANT_VIOLATION,
                "app security definer routine",
            )
        if owner_name not in approved_owners:
            raise ManagedDatabaseError(
                FailureCode.INVARIANT_VIOLATION,
                "app routine ownership",
            )
        grants = conn.execute(
            """
            SELECT CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE gr.rolname END,
                   x.privilege_type
            FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(
              COALESCE(p.proacl, acldefault('f', p.proowner))
            ) AS x
            LEFT JOIN pg_roles gr ON gr.oid = x.grantee
            WHERE p.oid = %s
            """,
            (oid,),
        ).fetchall()
        for grantee, privilege in grants:
            if privilege != "EXECUTE":
                continue
            if grantee in ("PUBLIC", "anon", "authenticated"):
                raise ManagedDatabaseError(
                    FailureCode.INVARIANT_VIOLATION,
                    "app routine execute grant",
                )
            if grantee not in approved_execute:
                raise ManagedDatabaseError(
                    FailureCode.INVARIANT_VIOLATION,
                    "app routine execute grant",
                )


def readiness_metadata(
    *,
    namespace: str,
    repository_id: str,
    provisioner_dsn: Optional[str] = None,
    provisioner_connection: Any = None,
) -> dict:
    """Narrow non-secret ownership/version facts for a later readiness route."""
    namespace = validate_namespace(namespace)
    repository_id = validate_repository_id(repository_id)
    roles = derive_role_names(namespace)
    conn, owned = _connection_from(
        dsn=provisioner_dsn, connection=provisioner_connection
    )
    try:
        _reject_active_caller_transaction(conn, owned=owned)
        from psycopg import sql as psql

        binding = conn.execute(
            psql.SQL(
                """
                SELECT namespace, repository_id, project_ref
                FROM {}.ownership_binding
                """
            ).format(psql.Identifier(roles.control_schema))
        ).fetchone()
        if binding is None or binding[1] != repository_id:
            raise ManagedDatabaseError(FailureCode.OWNERSHIP_MISMATCH)
        ledger = conn.execute(
            psql.SQL(
                """
                SELECT migration_id, checksum_sha256
                FROM {}.ledger
                ORDER BY migration_id
                """
            ).format(psql.Identifier(roles.control_schema))
        ).fetchall()
        return {
            "namespace": binding[0],
            "repository_id": binding[1],
            "project_ref": binding[2],
            "migrations": [
                {"migration_id": mid, "checksum_sha256": checksum}
                for mid, checksum in ledger
            ],
        }
    except ManagedDatabaseError:
        raise
    except Exception:
        _raise_public(FailureCode.INTERNAL)
    finally:
        if owned:
            conn.close()

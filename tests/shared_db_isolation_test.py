#!/usr/bin/env python3
"""APP-003 shared-database migration/access isolation tests.

Unit checks always run. PostgreSQL integration runs only when a disposable
local server is available (podman + postgres image) and APP003_PG_TESTS is
not set to 0. Integration never reads DATABASE_URL / SUPABASE_DB_URL.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = REPO_ROOT / "db" / "shared_isolation"
TEST_BASELINE_SQL = DB_ROOT / "provision" / "00_disposable_test_baseline.sql"
PROVISION_SQL = DB_ROOT / "provision" / "01_control_and_roles.sql"
PREEXISTING_SQL = DB_ROOT / "fixtures" / "preexisting" / "01_seed.sql"
ALPHA_DIR = DB_ROOT / "fixtures" / "app_alpha"
BETA_DIR = DB_ROOT / "fixtures" / "app_beta"

PG_IMAGE = os.environ.get("APP003_PG_IMAGE", "docker.io/library/postgres:16-alpine")
RUN_PG = os.environ.get("APP003_PG_TESTS", "auto")


def _load_harness():
    import sys

    path = REPO_ROOT / "scripts" / "migrate_app_namespace.py"
    spec = importlib.util.spec_from_file_location("migrate_app_namespace", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load harness from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _podman_available() -> bool:
    try:
        subprocess.run(
            ["podman", "image", "exists", PG_IMAGE],
            check=True,
            capture_output=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            subprocess.run(
                ["podman", "pull", PG_IMAGE],
                check=True,
                capture_output=True,
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False


def _pg_tests_enabled() -> bool:
    if RUN_PG == "0":
        return False
    if RUN_PG == "1":
        return True
    return _podman_available()


class HarnessUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = _load_harness()

    def test_refuses_inherited_database_url_env(self):
        os.environ["DATABASE_URL"] = "postgresql://should-not-be-used/db"
        os.environ["SUPABASE_DB_URL"] = "postgresql://also-not-used/db"
        try:
            with self.assertRaises(SystemExit):
                self.harness.resolve_database_url(None)
            url = self.harness.resolve_database_url(
                "postgresql://explicit:local@127.0.0.1:1/db"
            )
            self.assertIn("explicit", url)
            self.assertNotIn("should-not-be-used", url)
        finally:
            os.environ.pop("DATABASE_URL", None)
            os.environ.pop("SUPABASE_DB_URL", None)

    def test_migration_discovery_reads_file_once_into_immutable_bytes(self):
        migrations = self.harness.discover_migrations(ALPHA_DIR)
        self.assertGreaterEqual(len(migrations), 2)
        ids = [m.migration_id for m in migrations]
        self.assertEqual(ids, sorted(ids))
        for migration in migrations:
            self.assertEqual(
                migration.checksum_sha256,
                hashlib.sha256(migration.sql_bytes).hexdigest(),
            )
            self.assertEqual(migration.sql_bytes, migration.path.read_bytes())
            self.assertRegex(migration.migration_id, r"^[0-9]{4}_[a-z0-9_]+$")
            with self.assertRaises(Exception):
                migration.sql_bytes = b"mutated"  # type: ignore[misc]

    def test_harness_does_not_regex_sanitize_sql(self):
        source = (REPO_ROOT / "scripts" / "migrate_app_namespace.py").read_text(
            encoding="utf-8"
        )
        lowered = source.lower()
        self.assertNotIn("sanitize_sql", lowered)
        self.assertNotIn("strip_dangerous", lowered)
        self.assertIn("advisory_xact_lock", lowered)
        self.assertIn("checksum", lowered)
        self.assertIn("transaction-control", lowered)

    def test_reserved_namespaces_and_length_rejected(self):
        for name in ("public", "shared", "auth", "supabase_migrations", "app_migration_control"):
            with self.assertRaises(ValueError):
                self.harness.validate_namespace(name)
        too_long = "a" * (self.harness.MAX_NAMESPACE_LEN + 1)
        with self.assertRaises(ValueError):
            self.harness.validate_namespace(too_long)
        # 54-char namespace + "_migrator" == 63; must still be accepted
        ok = "a" * self.harness.MAX_NAMESPACE_LEN
        self.assertEqual(self.harness.validate_namespace(ok), ok)

    def test_namespace_suffix_truncation_collision_rejected(self):
        # 55+ char namespaces truncate "_migrator" inside NAMEDATALEN; reject them.
        over = "n" * (self.harness.MAX_NAMESPACE_LEN + 1)
        with self.assertRaises(ValueError):
            self.harness.validate_namespace(over)
        truncated_role = (over + "_migrator")[:63]
        self.assertEqual(len(truncated_role), 63)
        self.assertFalse(truncated_role.endswith("_migrator"))
        # Distinct over-long names collapse to the same truncated identifier.
        a = "n" * 63 + "aaa"
        b = "n" * 63 + "bbb"
        self.assertEqual((a + "_migrator")[:63], (b + "_migrator")[:63])
        with self.assertRaises(ValueError):
            self.harness.validate_namespace(a)
        with self.assertRaises(ValueError):
            self.harness.validate_namespace(b)

    def test_redact_db_error_strips_secrets_and_sql(self):
        raw = (
            "password=supersecret DETAIL: Key (id)=(1) CONTEXT: SQL statement "
            '"SELECT * FROM secrets WHERE password = \'x\'" '
            "postgresql://user:pass@127.0.0.1/db"
        )
        redacted = self.harness.redact_db_error(raw)
        self.assertNotIn("supersecret", redacted)
        self.assertNotIn("postgresql://user:pass", redacted)
        self.assertIn("[redacted]", redacted)

    def test_cli_requires_protected_url_without_local_gate(self):
        with self.assertRaises(SystemExit):
            self.harness.main(
                [
                    "--database-url",
                    "postgresql://u:p@127.0.0.1:1/db",
                    "--namespace",
                    "app_alpha",
                    "--migrations-dir",
                    str(ALPHA_DIR),
                ]
            )

    def test_cli_accepts_database_url_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("postgresql://explicit:local@127.0.0.1:1/db\n")
            path = Path(handle.name)
        try:
            url = self.harness.load_database_url_from_file(path)
            self.assertIn("explicit", url)
        finally:
            path.unlink(missing_ok=True)

    def test_provision_and_fixture_files_exist(self):
        self.assertTrue(TEST_BASELINE_SQL.is_file())
        self.assertTrue(PROVISION_SQL.is_file())
        self.assertTrue(PREEXISTING_SQL.is_file())
        self.assertTrue(ALPHA_DIR.is_dir())
        self.assertTrue(BETA_DIR.is_dir())
        provision = PROVISION_SQL.read_text(encoding="utf-8")
        self.assertNotRegex(
            provision,
            r"(?m)^\s*REVOKE CREATE ON SCHEMA public FROM PUBLIC\s*;",
        )
        self.assertNotRegex(
            provision,
            r"(?m)^\s*GRANT anon TO authenticator\s*;",
        )
        self.assertIn("realtime_allowlist", provision)
        self.assertIn("registered_apps", provision)
        self.assertIn("operator prerequisite", provision)


@unittest.skipUnless(_pg_tests_enabled(), "disposable local PostgreSQL (podman) unavailable")
class SharedDbIsolationIntegrationTests(unittest.TestCase):
    container_name: str
    port: int
    password: str
    admin_url: str
    harness: object

    @classmethod
    def setUpClass(cls):
        cls.harness = _load_harness()
        cls.password = f"app003-{uuid.uuid4().hex}"
        cls.port = _free_loopback_port()
        cls.container_name = f"app003-iso-{uuid.uuid4().hex[:12]}"
        cls.admin_url = (
            f"postgresql://postgres:{cls.password}@127.0.0.1:{cls.port}/app003"
        )
        run = subprocess.run(
            [
                "podman",
                "run",
                "-d",
                "--name",
                cls.container_name,
                "-p",
                f"127.0.0.1:{cls.port}:5432",
                "-e",
                f"POSTGRES_PASSWORD={cls.password}",
                "-e",
                "POSTGRES_USER=postgres",
                "-e",
                "POSTGRES_DB=app003",
                PG_IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        if run.returncode != 0:
            raise unittest.SkipTest(f"podman run failed: {run.stderr.strip()}")
        try:
            deadline = time.time() + 40
            import psycopg

            while time.time() < deadline:
                try:
                    with psycopg.connect(cls.admin_url, connect_timeout=2) as conn:
                        conn.execute("SELECT 1")
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                raise unittest.SkipTest("postgres did not become ready on loopback")

            cls._bootstrap()
            for namespace, path in (("app_alpha", ALPHA_DIR), ("app_beta", BETA_DIR)):
                result = cls.harness.migrate_app(
                    database_url=cls._migrator_url_static(namespace),
                    namespace=namespace,
                    migrations_dir=path,
                )
                if result.status != "ok":
                    raise RuntimeError(f"initial migrate failed for {namespace}")
        except Exception:
            cls._cleanup_container()
            raise

    @classmethod
    def _cleanup_container(cls):
        subprocess.run(
            ["podman", "rm", "-f", cls.container_name],
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls._cleanup_container()

    @classmethod
    def _migrator_url_static(cls, namespace: str) -> str:
        pw = f"{namespace}-mig-{cls.password[-8:]}"
        return (
            f"postgresql://{namespace}_migrator:{pw}@127.0.0.1:{cls.port}/app003"
        )

    @classmethod
    def _bootstrap(cls):
        import psycopg

        baseline = TEST_BASELINE_SQL.read_text(encoding="utf-8")
        provision = PROVISION_SQL.read_text(encoding="utf-8")
        preexisting = PREEXISTING_SQL.read_text(encoding="utf-8")
        with psycopg.connect(cls.admin_url, autocommit=True) as conn:
            cls.harness._execute_script(conn, baseline.encode("utf-8"))
            cls.harness._execute_script(conn, provision.encode("utf-8"))
            cls.harness._execute_script(conn, preexisting.encode("utf-8"))
            for namespace in ("app_alpha", "app_beta"):
                conn.execute(
                    "SELECT app_migration_control.provision_app(%s, %s)",
                    (namespace, f"{namespace}-mig-{cls.password[-8:]}"),
                )
                conn.execute(
                    "SELECT app_migration_control.allow_realtime_projection(%s, %s)",
                    (namespace, "game_public_events"),
                )

    def _migrator_url(self, namespace: str) -> str:
        pw = f"{namespace}-mig-{self.password[-8:]}"
        return (
            f"postgresql://{namespace}_migrator:{pw}@127.0.0.1:{self.port}/app003"
        )

    def test_migrate_two_apps_and_cross_access_denied(self):
        import psycopg

        with psycopg.connect(self.admin_url) as conn:
            version = conn.execute(
                "SELECT version FROM supabase_migrations.schema_migrations"
            ).fetchone()
            self.assertEqual(version[0], "20240101000001")
            note = conn.execute(
                "SELECT note FROM public.preexisting_shared WHERE id = 1"
            ).fetchone()
            self.assertEqual(note[0], "do-not-touch")

            rows = conn.execute(
                """
                SELECT app_namespace, migration_id, checksum_sha256
                FROM app_migration_control.ledger
                ORDER BY app_namespace, migration_id
                """
            ).fetchall()
            self.assertTrue(any(r[0] == "app_alpha" for r in rows))
            self.assertTrue(any(r[0] == "app_beta" for r in rows))
            for _ns, _mid, checksum in rows:
                self.assertRegex(checksum, r"^[a-f0-9]{64}$")

            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE app_alpha_migrator")
                    conn.execute("SELECT count(*) FROM app_beta.game_private_state")
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE app_alpha_migrator")
                    conn.execute(
                        "INSERT INTO app_beta.game_private_state "
                        "(id, owner_user_id, secret_payload) "
                        "VALUES (gen_random_uuid(), gen_random_uuid(), 'x')"
                    )
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE app_beta_migrator")
                    conn.execute("DROP TABLE app_alpha.game_private_state")

    def test_idempotent_rerun_and_checksum_mismatch(self):
        import psycopg

        first = self.harness.migrate_app(
            database_url=self._migrator_url("app_alpha"),
            namespace="app_alpha",
            migrations_dir=ALPHA_DIR,
        )
        self.assertEqual(first.status, "ok")
        second = self.harness.migrate_app(
            database_url=self._migrator_url("app_alpha"),
            namespace="app_alpha",
            migrations_dir=ALPHA_DIR,
        )
        self.assertEqual(second.status, "ok")
        self.assertEqual(second.applied_count, 0)
        self.assertGreaterEqual(second.skipped_count, 1)

        with psycopg.connect(self.admin_url) as conn:
            conn.execute(
                """
                UPDATE app_migration_control.ledger
                SET checksum_sha256 = repeat('ab', 32)
                WHERE app_namespace = 'app_alpha'
                """
            )
        with self.assertRaises(self.harness.MigrationError):
            self.harness.migrate_app(
                database_url=self._migrator_url("app_alpha"),
                namespace="app_alpha",
                migrations_dir=ALPHA_DIR,
            )
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            for migration in self.harness.discover_migrations(ALPHA_DIR):
                conn.execute(
                    """
                    UPDATE app_migration_control.ledger
                    SET checksum_sha256 = %s
                    WHERE app_namespace = 'app_alpha' AND migration_id = %s
                    """,
                    (migration.checksum_sha256, migration.migration_id),
                )

    def test_ledger_alteration_denied_for_migrator(self):
        import psycopg

        with psycopg.connect(self._migrator_url("app_alpha")) as conn:
            with self.assertRaises(Exception):
                conn.execute(
                    "UPDATE app_migration_control.ledger SET checksum_sha256 = repeat('cd', 32)"
                )
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM app_migration_control.ledger")
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO app_migration_control.ledger "
                    "(app_namespace, migration_id, checksum_sha256) "
                    "VALUES ('app_alpha', '9999_forged', repeat('ef', 32))"
                )

    def test_ledger_guc_spoof_denied(self):
        import psycopg

        with psycopg.connect(self._migrator_url("app_alpha")) as conn:
            conn.execute(
                "SELECT set_config('app.migration_namespace', 'app_beta', false)"
            )
            rows = conn.execute(
                """
                SELECT app_namespace
                FROM app_migration_control.ledger
                """
            ).fetchall()
            namespaces = {r[0] for r in rows}
            self.assertIn("app_alpha", namespaces)
            self.assertNotIn("app_beta", namespaces)

    def test_security_definer_scoped_and_preexisting_owner_allowed(self):
        import psycopg

        with psycopg.connect(self._migrator_url("app_alpha")) as conn:
            with self.assertRaises(Exception):
                conn.execute(
                    """
                    CREATE OR REPLACE FUNCTION public.evil_bypass()
                    RETURNS text
                    LANGUAGE sql
                    SECURITY DEFINER
                    SET search_path = public
                    AS $$ SELECT 'pwned' $$
                    """
                )
            with self.assertRaises(Exception):
                conn.execute("CREATE TABLE public.stolen (id int)")
            with self.assertRaises(Exception):
                conn.execute(
                    """
                    CREATE OR REPLACE FUNCTION app_alpha.sneaky()
                    RETURNS void
                    LANGUAGE plpgsql
                    SECURITY DEFINER
                    SET search_path = public, pg_temp
                    AS $$
                    BEGIN
                      DELETE FROM app_migration_control.ledger;
                    END;
                    $$
                    """
                )

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("CREATE ROLE legacy_preexisting_owner NOLOGIN")
            conn.execute(
                "CREATE SCHEMA legacy_preexisting AUTHORIZATION legacy_preexisting_owner"
            )
            conn.execute("SET ROLE legacy_preexisting_owner")
            conn.execute(
                """
                CREATE FUNCTION legacy_preexisting.approved_definer()
                RETURNS text
                LANGUAGE sql
                SECURITY DEFINER
                SET search_path = legacy_preexisting, pg_temp
                AS $$ SELECT 'ok' $$
                """
            )
            conn.execute("RESET ROLE")
            value = conn.execute(
                "SELECT legacy_preexisting.approved_definer()"
            ).fetchone()[0]
            self.assertEqual(value, "ok")

    def test_public_create_baseline_fail_closed_without_revoke(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
            try:
                with self.assertRaises(Exception) as ctx:
                    conn.execute(
                        "SELECT app_migration_control.provision_app(%s, %s)",
                        ("app_gamma", "gamma-temp-pw"),
                    )
                self.assertIn("operator prerequisite", str(ctx.exception).lower())
            finally:
                conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    def test_role_collision_fail_closed_without_password_reset(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("CREATE ROLE app_delta_migrator LOGIN PASSWORD 'orphan-pw'")
            with self.assertRaises(Exception) as ctx:
                conn.execute(
                    "SELECT app_migration_control.provision_app(%s, %s)",
                    ("app_delta", "should-not-apply"),
                )
            self.assertIn("collision", str(ctx.exception).lower())
            # Orphan password must not have been rewritten to should-not-apply
            bad = (
                f"postgresql://app_delta_migrator:should-not-apply@"
                f"127.0.0.1:{self.port}/app003"
            )
            with self.assertRaises(Exception):
                with psycopg.connect(bad, connect_timeout=3) as _conn:
                    _conn.execute("SELECT 1")

    def test_realtime_allowlist_rejects_private_innocuous_name(self):
        import psycopg

        with psycopg.connect(self._migrator_url("app_alpha")) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS game_leaderboard (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  secret_payload text NOT NULL
                )
                """
            )
            with self.assertRaises(Exception) as ctx:
                conn.execute(
                    "SELECT app_migration_control.add_to_realtime('app_alpha', 'game_leaderboard')"
                )
            self.assertIn("allowlist", str(ctx.exception).lower())
            with self.assertRaises(Exception):
                conn.execute(
                    "SELECT app_migration_control.allow_realtime_projection("
                    "'app_alpha', 'game_leaderboard')"
                )

        with psycopg.connect(self.admin_url) as conn:
            pub_members = {
                tuple(r)
                for r in conn.execute(
                    """
                    SELECT schemaname, tablename
                    FROM pg_publication_tables
                    WHERE pubname = 'supabase_realtime'
                    """
                ).fetchall()
            }
            self.assertNotIn(("app_alpha", "game_leaderboard"), pub_members)
            self.assertNotIn(("app_alpha", "game_private_state"), pub_members)
            self.assertIn(("app_alpha", "game_public_events"), pub_members)

    def test_private_game_not_in_realtime_and_sanitized_projection(self):
        import psycopg

        with psycopg.connect(self.admin_url) as conn:
            pub_members = {
                tuple(r)
                for r in conn.execute(
                    """
                    SELECT schemaname, tablename
                    FROM pg_publication_tables
                    WHERE pubname = 'supabase_realtime'
                    """
                ).fetchall()
            }
            self.assertNotIn(("app_alpha", "game_private_state"), pub_members)
            self.assertNotIn(("app_beta", "game_private_state"), pub_members)

            cols = {
                r[0]
                for r in conn.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'app_alpha'
                      AND table_name = 'game_sanitized_projection'
                    """
                ).fetchall()
            }
            self.assertIn("id", cols)
            self.assertIn("has_secret", cols)
            self.assertNotIn("secret_payload", cols)

            owner = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
            other = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
            with conn.transaction():
                conn.execute("SET LOCAL ROLE app_alpha_migrator")
                conn.execute(
                    """
                    INSERT INTO app_alpha.game_private_state
                      (id, owner_user_id, secret_payload)
                    VALUES (gen_random_uuid(), %s, 'top-secret')
                    """,
                    (owner,),
                )

            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE authenticated")
                    conn.execute(
                        "SELECT set_config('request.jwt.claim.sub', %s, true)",
                        (str(owner),),
                    )
                    conn.execute(
                        "SELECT set_config("
                        "'request.jwt.claim.app_namespace', 'app_alpha', true)"
                    )
                    conn.execute(
                        "SELECT secret_payload FROM app_alpha.game_sanitized_projection"
                    )

            with conn.transaction():
                conn.execute("SET LOCAL ROLE authenticated")
                conn.execute(
                    "SELECT set_config('request.jwt.claim.sub', %s, true)",
                    (str(owner),),
                )
                conn.execute(
                    "SELECT set_config('request.jwt.claim.app_namespace', 'app_alpha', true)"
                )
                visible = conn.execute(
                    "SELECT has_secret FROM app_alpha.game_sanitized_projection"
                ).fetchall()
                self.assertGreaterEqual(len(visible), 1)
                self.assertTrue(visible[0][0] is True)

                conn.execute(
                    "SELECT set_config('request.jwt.claim.app_namespace', 'app_beta', true)"
                )
                denied = conn.execute(
                    "SELECT count(*) FROM app_alpha.game_sanitized_projection"
                ).fetchone()[0]
                self.assertEqual(denied, 0)

                conn.execute(
                    "SELECT set_config('request.jwt.claim.app_namespace', 'app_alpha', true)"
                )
                conn.execute(
                    "SELECT set_config('request.jwt.claim.sub', %s, true)",
                    (str(other),),
                )
                base_count = conn.execute(
                    "SELECT count(*) FROM app_alpha.game_private_state"
                ).fetchone()[0]
                self.assertEqual(base_count, 0)

            with conn.transaction():
                conn.execute("SET LOCAL ROLE service_role")
                bypass = conn.execute(
                    "SELECT count(*) FROM app_alpha.game_private_state"
                ).fetchone()[0]
                self.assertGreaterEqual(bypass, 1)

    def test_preexisting_grants_policies_publication_preserved(self):
        import psycopg

        with psycopg.connect(self.admin_url) as conn:
            pol = conn.execute(
                """
                SELECT polname FROM pg_policy
                WHERE polrelid = 'public.preexisting_shared'::regclass
                """
            ).fetchone()
            self.assertIsNotNone(pol)
            self.assertEqual(pol[0], "preexisting_owner_all")

            grant = conn.execute(
                """
                SELECT has_table_privilege('authenticated', 'public.preexisting_shared', 'SELECT')
                """
            ).fetchone()[0]
            self.assertTrue(grant)

            pub = conn.execute(
                """
                SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'supabase_realtime'
                  AND schemaname = 'public'
                  AND tablename = 'preexisting_shared'
                """
            ).fetchone()
            self.assertIsNotNone(pub)

            public_create = conn.execute(
                "SELECT has_schema_privilege('public', 'public', 'CREATE')"
            ).fetchone()[0]
            self.assertFalse(public_create)

    def test_out_of_order_migration_below_ledger_max_rejected(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for migration in self.harness.discover_migrations(ALPHA_DIR):
                (tmp_path / migration.path.name).write_bytes(migration.sql_bytes)
            late = tmp_path / "0000_backfill.sql"
            late.write_text(
                "CREATE TABLE IF NOT EXISTS ooo_marker (id int PRIMARY KEY);\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.harness.MigrationError) as ctx:
                self.harness.migrate_app(
                    database_url=self._migrator_url("app_alpha"),
                    namespace="app_alpha",
                    migrations_dir=tmp_path,
                )
            self.assertIn("out-of-order", str(ctx.exception).lower())
            with psycopg.connect(self.admin_url) as conn:
                exists = conn.execute(
                    """
                    SELECT to_regclass('app_alpha.ooo_marker') IS NOT NULL
                    """
                ).fetchone()[0]
                self.assertFalse(exists)

    def test_concurrent_runners_and_rollback_idempotence(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for migration in self.harness.discover_migrations(BETA_DIR):
                (tmp_path / migration.path.name).write_bytes(migration.sql_bytes)
            extra = tmp_path / "0003_concurrent.sql"
            extra.write_text(
                "CREATE TABLE IF NOT EXISTS concurrent_marker (\n"
                "  id int PRIMARY KEY,\n"
                "  note text NOT NULL\n"
                ");\n"
                "INSERT INTO concurrent_marker (id, note) VALUES (1, 'once')\n"
                "ON CONFLICT (id) DO NOTHING;\n",
                encoding="utf-8",
            )

            results = []
            errors = []

            def runner():
                try:
                    results.append(
                        self.harness.migrate_app(
                            database_url=self._migrator_url("app_beta"),
                            namespace="app_beta",
                            migrations_dir=tmp_path,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - collect for assertion
                    errors.append(exc)

            threads = [threading.Thread(target=runner) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            applied_total = sum(item.applied_count for item in results)
            self.assertEqual(applied_total, 1)
            with psycopg.connect(self.admin_url) as conn:
                count = conn.execute(
                    "SELECT count(*) FROM app_beta.concurrent_marker"
                ).fetchone()[0]
                self.assertEqual(count, 1)
                ledger = conn.execute(
                    """
                    SELECT count(*) FROM app_migration_control.ledger
                    WHERE app_namespace = 'app_beta'
                      AND migration_id = '0003_concurrent'
                    """
                ).fetchone()[0]
                self.assertEqual(ledger, 1)

            # Rollback path: failing migration must not leave partial ledger row
            fail_dir = Path(tempfile.mkdtemp())
            try:
                for migration in self.harness.discover_migrations(tmp_path):
                    (fail_dir / migration.path.name).write_bytes(migration.sql_bytes)
                boom = fail_dir / "0004_boom.sql"
                boom.write_text(
                    "CREATE TABLE boom_ok (id int);\n"
                    "DO $$ BEGIN RAISE EXCEPTION 'forced-rollback-secret password=leak'; END $$;\n",
                    encoding="utf-8",
                )
                with self.assertRaises(self.harness.MigrationError):
                    self.harness.migrate_app(
                        database_url=self._migrator_url("app_beta"),
                        namespace="app_beta",
                        migrations_dir=fail_dir,
                    )
                with psycopg.connect(self.admin_url) as conn:
                    self.assertIsNone(
                        conn.execute(
                            "SELECT to_regclass('app_beta.boom_ok')"
                        ).fetchone()[0]
                    )
                    self.assertEqual(
                        conn.execute(
                            """
                            SELECT count(*) FROM app_migration_control.ledger
                            WHERE app_namespace = 'app_beta'
                              AND migration_id = '0004_boom'
                            """
                        ).fetchone()[0],
                        0,
                    )
            finally:
                for child in fail_dir.glob("*"):
                    child.unlink()
                fail_dir.rmdir()

    def test_cli_redacts_migration_error(self):
        import io
        from contextlib import redirect_stderr

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for migration in self.harness.discover_migrations(ALPHA_DIR):
                (tmp_path / migration.path.name).write_bytes(migration.sql_bytes)
            bad = tmp_path / "0003_fail.sql"
            bad.write_text(
                "DO $$ BEGIN RAISE EXCEPTION "
                "'visible postgresql://u:secret@127.0.0.1/db password=hunter2'; "
                "END $$;\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = self.harness.main(
                    [
                        "--database-url",
                        self._migrator_url("app_alpha"),
                        "--namespace",
                        "app_alpha",
                        "--migrations-dir",
                        str(tmp_path),
                        "--local-disposable-only",
                    ]
                )
            self.assertEqual(code, 1)
            err = stderr.getvalue()
            self.assertNotIn("secret", err)
            self.assertNotIn("hunter2", err)
            self.assertIn("[redacted]", err)


if __name__ == "__main__":
    unittest.main()

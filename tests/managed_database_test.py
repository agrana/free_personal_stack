#!/usr/bin/env python3
"""Managed database foundation tests (PostgreSQL 17 disposable integration).

Unit checks always run. Integration uses MDB_PG_TESTS modes:
0=skip, 1=mandatory (provision failures are errors), auto=run when podman +
postgres:17-alpine are available. APP003_PG_TESTS=1 also forces this suite
(even when MDB_PG_TESTS=0). Focused MDB override applies only without that
global force. Existing APP-003 PG16 fixtures remain unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import socket
import subprocess
import tempfile
import threading
import traceback
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = REPO_ROOT / "db" / "managed_database"
SQL_DIR = DB_ROOT / "sql"
PREEXISTING_SQL = DB_ROOT / "fixtures" / "preexisting" / "01_seed.sql"
APP_ONE_DIR = DB_ROOT / "fixtures" / "app_one"
APP_TWO_DIR = DB_ROOT / "fixtures" / "app_two"

PG_IMAGE = os.environ.get("MDB_PG_IMAGE", "docker.io/library/postgres:17-alpine")

APP_ONE_REPO = "100001"
APP_TWO_REPO = "100002"
APP_ONE_PROJECT = "proj-app-one"
APP_TWO_PROJECT = "proj-app-two"

# Permission denials (not in_failed_sql_transaction).
_SQLSTATE_INSUFFICIENT_PRIVILEGE = "42501"
_SQLSTATE_IN_FAILED_SQL_TRANSACTION = "25P02"
_SQLSTATE_RAISE_EXCEPTION = "P0001"


def _load_module():
    import sys

    path = REPO_ROOT / "scripts" / "managed_database.py"
    spec = importlib.util.spec_from_file_location("managed_database", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _podman_image_ready() -> bool:
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


def _pg_tests_mandatory() -> bool:
    return (
        os.environ.get("APP003_PG_TESTS") == "1"
        or os.environ.get("MDB_PG_TESTS") == "1"
    )


def _pg_tests_enabled() -> bool:
    """APP003_PG_TESTS=1 forces managed PG17 even when MDB_PG_TESTS=0."""
    if os.environ.get("APP003_PG_TESTS") == "1":
        return True
    run_pg = os.environ.get("MDB_PG_TESTS", "auto")
    if run_pg == "0":
        return False
    if run_pg == "1":
        return True
    return _podman_image_ready()


def _dsn(user: str, password: str, port: int, db: str = "mdb") -> str:
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/{db}"
    )


class ManagedDatabaseUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mdb = _load_module()

    def test_import_is_inert(self):
        source = (REPO_ROOT / "scripts" / "managed_database.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Importing this module performs", source)
        self.assertNotIn("psycopg.connect(", source.split("def _connect")[0])

    def test_ignores_inherited_database_url_env(self):
        os.environ["DATABASE_URL"] = "postgresql://should-not-be-used/db"
        os.environ["SUPABASE_DB_URL"] = "postgresql://also-not-used/db"
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    project_ref=APP_ONE_PROJECT,
                    migrator_password="x",
                    runtime_password="y",
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.CONNECTION)
        finally:
            os.environ.pop("DATABASE_URL", None)
            os.environ.pop("SUPABASE_DB_URL", None)

    def test_namespace_and_binding_validation(self):
        for name in (
            "public",
            "auth",
            "app_migration_control",
            "pg_toast",
        ):
            with self.assertRaises(self.mdb.ManagedDatabaseError):
                self.mdb.validate_namespace(name)
        too_long = "a" * (self.mdb.MAX_NAMESPACE_LEN + 1)
        with self.assertRaises(self.mdb.ManagedDatabaseError):
            self.mdb.validate_namespace(too_long)
        ok = "a" * self.mdb.MAX_NAMESPACE_LEN
        self.assertEqual(self.mdb.validate_namespace(ok), ok)
        with self.assertRaises(self.mdb.ManagedDatabaseError):
            self.mdb.validate_repository_id("0")
        with self.assertRaises(self.mdb.ManagedDatabaseError):
            self.mdb.validate_project_ref("Bad Ref")

    def test_secret_repr_hides_password(self):
        canary = f"canary-{uuid.uuid4().hex}"
        secret = self.mdb._Secret(canary)
        self.assertNotIn(canary, repr(secret))
        self.assertNotIn(canary, str(secret))
        self.assertIn("redacted", repr(secret).lower())

    def test_failure_code_repr_hides_driver_detail(self):
        canary = f"dsn-canary-{uuid.uuid4().hex}"
        err = self.mdb.ManagedDatabaseError(
            self.mdb.FailureCode.MIGRATION_EXEC, canary
        )
        self.assertNotIn("password=", repr(err))
        self.assertEqual(err.code, self.mdb.FailureCode.MIGRATION_EXEC)

    def test_public_error_suppresses_cause_chain_in_traceback(self):
        driver_canary = f"driver-canary-{uuid.uuid4().hex}"
        sql_canary = f"sql-canary-{uuid.uuid4().hex}"

        def boom_driver():
            try:
                raise RuntimeError(f"connection failed password={driver_canary}")
            except Exception:
                self.mdb._raise_public(self.mdb.FailureCode.CONNECTION)

        def boom_sql():
            try:
                raise RuntimeError(f"ERROR: {sql_canary} DETAIL: secret-row")
            except Exception:
                self.mdb._raise_public(self.mdb.FailureCode.MIGRATION_EXEC)

        for fn, canary in ((boom_driver, driver_canary), (boom_sql, sql_canary)):
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                fn()
            formatted = "".join(
                traceback.format_exception(
                    type(ctx.exception), ctx.exception, ctx.exception.__traceback__
                )
            )
            self.assertNotIn(canary, formatted)
            self.assertNotIn(canary, str(ctx.exception))
            self.assertNotIn(canary, repr(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)
            self.assertTrue(ctx.exception.__suppress_context__)

    def test_env_precedence_app003_forces_managed_pg(self):
        saved_app = os.environ.get("APP003_PG_TESTS")
        saved_mdb = os.environ.get("MDB_PG_TESTS")
        try:
            os.environ["APP003_PG_TESTS"] = "1"
            os.environ["MDB_PG_TESTS"] = "0"
            self.assertTrue(_pg_tests_enabled())
            self.assertTrue(_pg_tests_mandatory())

            os.environ["APP003_PG_TESTS"] = "0"
            os.environ["MDB_PG_TESTS"] = "0"
            self.assertFalse(_pg_tests_enabled())
            self.assertFalse(_pg_tests_mandatory())

            os.environ.pop("APP003_PG_TESTS", None)
            os.environ["MDB_PG_TESTS"] = "1"
            self.assertTrue(_pg_tests_enabled())
            self.assertTrue(_pg_tests_mandatory())
        finally:
            if saved_app is None:
                os.environ.pop("APP003_PG_TESTS", None)
            else:
                os.environ["APP003_PG_TESTS"] = saved_app
            if saved_mdb is None:
                os.environ.pop("MDB_PG_TESTS", None)
            else:
                os.environ["MDB_PG_TESTS"] = saved_mdb

    def test_migration_discovery_immutable_bytes_and_symlink_reject(self):
        migrations = self.mdb.discover_migrations(APP_ONE_DIR)
        self.assertGreaterEqual(len(migrations), 2)
        for migration in migrations:
            self.assertEqual(
                migration.checksum_sha256,
                hashlib.sha256(migration.sql_bytes).hexdigest(),
            )
            self.assertEqual(migration.sql_bytes, migration.path.read_bytes())
            with self.assertRaises(Exception):
                migration.sql_bytes = b"mutated"  # type: ignore[misc]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "0001_init.sql"
            target.write_text("SELECT 1;\n", encoding="utf-8")
            link = root / "0002_link.sql"
            link.symlink_to(target)
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.discover_migrations(root)
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.MIGRATION_PATH)

    def test_split_sql_nested_comments_estrings_dollar_quotes_identifiers(self):
        script = (
            "CREATE TABLE t1 (id int); /* outer /* nested ; */ still */\n"
            "CREATE TABLE \"Weird;Name\" (id int);\n"
            "SELECT E'semi;colon\\'keep';\n"
            "CREATE FUNCTION f() RETURNS void AS $body$\n"
            "BEGIN\n"
            "  PERFORM 1;\n"
            "END;\n"
            "$body$ LANGUAGE plpgsql;\n"
        )
        parts = self.mdb.split_sql_statements(script)
        self.assertEqual(len(parts), 4)
        self.assertIn("t1", parts[0])
        self.assertIn('"Weird;Name"', parts[1])
        self.assertIn("E'", parts[2])
        self.assertIn("$body$", parts[3])

    def test_support_and_fixture_files_exist(self):
        self.assertTrue((SQL_DIR / "control_objects.sql").is_file())
        self.assertTrue(PREEXISTING_SQL.is_file())
        self.assertTrue(APP_ONE_DIR.is_dir())
        self.assertTrue(APP_TWO_DIR.is_dir())
        control = (SQL_DIR / "control_objects.sql").read_text(encoding="utf-8")
        self.assertIn("FORCE ROW LEVEL SECURITY", control)
        self.assertIn("record_applied", control)
        self.assertIn("session_user", control)
        self.assertNotIn("BYPASSRLS", control)
        self.assertNotIn("EVENT TRIGGER", control.upper())
        self.assertNotIn("app_migration_control", control)


@unittest.skipUnless(_pg_tests_enabled(), "disposable local PostgreSQL 17 unavailable")
class ManagedDatabaseIntegrationTests(unittest.TestCase):
    container_name: str
    port: int
    super_password: str
    provisioner_password: str
    admin_url: str
    provisioner_url: str
    mdb: object

    @classmethod
    def setUpClass(cls):
        cls.mdb = _load_module()
        cls.super_password = f"mdb-super-{uuid.uuid4().hex}"
        cls.provisioner_password = f"mdb-prov-{uuid.uuid4().hex}"
        cls.port = _free_loopback_port()
        cls.container_name = f"mdb-pg17-{uuid.uuid4().hex[:12]}"
        cls.admin_url = _dsn("postgres", cls.super_password, cls.port)
        cls.provisioner_url = _dsn(
            "mdb_provisioner", cls.provisioner_password, cls.port
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
                f"POSTGRES_PASSWORD={cls.super_password}",
                "-e",
                "POSTGRES_USER=postgres",
                "-e",
                "POSTGRES_DB=mdb",
                PG_IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        if run.returncode != 0:
            msg = f"podman run failed: {run.stderr.strip()}"
            if _pg_tests_mandatory():
                raise RuntimeError(msg)
            raise unittest.SkipTest(msg)
        try:
            cls._wait_ready()
            cls._bootstrap_superuser()
            cls._seed_preexisting()
            cls._provision_both_apps()
            cls._migrate_both_apps()
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
    def _wait_ready(cls):
        import psycopg

        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with psycopg.connect(cls.admin_url, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                    version = conn.execute("SHOW server_version").fetchone()[0]
                    if not version.startswith("17"):
                        raise RuntimeError(f"expected PostgreSQL 17, got {version}")
                return
            except Exception:
                time.sleep(0.5)
        msg = "postgres 17 did not become ready on loopback"
        if _pg_tests_mandatory():
            raise RuntimeError(msg)
        raise unittest.SkipTest(msg)

    @classmethod
    def _bootstrap_superuser(cls):
        import psycopg
        from psycopg import sql as psql

        with psycopg.connect(cls.admin_url, autocommit=True) as conn:
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            for role in ("anon", "authenticated", "authenticator"):
                conn.execute(
                    psql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS "
                        "NOCREATEROLE NOCREATEDB NOREPLICATION"
                    ).format(psql.Identifier(role))
                )
            conn.execute(
                psql.SQL(
                    "CREATE ROLE mdb_provisioner LOGIN PASSWORD {} "
                    "NOSUPERUSER CREATEROLE NOBYPASSRLS NOCREATEDB NOREPLICATION"
                ).format(psql.Literal(cls.provisioner_password))
            )
            conn.execute(
                "GRANT CREATE, CONNECT, TEMPORARY ON DATABASE mdb TO mdb_provisioner"
            )

    @classmethod
    def _seed_preexisting(cls):
        import psycopg

        sql_bytes = PREEXISTING_SQL.read_bytes()
        with psycopg.connect(cls.admin_url, autocommit=True) as conn:
            conn.pgconn.exec_(sql_bytes)

    @classmethod
    def _app_passwords(cls, namespace: str):
        suffix = cls.super_password[-10:]
        return (
            f"{namespace}-mig-{suffix}",
            f"{namespace}-run-{suffix}",
        )

    @classmethod
    def _migrator_url(cls, namespace: str) -> str:
        mig_pw, _ = cls._app_passwords(namespace)
        return _dsn(f"{namespace}_migrator", mig_pw, cls.port)

    @classmethod
    def _runtime_url(cls, namespace: str) -> str:
        _, run_pw = cls._app_passwords(namespace)
        return _dsn(f"{namespace}_runtime", run_pw, cls.port)

    @classmethod
    def _provision_both_apps(cls):
        for namespace, repo, project in (
            ("app_one", APP_ONE_REPO, APP_ONE_PROJECT),
            ("app_two", APP_TWO_REPO, APP_TWO_PROJECT),
        ):
            mig_pw, run_pw = cls._app_passwords(namespace)
            result = cls.mdb.provision_app(
                namespace=namespace,
                repository_id=repo,
                project_ref=project,
                migrator_password=mig_pw,
                runtime_password=run_pw,
                provisioner_dsn=cls.provisioner_url,
            )
            if not result.created:
                raise RuntimeError(f"expected created provision for {namespace}")

    @classmethod
    def _migrate_both_apps(cls):
        for namespace, repo, path in (
            ("app_one", APP_ONE_REPO, APP_ONE_DIR),
            ("app_two", APP_TWO_REPO, APP_TWO_DIR),
        ):
            result = cls.mdb.migrate_app(
                namespace=namespace,
                repository_id=repo,
                migrations_dir=path,
                migrator_dsn=cls._migrator_url(namespace),
            )
            if result.status != "ok" or result.applied_count < 2:
                raise RuntimeError(f"initial migrate failed for {namespace}: {result}")

    def _assert_denied_sqlstate(self, dsn: str, sql: str, params=None, *, allowed_states):
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as conn:
            with self.assertRaises(psycopg.Error) as ctx:
                if params is None:
                    conn.execute(sql)
                else:
                    conn.execute(sql, params)
            state = ctx.exception.sqlstate
            self.assertNotEqual(state, _SQLSTATE_IN_FAILED_SQL_TRANSACTION)
            self.assertIn(state, allowed_states)

    def _copy_fixture_migrations(self, tmp_path: Path, fixture_dir: Path) -> None:
        for migration in self.mdb.discover_migrations(fixture_dir):
            (tmp_path / migration.path.name).write_bytes(migration.sql_bytes)

    def test_server_is_postgres_17(self):
        import psycopg

        with psycopg.connect(self.admin_url) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            self.assertTrue(version.startswith("17"), version)
            flags = conn.execute(
                """
                SELECT rolsuper, rolcreaterole, rolbypassrls
                FROM pg_roles WHERE rolname = 'mdb_provisioner'
                """
            ).fetchone()
            self.assertEqual(flags, (False, True, False))

    def test_repeat_provision_no_credential_reset(self):
        mig_pw, run_pw = self._app_passwords("app_one")
        result = self.mdb.provision_app(
            namespace="app_one",
            repository_id=APP_ONE_REPO,
            project_ref=APP_ONE_PROJECT,
            migrator_password=uuid.uuid4().hex,
            runtime_password=uuid.uuid4().hex,
            provisioner_dsn=self.provisioner_url,
        )
        self.assertFalse(result.created)
        import psycopg

        with psycopg.connect(self._migrator_url("app_one")) as conn:
            conn.execute("SELECT 1")
        bad = _dsn(
            "app_one_migrator", "should-not-apply-canary", self.port
        )
        with self.assertRaises(Exception):
            with psycopg.connect(bad, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
        self.assertTrue(mig_pw and run_pw)

    def test_repeat_provision_rejects_dropped_data_schema(self):
        import psycopg

        mig_pw, run_pw = self._app_passwords("app_drop")
        self.mdb.provision_app(
            namespace="app_drop",
            repository_id="200010",
            project_ref="proj-drop",
            migrator_password=mig_pw,
            runtime_password=run_pw,
            provisioner_dsn=self.provisioner_url,
        )
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA app_drop CASCADE")
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_drop",
                    repository_id="200010",
                    project_ref="proj-drop",
                    migrator_password=mig_pw,
                    runtime_password=run_pw,
                    provisioner_dsn=self.provisioner_url,
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.OWNERSHIP_MISMATCH)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                conn.execute("DROP SCHEMA IF EXISTS app_drop_control CASCADE")
                for role in (
                    "app_drop_runtime",
                    "app_drop_migrator",
                    "app_drop_owner",
                ):
                    conn.execute(f"DROP ROLE IF EXISTS {role}")

    def test_repeat_provision_rejects_runtime_schema_create_grant(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("GRANT CREATE ON SCHEMA app_one TO app_one_runtime")
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    project_ref=APP_ONE_PROJECT,
                    migrator_password="x",
                    runtime_password="y",
                    provisioner_dsn=self.provisioner_url,
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                conn.execute("REVOKE CREATE ON SCHEMA app_one FROM app_one_runtime")

    def test_unknown_ownership_and_role_collision(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(
                "CREATE ROLE app_delta_migrator LOGIN PASSWORD 'orphan-pw'"
            )
            conn.execute(
                "CREATE SCHEMA app_orphan AUTHORIZATION CURRENT_USER"
            )
        with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
            self.mdb.provision_app(
                namespace="app_delta",
                repository_id="200001",
                project_ref="proj-delta",
                migrator_password=uuid.uuid4().hex,
                runtime_password=uuid.uuid4().hex,
                provisioner_dsn=self.provisioner_url,
            )
        self.assertEqual(ctx.exception.code, self.mdb.FailureCode.ROLE_COLLISION)
        with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx2:
            self.mdb.provision_app(
                namespace="app_orphan",
                repository_id="200002",
                project_ref="proj-orphan",
                migrator_password=uuid.uuid4().hex,
                runtime_password=uuid.uuid4().hex,
                provisioner_dsn=self.provisioner_url,
            )
        self.assertEqual(ctx2.exception.code, self.mdb.FailureCode.SCHEMA_COLLISION)
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA app_orphan CASCADE")
            conn.execute("DROP ROLE app_delta_migrator")

    def test_public_create_fail_closed(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
            try:
                with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                    self.mdb.provision_app(
                        namespace="app_gamma",
                        repository_id="200003",
                        project_ref="proj-gamma",
                        migrator_password="pw",
                        runtime_password="pw2",
                        provisioner_dsn=self.provisioner_url,
                    )
                self.assertEqual(
                    ctx.exception.code,
                    self.mdb.FailureCode.PUBLIC_CREATE_PRIVILEGE,
                )
            finally:
                conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    def test_privilege_drift_rejected_on_repeat(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("GRANT app_one_owner TO app_one_runtime")
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    project_ref=APP_ONE_PROJECT,
                    migrator_password="x",
                    runtime_password="y",
                    provisioner_dsn=self.provisioner_url,
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                conn.execute("REVOKE app_one_owner FROM app_one_runtime")

    def test_transitive_membership_drift_rejected(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("CREATE ROLE app_one_bridge NOLOGIN")
            conn.execute("GRANT app_one_bridge TO app_one_runtime")
            conn.execute("GRANT app_one_owner TO app_one_bridge")
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    project_ref=APP_ONE_PROJECT,
                    migrator_password="x",
                    runtime_password="y",
                    provisioner_dsn=self.provisioner_url,
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx2:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=APP_ONE_DIR,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(ctx2.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                conn.execute("REVOKE app_one_owner FROM app_one_bridge")
                conn.execute("REVOKE app_one_bridge FROM app_one_runtime")
                conn.execute("DROP ROLE app_one_bridge")

    def test_privileged_group_membership_drift_rejected(self):
        import psycopg

        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("GRANT pg_read_all_data TO app_one_runtime")
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    project_ref=APP_ONE_PROJECT,
                    migrator_password="x",
                    runtime_password="y",
                    provisioner_dsn=self.provisioner_url,
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                conn.execute("REVOKE pg_read_all_data FROM app_one_runtime")

    def test_post_migration_invariants(self):
        self.mdb.verify_post_migration_invariants(
            namespace="app_one",
            provisioner_dsn=self.provisioner_url,
        )
        self.mdb.verify_post_migration_invariants(
            namespace="app_two",
            provisioner_dsn=self.provisioner_url,
        )

    def test_migrate_rolls_back_public_select_grant(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_public_select.sql").write_text(
                "CREATE TABLE public_select_marker (id int PRIMARY KEY);\n"
                "ALTER TABLE public_select_marker ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE public_select_marker FORCE ROW LEVEL SECURITY;\n"
                "GRANT SELECT ON public_select_marker TO PUBLIC;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.public_select_marker')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_public_select'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_anon_truncate_grant(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_anon_truncate.sql").write_text(
                "CREATE TABLE anon_trunc_marker (id int PRIMARY KEY);\n"
                "ALTER TABLE anon_trunc_marker ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE anon_trunc_marker FORCE ROW LEVEL SECURITY;\n"
                "GRANT TRUNCATE ON anon_trunc_marker TO anon;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.anon_trunc_marker')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_anon_truncate'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_column_grant_to_public(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_column_grant.sql").write_text(
                "CREATE TABLE col_grant_marker (\n"
                "  id int PRIMARY KEY,\n"
                "  secret text NOT NULL\n"
                ");\n"
                "ALTER TABLE col_grant_marker ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE col_grant_marker FORCE ROW LEVEL SECURITY;\n"
                "GRANT SELECT (secret) ON col_grant_marker TO PUBLIC;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.col_grant_marker')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_column_grant'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_partition_parent_missing_rls(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_part_parent.sql").write_text(
                "CREATE TABLE part_parent (\n"
                "  id int NOT NULL,\n"
                "  tenant int NOT NULL,\n"
                "  PRIMARY KEY (id, tenant)\n"
                ") PARTITION BY LIST (tenant);\n"
                "CREATE TABLE part_child PARTITION OF part_parent\n"
                "  FOR VALUES IN (1);\n"
                "ALTER TABLE part_child ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE part_child FORCE ROW LEVEL SECURITY;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.part_parent')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_part_parent'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_schema_create_grant_to_public(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_schema_public_create.sql").write_text(
                "CREATE TABLE schema_public_create_marker (id int PRIMARY KEY);\n"
                "ALTER TABLE schema_public_create_marker ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE schema_public_create_marker FORCE ROW LEVEL SECURITY;\n"
                "GRANT CREATE ON SCHEMA app_one TO PUBLIC;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.schema_public_create_marker')"
                    ).fetchone()[0]
                )
                self.assertFalse(
                    conn.execute(
                        "SELECT has_schema_privilege('public', 'app_one', 'CREATE')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_schema_public_create'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_schema_create_grant_to_authenticated(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_schema_auth_create.sql").write_text(
                "CREATE TABLE schema_auth_create_marker (id int PRIMARY KEY);\n"
                "ALTER TABLE schema_auth_create_marker ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE schema_auth_create_marker FORCE ROW LEVEL SECURITY;\n"
                "GRANT CREATE ON SCHEMA app_one TO authenticated;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.PRIVILEGE_DRIFT)
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.schema_auth_create_marker')"
                    ).fetchone()[0]
                )
                self.assertFalse(
                    conn.execute(
                        "SELECT has_schema_privilege("
                        "'authenticated', 'app_one', 'CREATE')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_schema_auth_create'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_public_security_definer_truncate(self):
        import psycopg

        user_id = str(uuid.uuid4())
        with psycopg.connect(self._runtime_url("app_one"), autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)",
                (user_id,),
            )
            conn.execute(
                "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                (user_id, "preserve-through-definer-deny"),
            )
            before = conn.execute(
                "SELECT count(*) FROM items WHERE note = %s",
                ("preserve-through-definer-deny",),
            ).fetchone()[0]
            self.assertEqual(before, 1)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_definer_truncate.sql").write_text(
                "CREATE FUNCTION wipe_items()\n"
                "RETURNS void\n"
                "LANGUAGE plpgsql\n"
                "SECURITY DEFINER\n"
                "SET search_path = app_one, pg_temp\n"
                "AS $fn$\n"
                "BEGIN\n"
                "  TRUNCATE items;\n"
                "END;\n"
                "$fn$;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )

        with psycopg.connect(self.admin_url) as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT to_regprocedure('app_one.wipe_items()')"
                ).fetchone()[0]
            )
            self.assertEqual(
                conn.execute(
                    """
                    SELECT count(*) FROM app_one_control.ledger
                    WHERE migration_id = '0003_definer_truncate'
                    """
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM app_one.items "
                    "WHERE note = 'preserve-through-definer-deny'"
                ).fetchone()[0],
                1,
            )

    def test_migrate_rolls_back_runtime_granted_security_definer(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_definer_runtime_grant.sql").write_text(
                "CREATE FUNCTION privileged_helper()\n"
                "RETURNS void\n"
                "LANGUAGE plpgsql\n"
                "SECURITY DEFINER\n"
                "SET search_path = app_one, pg_temp\n"
                "AS $fn$ BEGIN NULL; END; $fn$;\n"
                "REVOKE ALL ON FUNCTION privileged_helper() FROM PUBLIC;\n"
                "GRANT EXECUTE ON FUNCTION privileged_helper() TO app_one_runtime;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regprocedure('app_one.privileged_helper()')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_definer_runtime_grant'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_default_public_invoker_routine(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_public_invoker.sql").write_text(
                "CREATE FUNCTION public_invoker_ping()\n"
                "RETURNS text\n"
                "LANGUAGE sql\n"
                "SECURITY INVOKER\n"
                "SET search_path = app_one, pg_temp\n"
                "AS $fn$ SELECT 'ping'; $fn$;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regprocedure('app_one.public_invoker_ping()')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_public_invoker'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_allows_explicit_runtime_invoker_routine(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_runtime_invoker.sql").write_text(
                "CREATE FUNCTION runtime_invoker_ping()\n"
                "RETURNS text\n"
                "LANGUAGE sql\n"
                "SECURITY INVOKER\n"
                "SET search_path = app_one, pg_temp\n"
                "AS $fn$ SELECT 'runtime-ok'; $fn$;\n"
                "REVOKE ALL ON FUNCTION runtime_invoker_ping() FROM PUBLIC;\n"
                "GRANT EXECUTE ON FUNCTION runtime_invoker_ping() "
                "TO app_one_runtime;\n",
                encoding="utf-8",
            )
            result = self.mdb.migrate_app(
                namespace="app_one",
                repository_id=APP_ONE_REPO,
                migrations_dir=tmp_path,
                migrator_dsn=self._migrator_url("app_one"),
            )
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.applied_count, 1)

        with psycopg.connect(self._runtime_url("app_one"), autocommit=True) as conn:
            self.assertEqual(
                conn.execute("SELECT app_one.runtime_invoker_ping()").fetchone()[0],
                "runtime-ok",
            )

        # Cleanup so later migrations using 0003_* ids stay free.
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute("DROP FUNCTION IF EXISTS app_one.runtime_invoker_ping()")
            conn.execute(
                "DELETE FROM app_one_control.ledger "
                "WHERE migration_id = '0003_runtime_invoker'"
            )

    def test_migrate_rolls_back_security_definer_procedure(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_definer_proc.sql").write_text(
                "CREATE PROCEDURE wipe_via_proc()\n"
                "LANGUAGE plpgsql\n"
                "SECURITY DEFINER\n"
                "SET search_path = app_one, pg_temp\n"
                "AS $fn$\n"
                "BEGIN\n"
                "  TRUNCATE items;\n"
                "END;\n"
                "$fn$;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regprocedure('app_one.wipe_via_proc()')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_definer_proc'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_migrate_rolls_back_security_definer_trigger_function(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_definer_trigger_fn.sql").write_text(
                "CREATE FUNCTION items_definer_trg()\n"
                "RETURNS trigger\n"
                "LANGUAGE plpgsql\n"
                "SECURITY DEFINER\n"
                "SET search_path = app_one, pg_temp\n"
                "AS $fn$\n"
                "BEGIN\n"
                "  RETURN NEW;\n"
                "END;\n"
                "$fn$;\n"
                "REVOKE ALL ON FUNCTION items_definer_trg() FROM PUBLIC;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.INVARIANT_VIOLATION
            )
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regprocedure('app_one.items_definer_trg()')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_definer_trigger_fn'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_sequential_provision_on_reused_autocommit_connection(self):
        import psycopg

        namespaces = (
            ("app_reuse_a", "200101", "proj-reuse-a"),
            ("app_reuse_b", "200102", "proj-reuse-b"),
        )
        with psycopg.connect(self.provisioner_url, autocommit=True) as conn:
            self.assertEqual(int(conn.info.transaction_status), 0)
            for namespace, repo, project in namespaces:
                mig_pw, run_pw = self._app_passwords(namespace)
                result = self.mdb.provision_app(
                    namespace=namespace,
                    repository_id=repo,
                    project_ref=project,
                    migrator_password=mig_pw,
                    runtime_password=run_pw,
                    provisioner_connection=conn,
                )
                self.assertTrue(result.created, namespace)
                self.assertEqual(int(conn.info.transaction_status), 0)
                self.assertTrue(conn.autocommit)

        for namespace, repo, project in namespaces:
            mig_pw, run_pw = self._app_passwords(namespace)
            repeat = self.mdb.provision_app(
                namespace=namespace,
                repository_id=repo,
                project_ref=project,
                migrator_password=uuid.uuid4().hex,
                runtime_password=uuid.uuid4().hex,
                provisioner_dsn=self.provisioner_url,
            )
            self.assertFalse(repeat.created, namespace)
            with psycopg.connect(
                _dsn(f"{namespace}_migrator", mig_pw, self.port),
                autocommit=True,
            ) as mig_conn:
                self.assertEqual(
                    mig_conn.execute("SELECT current_user").fetchone()[0],
                    f"{namespace}_migrator",
                )
            with psycopg.connect(
                _dsn(f"{namespace}_runtime", run_pw, self.port),
                autocommit=True,
            ) as run_conn:
                self.assertEqual(
                    run_conn.execute("SELECT current_user").fetchone()[0],
                    f"{namespace}_runtime",
                )

    def test_idle_non_autocommit_connections_rejected_before_probes(self):
        import psycopg

        shared = {"namespace": "app_one", "repository_id": APP_ONE_REPO}
        mig_pw, run_pw = self._app_passwords("app_one")
        cases = (
            (self.mdb.provision_app, self.provisioner_url,
             "provisioner_connection", dict(shared, project_ref=APP_ONE_PROJECT,
                                            migrator_password=mig_pw,
                                            runtime_password=run_pw)),
            (self.mdb.migrate_app, self._migrator_url("app_one"),
             "migrator_connection", dict(shared, migrations_dir=APP_ONE_DIR)),
            (self.mdb.verify_post_migration_invariants, self.provisioner_url,
             "provisioner_connection", {"namespace": "app_one"}),
            (self.mdb.readiness_metadata, self.provisioner_url,
             "provisioner_connection", shared),
        )
        for operation, dsn, connection_key, arguments in cases:
            with self.subTest(operation=operation.__name__):
                with psycopg.connect(dsn) as conn:
                    self.assertEqual(int(conn.info.transaction_status), 0)
                    self.assertFalse(conn.autocommit)
                    with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                        operation(**arguments, **{connection_key: conn})
                    self.assertEqual(ctx.exception.code, self.mdb.FailureCode.CONNECTION)
                    self.assertEqual(int(conn.info.transaction_status), 0)
                    self.assertFalse(conn.autocommit)

    def test_active_caller_transaction_rejected_without_rollback(self):
        import psycopg

        with psycopg.connect(self.provisioner_url) as conn:
            conn.execute(
                "CREATE TEMP TABLE caller_pending (id int PRIMARY KEY, note text)"
            )
            conn.execute(
                "INSERT INTO caller_pending (id, note) VALUES (1, 'keep-me')"
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.provision_app(
                    namespace="app_txn",
                    repository_id="200020",
                    project_ref="proj-txn",
                    migrator_password="x",
                    runtime_password="y",
                    provisioner_connection=conn,
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.ACTIVE_TRANSACTION
            )
            self.assertEqual(
                conn.execute(
                    "SELECT note FROM caller_pending WHERE id = 1"
                ).fetchone()[0],
                "keep-me",
            )
            conn.commit()
            self.assertEqual(
                conn.execute(
                    "SELECT note FROM caller_pending WHERE id = 1"
                ).fetchone()[0],
                "keep-me",
            )

    def test_reused_migrator_connection_replay_idempotent(self):
        import psycopg

        with psycopg.connect(
            self._migrator_url("app_one"), autocommit=True
        ) as conn:
            first = self.mdb.migrate_app(
                namespace="app_one",
                repository_id=APP_ONE_REPO,
                migrations_dir=APP_ONE_DIR,
                migrator_connection=conn,
            )
            second = self.mdb.migrate_app(
                namespace="app_one",
                repository_id=APP_ONE_REPO,
                migrations_dir=APP_ONE_DIR,
                migrator_connection=conn,
            )
        self.assertEqual(first.applied_count, 0)
        self.assertGreaterEqual(first.skipped_count, 2)
        self.assertEqual(second.applied_count, 0)
        self.assertGreaterEqual(second.skipped_count, 2)

    def test_runtime_own_row_crud_and_cross_user_denial(self):
        import psycopg

        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        with psycopg.connect(self._runtime_url("app_one"), autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)",
                (user_a,),
            )
            conn.execute(
                "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                (user_a, "alpha-note"),
            )
            rows = conn.execute("SELECT note FROM items").fetchall()
            self.assertEqual(rows, [("alpha-note",)])
            conn.execute(
                "UPDATE items SET note = %s WHERE owner_id = %s::uuid",
                ("alpha-updated", user_a),
            )
            self.assertEqual(
                conn.execute("SELECT note FROM items").fetchone()[0],
                "alpha-updated",
            )

        with psycopg.connect(self._runtime_url("app_one"), autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)",
                (user_b,),
            )
            visible = conn.execute("SELECT count(*) FROM items").fetchone()[0]
            self.assertEqual(visible, 0)
            conn.execute(
                "UPDATE items SET note = %s WHERE owner_id = %s::uuid",
                ("stolen", user_a),
            )
            conn.execute(
                "DELETE FROM items WHERE owner_id = %s::uuid",
                (user_a,),
            )
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                    (user_a, "spoofed"),
                )
            conn.execute(
                "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                (user_b, "beta-note"),
            )

        with psycopg.connect(self._runtime_url("app_one"), autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)",
                (user_a,),
            )
            notes = {
                r[0] for r in conn.execute("SELECT note FROM items").fetchall()
            }
            self.assertEqual(notes, {"alpha-updated"})
            conn.execute(
                "DELETE FROM items WHERE owner_id = %s::uuid",
                (user_a,),
            )

    def test_cross_app_schema_ledger_ddl_and_role_spoof_denied(self):
        mig = self._migrator_url("app_one")
        run = self._runtime_url("app_one")
        self._assert_denied_sqlstate(
            mig,
            "SELECT count(*) FROM app_two.items",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "SELECT count(*) FROM app_two_control.ledger",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "CREATE TABLE app_two.evil (id int)",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "SET ROLE app_two_migrator",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "SELECT app_two_control.record_applied(%s, %s)",
            ("9999_forged", "ab" * 32),
            allowed_states={
                _SQLSTATE_INSUFFICIENT_PRIVILEGE,
                _SQLSTATE_RAISE_EXCEPTION,
            },
        )
        self._assert_denied_sqlstate(
            run,
            "SELECT count(*) FROM app_two.items",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            run,
            "SELECT app_one_control.record_applied(%s, %s)",
            ("9999_forged", "ab" * 32),
            allowed_states={
                _SQLSTATE_INSUFFICIENT_PRIVILEGE,
                _SQLSTATE_RAISE_EXCEPTION,
            },
        )
        self._assert_denied_sqlstate(
            run,
            "CREATE TABLE owned_by_runtime (id int)",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )

    def test_migrator_cannot_alter_control_or_update_ledger(self):
        mig = self._migrator_url("app_one")
        self._assert_denied_sqlstate(
            mig,
            "UPDATE app_one_control.ledger SET checksum_sha256 = repeat('ab', 32)",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "DELETE FROM app_one_control.ledger",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "INSERT INTO app_one_control.ledger "
            "(migration_id, checksum_sha256) "
            "VALUES ('9999_forged', repeat('cd', 32))",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "CREATE OR REPLACE FUNCTION app_one_control.record_applied"
            "(text, text) RETURNS void LANGUAGE sql AS $$ SELECT 1 $$",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )
        self._assert_denied_sqlstate(
            mig,
            "UPDATE app_one_control.ownership_binding "
            "SET repository_id = '999999'",
            allowed_states={_SQLSTATE_INSUFFICIENT_PRIVILEGE},
        )

    def test_checksum_and_out_of_order_refusal(self):
        import psycopg

        with psycopg.connect(self.provisioner_url, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE app_one_control.ledger
                SET checksum_sha256 = repeat('11', 32)
                WHERE migration_id = '0001_init'
                """
            )
        try:
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=APP_ONE_DIR,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(
                ctx.exception.code, self.mdb.FailureCode.MIGRATION_CHECKSUM
            )
        finally:
            with psycopg.connect(self.provisioner_url, autocommit=True) as conn:
                for migration in self.mdb.discover_migrations(APP_ONE_DIR):
                    conn.execute(
                        """
                        UPDATE app_one_control.ledger
                        SET checksum_sha256 = %s
                        WHERE migration_id = %s
                        """,
                        (migration.checksum_sha256, migration.migration_id),
                    )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0000_backfill.sql").write_text(
                "CREATE TABLE ooo_marker (id int PRIMARY KEY);\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.MIGRATION_ORDER)
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.ooo_marker')"
                    ).fetchone()[0]
                )

    def test_concurrent_advisory_locks(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_TWO_DIR)
            (tmp_path / "0003_concurrent.sql").write_text(
                "CREATE TABLE IF NOT EXISTS concurrent_marker (\n"
                "  id int PRIMARY KEY,\n"
                "  note text NOT NULL\n"
                ");\n"
                "INSERT INTO concurrent_marker (id, note) VALUES (1, 'once')\n"
                "ON CONFLICT (id) DO NOTHING;\n"
                "ALTER TABLE concurrent_marker ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE concurrent_marker FORCE ROW LEVEL SECURITY;\n",
                encoding="utf-8",
            )
            results = []
            errors = []

            def runner():
                try:
                    results.append(
                        self.mdb.migrate_app(
                            namespace="app_two",
                            repository_id=APP_TWO_REPO,
                            migrations_dir=tmp_path,
                            migrator_dsn=self._migrator_url("app_two"),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=runner) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(sum(item.applied_count for item in results), 1)
            with psycopg.connect(self.admin_url) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT count(*) FROM app_two.concurrent_marker"
                    ).fetchone()[0],
                    1,
                )

    def test_transaction_control_escapes_roll_back(self):
        import psycopg

        cases = [
            (
                "0003_commit_escape",
                "CREATE TABLE commit_escape_marker (id int PRIMARY KEY);\nCOMMIT;\n",
                "app_one.commit_escape_marker",
            ),
            (
                "0003_rollback_escape",
                "CREATE TABLE rollback_escape_marker (id int PRIMARY KEY);\nROLLBACK;\n",
                "app_one.rollback_escape_marker",
            ),
            (
                "0003_start_escape",
                "CREATE TABLE start_escape_marker (id int PRIMARY KEY);\n"
                "START TRANSACTION;\n",
                "app_one.start_escape_marker",
            ),
            (
                "0003_end_escape",
                "CREATE TABLE end_escape_marker (id int PRIMARY KEY);\nEND;\n",
                "app_one.end_escape_marker",
            ),
            (
                "0003_abort_escape",
                "CREATE TABLE abort_escape_marker (id int PRIMARY KEY);\nABORT;\n",
                "app_one.abort_escape_marker",
            ),
            (
                "0003_savepoint_escape",
                "CREATE TABLE savepoint_escape_marker (id int PRIMARY KEY);\n"
                "SAVEPOINT s1;\n",
                "app_one.savepoint_escape_marker",
            ),
            (
                "0003_prepare_escape",
                "CREATE TABLE prepare_escape_marker (id int PRIMARY KEY);\n"
                "PREPARE TRANSACTION 'mdb_escape';\n",
                "app_one.prepare_escape_marker",
            ),
            (
                "0003_proc_commit",
                "CREATE TABLE proc_commit_marker (id int PRIMARY KEY);\n"
                "CREATE PROCEDURE proc_commit_escape() LANGUAGE plpgsql AS $$\n"
                "BEGIN\n  COMMIT;\nEND;\n$$;\n"
                "CALL proc_commit_escape();\n",
                "app_one.proc_commit_marker",
            ),
        ]
        for migration_id, body, regclass in cases:
            with self.subTest(migration_id=migration_id):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
                    (tmp_path / f"{migration_id}.sql").write_text(
                        body, encoding="utf-8"
                    )
                    with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                        self.mdb.migrate_app(
                            namespace="app_one",
                            repository_id=APP_ONE_REPO,
                            migrations_dir=tmp_path,
                            migrator_dsn=self._migrator_url("app_one"),
                        )
                    self.assertEqual(
                        ctx.exception.code, self.mdb.FailureCode.MIGRATION_EXEC
                    )
                    with psycopg.connect(self.admin_url) as conn:
                        self.assertIsNone(
                            conn.execute(
                                "SELECT to_regclass(%s)", (regclass,)
                            ).fetchone()[0]
                        )
                        self.assertEqual(
                            conn.execute(
                                """
                                SELECT count(*) FROM app_one_control.ledger
                                WHERE migration_id = %s
                                """,
                                (migration_id,),
                            ).fetchone()[0],
                            0,
                        )

    def test_failure_rolls_back_partial_migration_and_redacts_traceback(self):
        import psycopg

        canary = f"password={uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_migrations(tmp_path, APP_ONE_DIR)
            (tmp_path / "0003_boom.sql").write_text(
                "CREATE TABLE boom_ok (id int);\n"
                f"DO $$ BEGIN RAISE EXCEPTION 'forced {canary}'; END $$;\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
                self.mdb.migrate_app(
                    namespace="app_one",
                    repository_id=APP_ONE_REPO,
                    migrations_dir=tmp_path,
                    migrator_dsn=self._migrator_url("app_one"),
                )
            self.assertEqual(ctx.exception.code, self.mdb.FailureCode.MIGRATION_EXEC)
            formatted = "".join(
                traceback.format_exception(
                    type(ctx.exception), ctx.exception, ctx.exception.__traceback__
                )
            )
            self.assertNotIn(canary, str(ctx.exception))
            self.assertNotIn(canary, repr(ctx.exception))
            self.assertNotIn(canary, formatted)
            with psycopg.connect(self.admin_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('app_one.boom_ok')"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT count(*) FROM app_one_control.ledger
                        WHERE migration_id = '0003_boom'
                        """
                    ).fetchone()[0],
                    0,
                )

    def test_connection_failure_redacts_dsn_canary_from_traceback(self):
        canary = f"dsn-secret-{uuid.uuid4().hex}"
        bad_dsn = (
            f"postgresql://nobody:{canary}@127.0.0.1:1/does-not-exist"
        )
        with self.assertRaises(self.mdb.ManagedDatabaseError) as ctx:
            self.mdb.provision_app(
                namespace="app_one",
                repository_id=APP_ONE_REPO,
                project_ref=APP_ONE_PROJECT,
                migrator_password="x",
                runtime_password="y",
                provisioner_dsn=bad_dsn,
            )
        self.assertEqual(ctx.exception.code, self.mdb.FailureCode.CONNECTION)
        formatted = "".join(
            traceback.format_exception(
                type(ctx.exception), ctx.exception, ctx.exception.__traceback__
            )
        )
        self.assertNotIn(canary, formatted)
        self.assertNotIn(canary, str(ctx.exception))

    def test_idempotent_rerun_and_readiness_metadata(self):
        result = self.mdb.migrate_app(
            namespace="app_one",
            repository_id=APP_ONE_REPO,
            migrations_dir=APP_ONE_DIR,
            migrator_dsn=self._migrator_url("app_one"),
        )
        self.assertEqual(result.applied_count, 0)
        self.assertGreaterEqual(result.skipped_count, 2)
        meta = self.mdb.readiness_metadata(
            namespace="app_one",
            repository_id=APP_ONE_REPO,
            provisioner_dsn=self.provisioner_url,
        )
        self.assertEqual(meta["namespace"], "app_one")
        self.assertEqual(meta["repository_id"], APP_ONE_REPO)
        self.assertEqual(meta["project_ref"], APP_ONE_PROJECT)
        self.assertGreaterEqual(len(meta["migrations"]), 2)
        for entry in meta["migrations"]:
            self.assertRegex(entry["checksum_sha256"], r"^[a-f0-9]{64}$")

    def test_preexisting_objects_preserved(self):
        import psycopg

        with psycopg.connect(self.admin_url) as conn:
            note = conn.execute(
                "SELECT note FROM legacy_other_app.private_history WHERE id = 1"
            ).fetchone()[0]
            self.assertEqual(note, "unrelated-preexisting")
            payload = conn.execute(
                "SELECT payload FROM public.preexisting_marker WHERE id = 1"
            ).fetchone()[0]
            self.assertEqual(payload, "leave-me-alone")
            grant = conn.execute(
                """
                SELECT has_table_privilege('public', 'public.preexisting_marker', 'SELECT')
                """
            ).fetchone()[0]
            self.assertTrue(grant)
            public_create = conn.execute(
                "SELECT has_schema_privilege('public', 'public', 'CREATE')"
            ).fetchone()[0]
            self.assertFalse(public_create)


if __name__ == "__main__":
    unittest.main()

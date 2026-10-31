#!/usr/bin/env python3
"""Managed database delivery: config, provider transport, custody, PG17 orchestration.

Unit/API boundary tests always run. PG17 integration uses MDB_PG_TESTS /
APP003_PG_TESTS=1 (mandatory; no soft skip under force). All fixture passwords
are generated at runtime.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "db" / "managed_database" / "sql"
APP_ONE_DIR = ROOT / "db" / "managed_database" / "fixtures" / "app_one"
PREEXISTING_SQL = (
    ROOT / "db" / "managed_database" / "fixtures" / "preexisting" / "01_seed.sql"
)
PG_IMAGE = os.environ.get("MDB_PG_IMAGE", "docker.io/library/postgres:17-alpine")
PYTHON = Path(
    os.environ.get(
        "MDB_TEST_PYTHON",
        str(
            ROOT.parent
            / "free-personal-stack-provenance"
            / ".venv"
            / "bin"
            / "python"
        ),
    )
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cfg = _load("database_delivery_config", ROOT / "scripts/database-delivery-config.py")
provider = _load(
    "supabase_management_database", ROOT / "scripts/supabase_management_database.py"
)
custody = _load("vercel_env_custody", ROOT / "scripts/vercel_env_custody.py")
delivery = _load(
    "managed_database_delivery", ROOT / "scripts/managed_database_delivery.py"
)
mdb = _load("managed_database", ROOT / "scripts/managed_database.py")
orch = _load("merge_onboarding", ROOT / "scripts/merge-onboarding.py")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _podman_available() -> bool:
    try:
        subprocess.run(
            ["podman", "image", "exists", PG_IMAGE], check=True, capture_output=True
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            subprocess.run(
                ["podman", "pull", PG_IMAGE], check=True, capture_output=True
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False


def _pg_tests_mandatory() -> bool:
    return os.environ.get("APP003_PG_TESTS") == "1" or os.environ.get(
        "MDB_DELIVERY_PG_TESTS"
    ) == "1"


def _pg_tests_enabled() -> bool:
    if _pg_tests_mandatory():
        return True
    if os.environ.get("MDB_PG_TESTS") == "0":
        return False
    if os.environ.get("MDB_PG_TESTS") == "1":
        return True
    return _podman_available()


def _dsn(user: str, password: str, port: int, db: str = "mdb") -> str:
    return provider.build_psycopg_dsn(
        host="127.0.0.1",
        port=port,
        database=db,
        user=user,
        password=password,
        sslmode="disable",
    )


def _gen_secret(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex}"


class FakeTransport:
    def __init__(self, responses: Dict[Tuple[str, str], Tuple[int, bytes]]):
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def request(self, *, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        # Match by method + path without query for simplicity.
        from urllib.parse import urlparse

        parsed = urlparse(url)
        key = (method, parsed.path)
        if key not in self.responses:
            raise AssertionError(f"unexpected request {key}")
        return self.responses[key]


def write_delivery_config(source: Path, **overrides):
    payload = {
        "schema_version": 1,
        "enabled": False,
        "auto_apply": False,
        "namespace": None,
        "migrations_path": None,
        "expected_project_ref": None,
        "rotate_migrator": False,
    }
    payload.update(overrides)
    path = source / "database-delivery.config.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def git_init_commit(source: Path) -> str:
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Offline"], cwd=source, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "offline@example.invalid"],
        cwd=source,
        check=True,
    )
    marker = source / ".gitkeep"
    if not any(source.iterdir()):
        marker.write_text("", encoding="utf-8")
    elif not list(source.glob("*")):
        marker.write_text("", encoding="utf-8")
    # Ensure at least one path exists for the initial commit.
    if not any(p.name != ".git" for p in source.iterdir()):
        marker.write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "init"], cwd=source, check=True
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()


class DeliveryConfigTests(unittest.TestCase):
    def test_template_defaults_disabled(self):
        raw = json.loads(
            (ROOT / "database-delivery.config.json").read_text(encoding="utf-8")
        )
        example = json.loads(
            (ROOT / "examples/database-delivery.config.example.json").read_text(
                encoding="utf-8"
            )
        )
        for payload in (raw, example):
            validated = cfg.validate_mapping(payload)
            self.assertIs(validated["enabled"], False)
            self.assertIs(validated["auto_apply"], False)
            self.assertIsNone(validated["namespace"])

    def test_closed_schema_rejects_unknown_and_auto_apply_without_enabled(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.validate_mapping(
                {
                    "schema_version": 1,
                    "enabled": False,
                    "auto_apply": True,
                    "namespace": None,
                    "migrations_path": None,
                    "expected_project_ref": None,
                    "rotate_migrator": False,
                }
            )
        self.assertEqual(str(ctx.exception), "config_auto_apply_requires_enabled")
        with self.assertRaises(cfg.ConfigError):
            cfg.validate_mapping(
                {
                    "schema_version": 1,
                    "enabled": False,
                    "auto_apply": False,
                    "namespace": None,
                    "migrations_path": None,
                    "expected_project_ref": None,
                    "rotate_migrator": False,
                    "extra": True,
                }
            )

    def test_enabled_requires_in_repo_migrations_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "db" / "migrations"
            mig.mkdir(parents=True)
            (mig / "0001_a.sql").write_text("select 1;\n", encoding="utf-8")
            validated = cfg.validate_mapping(
                {
                    "schema_version": 1,
                    "enabled": True,
                    "auto_apply": False,
                    "namespace": "arena_app",
                    "migrations_path": "db/migrations",
                    "expected_project_ref": "abcdefghijklmnop",
                    "rotate_migrator": False,
                },
                source=root,
            )
            self.assertEqual(validated["namespace"], "arena_app")
            with self.assertRaises(cfg.ConfigError):
                cfg.validate_mapping(
                    {
                        "schema_version": 1,
                        "enabled": True,
                        "auto_apply": False,
                        "namespace": "arena_app",
                        "migrations_path": "../outside",
                        "expected_project_ref": "abcdefghijklmnop",
                        "rotate_migrator": False,
                    },
                    source=root,
                )

    def test_missing_file_unconfigured_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README").write_text("x\n", encoding="utf-8")
            git_init_commit(root)
            loaded = cfg.load_database_delivery_config(
                root, require_tracked_clean=False
            )
            self.assertEqual(loaded["config_status"], "unconfigured")
            self.assertIs(loaded["enabled"], False)


class ProviderTransportTests(unittest.TestCase):
    def test_cli_login_normalizes_and_redacts(self):
        password = _gen_secret("cli")
        body = json.dumps(
            {"role": "cli_login_abc", "password": password, "ttl_seconds": 3600}
        ).encode()
        transport = FakeTransport(
            {
                ("POST", "/v1/projects/myrefproj1234567/cli/login-role"): (201, body),
            }
        )
        login = provider.create_cli_login_role(
            project_ref="myrefproj1234567",
            access_token=_gen_secret("tok"),
            transport=transport,
        )
        self.assertEqual(login.role, "cli_login_abc")
        self.assertEqual(login.ttl_seconds, 3600)
        self.assertIn("redacted", repr(login.password))
        self.assertNotIn(password, repr(login))
        self.assertNotIn(password, repr(login.password))
        self.assertEqual(transport.calls[0]["method"], "POST")
        self.assertNotIn("DELETE", transport.calls[0]["method"])

    def test_cli_login_maps_403_and_rejects_redirect(self):
        transport = FakeTransport(
            {
                ("POST", "/v1/projects/myrefproj1234567/cli/login-role"): (403, b"{}"),
            }
        )
        with self.assertRaises(provider.ProviderError) as ctx:
            provider.create_cli_login_role(
                project_ref="myrefproj1234567",
                access_token=_gen_secret("tok"),
                transport=transport,
            )
        self.assertEqual(str(ctx.exception), "provider_auth_or_permission_denied")
        transport2 = FakeTransport(
            {
                ("POST", "/v1/projects/myrefproj1234567/cli/login-role"): (302, b""),
            }
        )
        with self.assertRaises(provider.ProviderError) as ctx2:
            provider.create_cli_login_role(
                project_ref="myrefproj1234567",
                access_token=_gen_secret("tok"),
                transport=transport2,
            )
        self.assertEqual(str(ctx2.exception), "provider_redirect_rejected")

    def test_pooler_primary_session_validation(self):
        ref = "myrefproj1234567"
        host = "aws-0-eu-central-1.pooler.supabase.com"
        role = "postgres"
        # Include a discarded password fragment to prove we never reuse it.
        discarded = _gen_secret("discard")
        cs = (
            f"postgresql://{role}.{ref}:{quote(discarded)}@{host}:5432/postgres"
        )
        rows = [
            {
                "identifier": "primary",
                "database_type": "PRIMARY",
                "db_user": role,
                "db_host": host,
                "db_port": 5432,
                "db_name": "postgres",
                "connection_string": cs,
                "pool_mode": "session",
            }
        ]
        target = provider.normalize_primary_session_pooler(rows, project_ref=ref)
        self.assertEqual(target.host, host)
        self.assertEqual(target.port, 5432)
        self.assertEqual(target.database, "postgres")
        self.assertNotIn(discarded, repr(target))

        transaction_metadata = dict(
            rows[0], db_port=6543, pool_mode="transaction",
            connection_string=cs.replace(':5432/', ':6543/'),
        )
        session = provider.normalize_primary_session_pooler(
            [transaction_metadata], project_ref=ref)
        self.assertEqual(session.host, host)
        self.assertEqual(session.port, 5432)
        self.assertEqual(session.database, 'postgres')
        self.assertEqual(transaction_metadata['db_port'], 6543)
        self.assertEqual(transaction_metadata['pool_mode'], 'transaction')

        templated = dict(transaction_metadata, connection_string=(
            f'postgresql://{role}.{ref}:[YOUR-PASSWORD]@{host}:6543/postgres'))
        self.assertEqual(provider.normalize_primary_session_pooler(
            [templated], project_ref=ref).port, 5432)

        for invalid in (
            dict(rows[0], db_port=1234),
            dict(rows[0], db_port=5432.0),
            dict(rows[0], pool_mode='unknown'),
            dict(rows[0], connection_string=cs.replace(':5432/', ':1234/')),
            dict(rows[0], connection_string=cs.replace(':5432/', ':invalid/')),
            dict(rows[0], connection_string=cs.replace(host, host + '.invalid')),
            dict(transaction_metadata, db_host='db.other.supabase.co'),
        ):
            with self.assertRaises(provider.ProviderError):
                provider.normalize_primary_session_pooler([invalid], project_ref=ref)

        bad_ref = dict(
            rows[0],
            connection_string=(
                f"postgresql://{role}.otherref@{host}:5432/postgres"
            ),
        )
        with self.assertRaises(provider.ProviderError):
            provider.normalize_primary_session_pooler([bad_ref], project_ref=ref)

    def test_sslmode_plaintext_only_loopback(self):
        pw = _gen_secret("pw")
        with self.assertRaises(provider.ProviderError):
            provider.build_psycopg_dsn(
                host="aws-0-eu-central-1.pooler.supabase.com",
                port=5432,
                database="postgres",
                user="u",
                password=pw,
                sslmode="disable",
            )
        dsn = provider.build_psycopg_dsn(
            host="127.0.0.1",
            port=5432,
            database="postgres",
            user="u",
            password=pw,
            sslmode="disable",
        )
        self.assertIn("sslmode=disable", dsn)
        self.assertNotIn("sslrootcert", dsn)

    def test_password_injection_cannot_override_host_user_tls(self):
        from psycopg.conninfo import conninfo_to_dict

        host = "aws-0-eu-central-1.pooler.supabase.com"
        candidates = [
            _gen_secret("ws") + " host=evil sslmode=disable",
            _gen_secret("q") + " ' quoted=1",
            _gen_secret("dq") + ' "quoted=1',
            _gen_secret("bs") + r"\backslash",
            _gen_secret("eq") + "=extra",
            "postgres://u:p@evil/db " + _gen_secret("uri"),
            _gen_secret("opt") + " options=-csearch_path=evil",
        ]
        for pw in candidates:
            dsn = provider.build_psycopg_dsn(
                host=host,
                port=5432,
                database="postgres",
                user="role.myrefproj1234567",
                password=pw,
                sslmode="verify-full",
            )
            parsed = conninfo_to_dict(dsn)
            self.assertEqual(parsed["host"], host)
            self.assertEqual(parsed["user"], "role.myrefproj1234567")
            self.assertEqual(parsed["password"], pw)
            self.assertEqual(parsed["sslmode"], "verify-full")
            self.assertIn("sslrootcert", parsed)
            self.assertNotIn("options", parsed)

    def test_validate_runtime_conninfo_rejects_raw_and_wrong_binding(self):
        host = "aws-0-eu-central-1.pooler.supabase.com"
        ca = provider.resolve_system_ssl_rootcert()
        pw = _gen_secret("rt")
        good = provider.build_psycopg_dsn(
            host=host,
            port=5432,
            database="postgres",
            user="app_runtime.myrefproj1234567",
            password=pw,
            sslmode="verify-full",
            sslrootcert=ca,
        )
        validated = provider.validate_runtime_conninfo(
            good,
            expected_host=host,
            expected_port=5432,
            expected_database="postgres",
            expected_user="app_runtime.myrefproj1234567",
            expected_sslmode="verify-full",
            expected_sslrootcert=ca,
        )
        self.assertEqual(validated.password.value, pw)
        self.assertNotIn(pw, repr(validated))
        with self.assertRaises(provider.ProviderError) as ctx:
            provider.validate_runtime_conninfo(
                pw,
                expected_host=host,
                expected_port=5432,
                expected_database="postgres",
                expected_user="app_runtime.myrefproj1234567",
                expected_sslmode="verify-full",
                expected_sslrootcert=ca,
            )
        self.assertEqual(str(ctx.exception), "runtime_secret_not_conninfo")
        weakened = provider.build_psycopg_dsn(
            host="127.0.0.1",
            port=5432,
            database="postgres",
            user="app_runtime.myrefproj1234567",
            password=pw,
            sslmode="disable",
        )
        with self.assertRaises(provider.ProviderError):
            provider.validate_runtime_conninfo(
                weakened,
                expected_host=host,
                expected_port=5432,
                expected_database="postgres",
                expected_user="app_runtime.myrefproj1234567",
                expected_sslmode="verify-full",
                expected_sslrootcert=ca,
            )
        extras = good + " options=-csearch_path=evil"
        with self.assertRaises(provider.ProviderError) as ctx2:
            provider.validate_runtime_conninfo(
                extras,
                expected_host=host,
                expected_port=5432,
                expected_database="postgres",
                expected_user="app_runtime.myrefproj1234567",
                expected_sslmode="verify-full",
                expected_sslrootcert=ca,
            )
        self.assertEqual(str(ctx2.exception), "runtime_secret_unapproved_options")

    def test_verify_full_selects_system_ca(self):
        from psycopg.conninfo import conninfo_to_dict

        ca = provider.resolve_system_ssl_rootcert()
        self.assertTrue(Path(ca).is_file())
        dsn = provider.build_psycopg_dsn(
            host="aws-0-eu-central-1.pooler.supabase.com",
            port=5432,
            database="postgres",
            user="u.ref",
            password=_gen_secret("pw"),
            sslmode="verify-full",
        )
        parsed = conninfo_to_dict(dsn)
        self.assertEqual(parsed["sslmode"], "verify-full")
        self.assertEqual(parsed["sslrootcert"], ca)

    def test_oversized_and_malformed_json(self):
        transport = FakeTransport(
            {
                (
                    "GET",
                    "/v1/projects/myrefproj1234567/config/database/pooler",
                ): (200, b"{" + b"x" * (provider.MAX_RESPONSE_BYTES + 10)),
            }
        )
        with self.assertRaises(provider.ProviderError) as ctx:
            provider.fetch_primary_session_pooler(
                project_ref="myrefproj1234567",
                access_token=_gen_secret("tok"),
                transport=transport,
            )
        self.assertEqual(str(ctx.exception), "provider_response_too_large")


class VercelCustodyTests(unittest.TestCase):
    def test_ownership_comment_roundtrip(self):
        binding = custody.EnvBinding(
            repository_id="123456789",
            project_ref="myrefproj1234567",
            namespace="arena_app",
            vercel_project_id="prj_Abc123",
        )
        comment = custody.ownership_comment(binding)
        self.assertEqual(custody.parse_ownership_comment(comment), binding)

    def test_inspect_absent_reusable_recovery(self):
        binding = custody.EnvBinding(
            repository_id="123456789",
            project_ref="myrefproj1234567",
            namespace="arena_app",
            vercel_project_id="prj_Abc123",
        )
        team = "team_Abc123"
        token = _gen_secret("vercel")
        secret_value = _gen_secret("runtime")

        # absent
        transport = FakeTransport(
            {
                ("GET", "/v10/projects/prj_Abc123/env"): (
                    200,
                    json.dumps({"envs": []}).encode(),
                ),
            }
        )
        state = custody.inspect_runtime_custody(
            project_id="prj_Abc123",
            team_id=team,
            token=token,
            binding=binding,
            transport=transport,
        )
        self.assertEqual(state["status"], "absent")

        # reusable
        env_id = "env_Abc123"
        comment = custody.ownership_comment(binding)
        inventory = {
            "envs": [
                {
                    "id": env_id,
                    "key": custody.RUNTIME_ENV_KEY,
                    "type": "encrypted",
                    "target": ["production"],
                    "comment": comment,
                }
            ]
        }
        transport2 = FakeTransport(
            {
                ("GET", "/v10/projects/prj_Abc123/env"): (
                    200,
                    json.dumps(inventory).encode(),
                ),
                ("GET", f"/v1/projects/prj_Abc123/env/{env_id}"): (
                    200,
                    json.dumps({"value": secret_value}).encode(),
                ),
            }
        )
        state2 = custody.inspect_runtime_custody(
            project_id="prj_Abc123",
            team_id=team,
            token=token,
            binding=binding,
            transport=transport2,
        )
        self.assertEqual(state2["status"], "reusable")
        self.assertNotIn(secret_value, repr(state2["value"]))

        # recovery_required on binding drift
        bad = dict(inventory["envs"][0], comment="unrelated")
        transport3 = FakeTransport(
            {
                ("GET", "/v10/projects/prj_Abc123/env"): (
                    200,
                    json.dumps({"envs": [bad]}).encode(),
                ),
            }
        )
        state3 = custody.inspect_runtime_custody(
            project_id="prj_Abc123",
            team_id=team,
            token=token,
            binding=binding,
            transport=transport3,
        )
        self.assertEqual(state3["status"], "recovery_required")

    def test_create_rejects_non_encrypted_contract_via_assert(self):
        binding = custody.EnvBinding(
            repository_id="1",
            project_ref="myrefproj1234567",
            namespace="arena_app",
            vercel_project_id="prj_Abc123",
        )
        record = custody.EnvRecord(
            env_id="env_Abc123",
            key=custody.RUNTIME_ENV_KEY,
            env_type="plain",
            targets=("production",),
            comment=custody.ownership_comment(binding),
        )
        with self.assertRaises(custody.CustodyError) as ctx:
            custody.assert_compatible_runtime_record(record, binding)
        self.assertEqual(str(ctx.exception), "env_type_not_encrypted")

    def test_opaque_env_id_and_path_traversal_rejection(self):
        self.assertEqual(custody._require_env_id("dB71CxR5Qj"), "dB71CxR5Qj")
        self.assertEqual(custody._require_env_id("env_XCG7t7AIHuO2SBA8667zNUiM"),
                         "env_XCG7t7AIHuO2SBA8667zNUiM")
        for bad in ("../etc/passwd", "a/b", "a%2fb", "env_.._x", ""):
            with self.assertRaises(custody.CustodyError):
                custody._require_env_id(bad)

    def test_persist_verifies_created_envelope_and_readback(self):
        binding = custody.EnvBinding(
            repository_id="123456789",
            project_ref="myrefproj1234567",
            namespace="arena_app",
            vercel_project_id="prj_Abc123",
        )
        team = "team_Abc123"
        token = _gen_secret("vercel")
        value = _gen_secret("stored-dsn")
        env_id = "dB71CxR5Qj" + uuid.uuid4().hex[:6]
        stored = {"envs": []}

        class Transport:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse

                path = urlparse(url).path
                if method == "POST" and path.endswith("/env"):
                    payload = json.loads(body.decode())
                    row = {
                        "id": env_id,
                        "key": payload["key"],
                        "type": payload["type"],
                        "target": payload["target"],
                        "comment": payload["comment"],
                        "value": payload["value"],
                    }
                    stored["envs"] = [row]
                    # OpenAPI created envelope (not a bare echo of local password alone).
                    return 201, json.dumps({"created": row, "failed": []}).encode()
                if method == "GET" and path.endswith("/env"):
                    public = [
                        {k: v for k, v in row.items() if k != "value"}
                        for row in stored["envs"]
                    ]
                    return 200, json.dumps({"envs": public}).encode()
                if method == "GET" and f"/env/{env_id}" in path:
                    return 200, json.dumps({"value": stored["envs"][0]["value"]}).encode()
                raise AssertionError(path)

        result = custody.persist_runtime_env(
            project_id="prj_Abc123",
            team_id=team,
            token=token,
            binding=binding,
            value=value,
            transport=Transport(),
        )
        self.assertEqual(result["status"], "persisted")
        self.assertEqual(result["env_id"], env_id)
        self.assertEqual(result["value"].value, value)
        self.assertNotIn(value, repr(result["value"]))

        # Altered response metadata / failed array is failure.
        class BadTransport:
            def request(self, *, method, url, headers, body, timeout):
                return 201, json.dumps({
                    "created": [{
                        "id": env_id,
                        "key": custody.RUNTIME_ENV_KEY,
                        "type": "encrypted",
                        "target": ["production"],
                        "comment": custody.ownership_comment(binding),
                    }],
                    "failed": [{"error": {"code": "x", "key": custody.RUNTIME_ENV_KEY}}],
                }).encode()

        with self.assertRaises(custody.CustodyError) as ctx:
            custody.persist_runtime_env(
                project_id="prj_Abc123",
                team_id=team,
                token=token,
                binding=binding,
                value=value,
                transport=BadTransport(),
            )
        self.assertEqual(str(ctx.exception), "env_create_partial_or_failed")


class MergeDeliverySeamTests(unittest.TestCase):
    def test_disabled_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_delivery_config(root)
            sha = git_init_commit(root)
            calls = []

            class BoomTransport:
                def request(self, **kwargs):
                    calls.append(kwargs)
                    raise AssertionError("network")

            report = delivery.run_for_merge(
                root,
                expected_sha=sha,
                artifact_dir=root / "artifacts",
                transport=BoomTransport(),
                env={},
            )
            self.assertEqual(report["status"], "delivery_disabled")
            self.assertEqual(calls, [])
            self.assertIs(report["database_ready"], False)
            self.assertIs(report["full_app_ready"], False)

    def test_capability_matrix_documents_existing_tokens(self):
        matrix = delivery.capability_matrix()
        self.assertIn("database_write", matrix["supabase_management"]["required"])
        self.assertIn(
            "database_pooling_config_read",
            matrix["supabase_management"]["required"],
        )
        self.assertIn(
            "DELETE /v1/projects/{ref}/cli/login-role",
            matrix["supabase_management"]["never"],
        )
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", matrix["not_routine_authority"])

    def test_traceback_and_repr_redaction_canary(self):
        password = _gen_secret("canary")
        err = delivery.DeliveryError("provider_auth_or_permission_denied")
        text = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        self.assertNotIn(password, text)
        secret = delivery._Secret(password)
        self.assertNotIn(password, repr(secret))
        self.assertNotIn(password, str(secret))
        # Full exception chain with password-bearing context must sanitize.
        try:
            raise RuntimeError("boom " + password)
        except RuntimeError as exc:
            chained = delivery.sanitize_exception_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                [password],
            )
            self.assertNotIn(password, chained)
            self.assertIn("<redacted>", chained)

    def test_execute_delivery_requires_authorization_without_test_flag(self):
        with self.assertRaises(delivery.DeliveryError) as ctx:
            delivery.execute_delivery(
                source=ROOT,
                config={"namespace": "x", "migrations_path": "db", "expected_project_ref": "y", "rotate_migrator": False},
                env={},
                transport=FakeTransport({}),
                vercel_project_id="prj_Abc123",
                repository_id="123456789",
            )
        self.assertEqual(str(ctx.exception), "delivery_authorization_required")

    def test_run_for_merge_requires_enrollment_and_quality_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "db" / "migrations"
            mig.mkdir(parents=True)
            (mig / "0001_a.sql").write_text("select 1;\n", encoding="utf-8")
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=True,
                namespace="arena_app",
                migrations_path="db/migrations",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            sha = git_init_commit(root)
            env = {
                "SUPABASE_URL": "https://abcdefghijklmnop.supabase.co",
                "SUPABASE_ACCESS_TOKEN": _gen_secret("tok"),
                "VERCEL_API_TOKEN": _gen_secret("vercel"),
                "VERCEL_TEAM_ID": "team_Abc123",
            }
            report = delivery.run_for_merge(
                root,
                expected_sha=sha,
                artifact_dir=root / "artifacts",
                env=env,
                vercel_project_id="prj_Abc123",
                repository_id="123456789",
            )
            self.assertEqual(report["status"], "delivery_enrollment_binding_required")
            self.assertIs(report["database_ready"], False)
            self.assertIs(report["full_app_ready"], False)

    def test_authorized_entry_rejects_forged_ownership_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "db" / "migrations"
            mig.mkdir(parents=True)
            (mig / "0001_a.sql").write_text("select 1;\n", encoding="utf-8")
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=True,
                namespace="arena_app",
                migrations_path="db/migrations",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            sha = git_init_commit(root)
            token = _gen_secret("vercel")
            env = {
                "SUPABASE_URL": "https://abcdefghijklmnop.supabase.co",
                "SUPABASE_ACCESS_TOKEN": _gen_secret("tok"),
                "VERCEL_API_TOKEN": token,
                "VERCEL_TEAM_ID": "team_Abc123",
                "GITHUB_REPOSITORY": "Owner/Arena-App",
                "GITHUB_REPOSITORY_ID": "123456789",
                "GITHUB_SHA": sha,
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_TOKEN": _gen_secret("gh"),
            }
            calls = []

            class Transport:
                def request(self, *, method, url, headers, body, timeout):
                    calls.append(method + ":" + url)
                    if "/v9/projects/" in url and method == "GET":
                        return 200, json.dumps({
                            "id": "prj_Forged",
                            "name": "wrong-name",
                            "accountId": "team_Other",
                        }).encode()
                    raise AssertionError(url)

            quality = {
                "status": "quality_gate_passed",
                "head_sha": sha,
                "workflow_id": 1,
                "run_id": 2,
                "run_attempt": 1,
                "repository_id": "123456789",
                "repository_full_name": "Owner/Arena-App",
                "path": ".github/workflows/app-quality.yml",
                "event": "push",
                "head_branch": "main",
            }

            def fake_context(_source):
                return "123456789", "Owner/Arena-App", sha, "main"

            def fake_waiter(*_a, **_k):
                return quality

            with self.assertRaises(delivery.DeliveryError) as ctx:
                delivery.execute_authorized_delivery(
                    source=root,
                    expected_sha=sha,
                    env=env,
                    transport=Transport(),
                    vercel_project_id="prj_Forged",
                    repository_id="123456789",
                    quality_evidence=quality,
                    quality_waiter=fake_waiter,
                    github_context_fn=fake_context,
                )
            self.assertEqual(str(ctx.exception), "project_ownership_mismatch")
            self.assertTrue(any(c.startswith("GET:") for c in calls))
            self.assertFalse(any(c.startswith("POST:") for c in calls))

    def test_authorize_revalidates_forged_and_stale_quality_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=True,
                namespace="arena_app",
                migrations_path="db/migrations",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            (root / "db" / "migrations").mkdir(parents=True)
            (root / "db" / "migrations" / "0001_a.sql").write_text(
                "select 1;\n", encoding="utf-8"
            )
            sha = git_init_commit(root)
            env = {
                "SUPABASE_URL": "https://abcdefghijklmnop.supabase.co",
                "SUPABASE_ACCESS_TOKEN": _gen_secret("tok"),
                "VERCEL_API_TOKEN": _gen_secret("vercel"),
                "VERCEL_TEAM_ID": "team_Abc123",
                "GITHUB_REPOSITORY": "Owner/my_app_repo",
                "GITHUB_REPOSITORY_ID": "123456789",
                "GITHUB_SHA": sha,
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_TOKEN": _gen_secret("gh"),
            }
            custody_calls = []

            class Transport:
                def request(self, *, method, url, headers, body, timeout):
                    custody_calls.append(method)
                    raise AssertionError("no provider writes for forged quality")

            forged = {
                "status": "quality_gate_passed",
                "head_sha": sha,
                "workflow_id": 1,
                "run_id": 1,
                "run_attempt": 1,
                "repository_id": "123456789",
                "repository_full_name": "Owner/my_app_repo",
                "path": ".github/workflows/app-quality.yml",
                "event": "push",
                "head_branch": "main",
            }
            authoritative_pending = {
                "status": "quality_gate_passed",
                "head_sha": sha,
                "workflow_id": 1,
                "run_id": 99,
                "run_attempt": 2,
                "repository_id": "123456789",
                "repository_full_name": "Owner/my_app_repo",
                "path": ".github/workflows/app-quality.yml",
                "event": "push",
                "head_branch": "main",
            }

            def fake_context(_source):
                return "123456789", "Owner/my_app_repo", sha, "main"

            def waiter_rejects(*_a, **_k):
                raise delivery.load("merge-onboarding").Failure("quality_gate_failed")

            with self.assertRaises(delivery.DeliveryError) as ctx:
                delivery.authorize_delivery_context(
                    source=root,
                    expected_sha=sha,
                    repository_id="123456789",
                    vercel_project_id="prj_Abc123",
                    env=env,
                    transport=Transport(),
                    quality_evidence=forged,
                    quality_waiter=waiter_rejects,
                    github_context_fn=fake_context,
                )
            self.assertEqual(str(ctx.exception), "quality_gate_failed")
            self.assertEqual(custody_calls, [])

            def waiter_newer_success(*_a, **_k):
                return authoritative_pending

            with self.assertRaises(delivery.DeliveryError) as ctx2:
                delivery.authorize_delivery_context(
                    source=root,
                    expected_sha=sha,
                    repository_id="123456789",
                    vercel_project_id="prj_Abc123",
                    env=env,
                    transport=Transport(),
                    quality_evidence=forged,
                    quality_waiter=waiter_newer_success,
                    github_context_fn=fake_context,
                )
            self.assertEqual(str(ctx2.exception), "quality_evidence_stale_or_forged")
            self.assertEqual(custody_calls, [])

            # Canonical underscore repo name must match launcher identity.
            launch = delivery.load("launch-app")
            self.assertEqual(
                launch.canonical_vercel_project_name("my_app_repo"),
                "my-app-repo",
            )

            owned = {"envs": [{
                "key": "TEMPLATE_REPOSITORY_ID_123456789",
                "type": "encrypted",
                "target": ["production"],
                "visibility": "config",
            }]}

            class OwnedTransport:
                def request(self, *, method, url, headers, body, timeout):
                    custody_calls.append(method + ":" + url)
                    if method != "GET":
                        raise AssertionError("ownership check is read-only")
                    if "/env" in url:
                        return 200, json.dumps(owned).encode()
                    return 200, json.dumps({
                        "id": "prj_Abc123",
                        "name": "my-app-repo",
                        "accountId": "team_Abc123",
                    }).encode()

            auth = delivery.authorize_delivery_context(
                source=root,
                expected_sha=sha,
                repository_id="123456789",
                vercel_project_id="prj_Abc123",
                env=env,
                transport=OwnedTransport(),
                quality_evidence=authoritative_pending,
                quality_waiter=waiter_newer_success,
                github_context_fn=fake_context,
            )
            self.assertEqual(auth["project_name"], "my-app-repo")
            self.assertTrue(all(c.startswith("GET:") for c in custody_calls))

    def test_persist_runtime_env_reports_uncertain_after_create_readback_fail(self):
        binding = custody.EnvBinding(
            repository_id="123456789",
            project_ref="abcdefghijklmnop",
            namespace="arena_app",
            vercel_project_id="prj_Abc123",
        )
        token = _gen_secret("vercel")
        value = _gen_secret("stored-dsn")
        env_id = "dB71CxR5QjReadFail"
        stored = {"envs": [], "posted": False}

        class Transport:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse

                path = urlparse(url).path
                if method == "POST" and path.endswith("/env"):
                    payload = json.loads(body.decode())
                    row = {
                        "id": env_id,
                        "key": payload["key"],
                        "type": payload["type"],
                        "target": payload["target"],
                        "comment": payload["comment"],
                        "value": payload["value"],
                    }
                    stored["envs"] = [row]
                    stored["posted"] = True
                    return 201, json.dumps({"created": row, "failed": []}).encode()
                if method == "GET" and path.endswith("/env"):
                    # Simulate inventory lag after successful create.
                    return 200, json.dumps({"envs": []}).encode()
                raise AssertionError(path)

        with self.assertRaises(custody.CustodyError) as ctx:
            custody.persist_runtime_env(
                project_id="prj_Abc123",
                team_id="team_Abc123",
                token=token,
                binding=binding,
                value=value,
                transport=Transport(),
            )
        err = ctx.exception
        self.assertEqual(str(err), "env_persist_missing_after_create")
        self.assertEqual(err.vercel_writes_attempted, 1)
        self.assertEqual(err.vercel_writes_confirmed, 0)
        self.assertEqual(err.custody_completion, "uncertain")
        self.assertEqual(err.env_id, env_id)
        self.assertTrue(stored["posted"])
        self.assertNotIn(value, repr(err))
        self.assertNotIn(value, str(err.report_fields()))

        # Safe resume: inventory now visible, exact value matches, no password reset.
        class ResumeTransport:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse

                path = urlparse(url).path
                if method == "GET" and path.endswith("/env"):
                    public = [
                        {k: v for k, v in row.items() if k != "value"}
                        for row in stored["envs"]
                    ]
                    return 200, json.dumps({"envs": public}).encode()
                if method == "GET" and f"/env/{env_id}" in path:
                    return 200, json.dumps({"value": stored["envs"][0]["value"]}).encode()
                if method == "POST":
                    raise AssertionError("resume must not recreate")
                raise AssertionError(path)

        state = custody.inspect_runtime_custody(
            project_id="prj_Abc123",
            team_id="team_Abc123",
            token=token,
            binding=binding,
            transport=ResumeTransport(),
        )
        self.assertEqual(state["status"], "reusable")
        self.assertEqual(state["value"].value, value)

    def test_persist_uncertain_on_lost_reply_malformed_json_and_inventory(self):
        binding = custody.EnvBinding(
            repository_id="123456789",
            project_ref="abcdefghijklmnop",
            namespace="arena_app",
            vercel_project_id="prj_Abc123",
        )
        token = _gen_secret("vercel")
        value = _gen_secret("stored-dsn")
        env_id = "dB71CxR5QjLost"

        def assert_uncertain(err, code):
            self.assertEqual(str(err), code)
            self.assertEqual(err.vercel_writes_attempted, 1)
            self.assertEqual(err.vercel_writes_confirmed, 0)
            self.assertEqual(err.custody_completion, "uncertain")
            self.assertIsNotNone(err.recoverable_checkpoint)
            self.assertNotIn(value, repr(err))
            self.assertNotIn(token, repr(err))

        class LostReply:
            def request(self, *, method, url, headers, body, timeout):
                # Server persisted; transport loses the HTTP reply.
                raise TimeoutError("simulated lost reply")

        with self.assertRaises(custody.CustodyError) as ctx:
            custody.persist_runtime_env(
                project_id="prj_Abc123",
                team_id="team_Abc123",
                token=token,
                binding=binding,
                value=value,
                transport=LostReply(),
            )
        assert_uncertain(ctx.exception, "provider_transport_failed")

        class MalformedSuccess:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse
                path = urlparse(url).path
                if method == "POST" and path.endswith("/env"):
                    return 201, b"not-json{"
                raise AssertionError(path)

        with self.assertRaises(custody.CustodyError) as ctx2:
            custody.persist_runtime_env(
                project_id="prj_Abc123",
                team_id="team_Abc123",
                token=token,
                binding=binding,
                value=value,
                transport=MalformedSuccess(),
            )
        assert_uncertain(ctx2.exception, "provider_response_invalid")

        class CreateThenInventory503:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse
                path = urlparse(url).path
                if method == "POST" and path.endswith("/env"):
                    payload = json.loads(body.decode())
                    row = {
                        "id": env_id,
                        "key": payload["key"],
                        "type": payload["type"],
                        "target": payload["target"],
                        "comment": payload["comment"],
                    }
                    return 201, json.dumps({"created": row, "failed": []}).encode()
                if method == "GET" and path.endswith("/env"):
                    return 503, b"{}"
                raise AssertionError(path)

        with self.assertRaises(custody.CustodyError) as ctx3:
            custody.persist_runtime_env(
                project_id="prj_Abc123",
                team_id="team_Abc123",
                token=token,
                binding=binding,
                value=value,
                transport=CreateThenInventory503(),
            )
        assert_uncertain(ctx3.exception, "provider_unavailable")

        class CreateThenWrongReadback:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse
                path = urlparse(url).path
                if method == "POST" and path.endswith("/env"):
                    payload = json.loads(body.decode())
                    row = {
                        "id": env_id,
                        "key": payload["key"],
                        "type": payload["type"],
                        "target": payload["target"],
                        "comment": payload["comment"],
                    }
                    return 201, json.dumps({"created": row, "failed": []}).encode()
                if method == "GET" and path.endswith("/env"):
                    return 200, json.dumps({"envs": [{
                        "id": env_id,
                        "key": custody.RUNTIME_ENV_KEY,
                        "type": "encrypted",
                        "target": ["production"],
                        "comment": custody.ownership_comment(binding),
                    }]}).encode()
                if method == "GET" and f"/env/{env_id}" in path:
                    return 200, json.dumps({"value": "wrong-value"}).encode()
                raise AssertionError(path)

        with self.assertRaises(custody.CustodyError) as ctx4:
            custody.persist_runtime_env(
                project_id="prj_Abc123",
                team_id="team_Abc123",
                token=token,
                binding=binding,
                value=value,
                transport=CreateThenWrongReadback(),
            )
        assert_uncertain(ctx4.exception, "env_persist_value_mismatch")

        # Resume by readback without secret reset.
        class Resume:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse
                path = urlparse(url).path
                if method == "POST":
                    raise AssertionError("resume must not recreate")
                if method == "GET" and path.endswith("/env"):
                    return 200, json.dumps({"envs": [{
                        "id": env_id,
                        "key": custody.RUNTIME_ENV_KEY,
                        "type": "encrypted",
                        "target": ["production"],
                        "comment": custody.ownership_comment(binding),
                    }]}).encode()
                if method == "GET" and f"/env/{env_id}" in path:
                    return 200, json.dumps({"value": value}).encode()
                raise AssertionError(path)

        state = custody.inspect_runtime_custody(
            project_id="prj_Abc123",
            team_id="team_Abc123",
            token=token,
            binding=binding,
            transport=Resume(),
        )
        self.assertEqual(state["status"], "reusable")
        self.assertEqual(state["value"].value, value)

    def test_symlink_ancestor_and_ignored_extra_sql_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "db" / "migrations"
            real.mkdir(parents=True)
            (real / "0001_a.sql").write_text("select 1;\n", encoding="utf-8")
            link = root / "db" / "miglink"
            link.symlink_to(real)
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=False,
                namespace="arena_app",
                migrations_path="db/miglink",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.validate_mapping(
                    json.loads((root / "database-delivery.config.json").read_text()),
                    source=root,
                )
            self.assertEqual(str(ctx.exception), "config_migrations_symlink_forbidden")

            write_delivery_config(
                root,
                enabled=True,
                auto_apply=False,
                namespace="arena_app",
                migrations_path="db/migrations",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            (root / ".gitignore").write_text("db/migrations/9999_ignored.sql\n", encoding="utf-8")
            (real / "9999_ignored.sql").write_text("select 2;\n", encoding="utf-8")
            sha = git_init_commit(root)
            # Whole-repo porcelain can be clean while ignored SQL still exists.
            porcelain = subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True
            ).strip()
            self.assertEqual(porcelain, "")
            with self.assertRaises(cfg.ConfigError) as ctx2:
                cfg.capture_approved_migrations(
                    root, "db/migrations", expected_sha=sha
                )
            self.assertEqual(str(ctx2.exception), "config_migrations_extra_sql")

    def test_captured_migration_bytes_survive_post_preflight_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "db" / "migrations"
            mig.mkdir(parents=True)
            original = "SELECT 1;\n"
            mutated = "SELECT 2;\n"
            (mig / "0001_a.sql").write_text(original, encoding="utf-8")
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=False,
                namespace="arena_app",
                migrations_path="db/migrations",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            sha = git_init_commit(root)
            manifest = cfg.capture_approved_migrations(
                root, "db/migrations", expected_sha=sha
            )
            self.assertEqual(manifest[0]["sql_bytes"], original.encode())
            (mig / "0001_a.sql").write_text(mutated, encoding="utf-8")
            migrations = mdb.migrations_from_captured_manifest(manifest)
            self.assertEqual(migrations[0].sql_bytes, original.encode())
            self.assertNotEqual(migrations[0].sql_bytes, mutated.encode())

    def test_capture_uses_approved_tree_not_index_and_distinct_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "db" / "migrations"
            mig.mkdir(parents=True)
            a_bytes = b"SELECT 'a';\n"
            b_bytes = b"SELECT 'b';\n"
            (mig / "0001_a.sql").write_bytes(a_bytes)
            (mig / "0002_b.sql").write_bytes(b_bytes)
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=False,
                namespace="arena_app",
                migrations_path="db/migrations",
                expected_project_ref="abcdefghijklmnop",
                rotate_migrator=False,
            )
            sha_a = git_init_commit(root)
            manifest = cfg.capture_approved_migrations(
                root, "db/migrations", expected_sha=sha_a
            )
            self.assertEqual(
                [item["relative_path"] for item in manifest],
                ["db/migrations/0001_a.sql", "db/migrations/0002_b.sql"],
            )
            self.assertEqual(
                {item["relative_path"] for item in manifest},
                {"db/migrations/0001_a.sql", "db/migrations/0002_b.sql"},
            )
            self.assertEqual(manifest[0]["sql_bytes"], a_bytes)
            self.assertEqual(manifest[1]["sql_bytes"], b_bytes)
            self.assertNotEqual(manifest[0]["git_sha"], manifest[1]["git_sha"])
            self.assertEqual(manifest[0]["mode"], "100644")
            self.assertEqual(manifest[1]["mode"], "100644")

            # Stage replacement B without committing — approved tree stays A.
            mutated = b"SELECT 'mutated-index';\n"
            (mig / "0001_a.sql").write_bytes(mutated)
            subprocess.run(
                ["git", "add", "db/migrations/0001_a.sql"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            index_blob = subprocess.check_output(
                ["git", "ls-files", "-s", "--", "db/migrations/0001_a.sql"],
                cwd=root,
                text=True,
            ).split()[1]
            tree_blob = subprocess.check_output(
                ["git", "ls-tree", sha_a, "--", "db/migrations/0001_a.sql"],
                cwd=root,
                text=True,
            ).split()[2]
            self.assertNotEqual(index_blob, tree_blob)
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.capture_approved_migrations(
                    root, "db/migrations", expected_sha=sha_a
                )
            self.assertEqual(str(ctx.exception), "config_migrations_working_tree_mismatch")

            # Extra staged SQL absent from approved tree must never be captured.
            subprocess.run(
                ["git", "checkout", "--", "db/migrations/0001_a.sql"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (mig / "0003_extra.sql").write_text("SELECT 3;\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "db/migrations/0003_extra.sql"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            with self.assertRaises(cfg.ConfigError) as ctx2:
                cfg.capture_approved_migrations(
                    root, "db/migrations", expected_sha=sha_a
                )
            self.assertEqual(str(ctx2.exception), "config_migrations_extra_sql")

    def test_git_dir_override_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_delivery_config(root)
            sha = git_init_commit(root)
            foreign = Path(tempfile.mkdtemp(prefix="foreign-git-"))
            subprocess.run(["git", "init"], cwd=foreign, check=True, capture_output=True)
            old_dir = os.environ.get("GIT_DIR")
            old_tree = os.environ.get("GIT_WORK_TREE")
            os.environ["GIT_DIR"] = str(foreign / ".git")
            os.environ["GIT_WORK_TREE"] = str(foreign)
            try:
                cleaned = cfg._git_env()
                self.assertNotIn("GIT_DIR", cleaned)
                self.assertNotIn("GIT_WORK_TREE", cleaned)
                head = cfg.assert_tracked_clean_head(root, expected_sha=sha)
                self.assertEqual(head, sha)
            finally:
                if old_dir is None:
                    os.environ.pop("GIT_DIR", None)
                else:
                    os.environ["GIT_DIR"] = old_dir
                if old_tree is None:
                    os.environ.pop("GIT_WORK_TREE", None)
                else:
                    os.environ["GIT_WORK_TREE"] = old_tree


@unittest.skipUnless(_pg_tests_enabled(), "disposable local PostgreSQL 17 unavailable")
class DeliveryPgIntegrationTests(unittest.TestCase):
    container_name: str
    port: int
    super_password: str
    cli_password: str
    trusted_password: str
    admin_url: str
    cli_url: str

    @classmethod
    def setUpClass(cls):
        if _pg_tests_mandatory() and not _podman_available():
            raise RuntimeError("podman/postgres:17 required under APP003_PG_TESTS=1")
        cls.super_password = _gen_secret("super")
        cls.cli_password = _gen_secret("cli")
        cls.trusted_password = _gen_secret("trusted")
        cls.direct_password = _gen_secret("direct")
        cls.port = _free_loopback_port()
        cls.container_name = f"mdb-deliv-{uuid.uuid4().hex[:12]}"
        cls.admin_url = _dsn("postgres", cls.super_password, cls.port)
        cls.cli_url = _dsn("mdb_cli_login", cls.cli_password, cls.port)
        cls.direct_url = _dsn("mdb_direct_prov", cls.direct_password, cls.port)
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
            cls._bootstrap()
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def _cleanup(cls):
        subprocess.run(
            ["podman", "rm", "-f", cls.container_name], capture_output=True
        )

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    @classmethod
    def _wait_ready(cls):
        import psycopg

        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with psycopg.connect(cls.admin_url, connect_timeout=2) as conn:
                    version = conn.execute("SHOW server_version").fetchone()[0]
                    if not version.startswith("17"):
                        raise RuntimeError(version)
                return
            except Exception:
                time.sleep(0.5)
        msg = "postgres 17 not ready"
        if _pg_tests_mandatory():
            raise RuntimeError(msg)
        raise unittest.SkipTest(msg)

    @classmethod
    def _bootstrap(cls):
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
            # Trusted delegated root: NOSUPERUSER CREATEROLE with BYPASSRLS.
            conn.execute(
                psql.SQL(
                    "CREATE ROLE mdb_trusted NOLOGIN NOSUPERUSER CREATEROLE "
                    "BYPASSRLS NOCREATEDB NOREPLICATION"
                )
            )
            conn.execute(
                "GRANT CREATE, CONNECT, TEMPORARY ON DATABASE mdb TO mdb_trusted"
            )
            conn.execute(
                psql.SQL(
                    "CREATE ROLE mdb_cli_login LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEROLE NOBYPASSRLS NOCREATEDB NOREPLICATION"
                ).format(psql.Literal(cls.cli_password))
            )
            conn.execute(
                "GRANT CONNECT, TEMPORARY ON DATABASE mdb TO mdb_cli_login"
            )
            conn.execute(
                "GRANT mdb_trusted TO mdb_cli_login WITH SET TRUE, INHERIT FALSE"
            )
            # Genuine non-BYPASS direct bootstrap (no dependence on elevated bypass).
            conn.execute(
                psql.SQL(
                    "CREATE ROLE mdb_direct_prov LOGIN PASSWORD {} "
                    "NOSUPERUSER CREATEROLE NOBYPASSRLS NOCREATEDB NOREPLICATION"
                ).format(psql.Literal(cls.direct_password))
            )
            conn.execute(
                "GRANT CREATE, CONNECT, TEMPORARY ON DATABASE mdb TO mdb_direct_prov"
            )
            if PREEXISTING_SQL.is_file():
                conn.pgconn.exec_(PREEXISTING_SQL.read_bytes())

    def test_delegated_trusted_provisioner_uses_current_user(self):
        import psycopg

        mig = _gen_secret("mig")
        run = _gen_secret("run")
        with psycopg.connect(self.cli_url, autocommit=True) as conn:
            session_before = conn.execute("SELECT session_user").fetchone()[0]
            self.assertEqual(session_before, "mdb_cli_login")
            delivery.assume_trusted_provisioner(conn, role="mdb_trusted")
            current = conn.execute("SELECT current_user").fetchone()[0]
            session = conn.execute("SELECT session_user").fetchone()[0]
            self.assertEqual(current, "mdb_trusted")
            self.assertEqual(session, "mdb_cli_login")
            result = mdb.provision_app(
                namespace="app_one",
                repository_id="1000000001",
                project_ref="delivref00000001",
                migrator_password=mig,
                runtime_password=run,
                provisioner_connection=conn,
                expected_provisioner_role="mdb_trusted",
                allow_trusted_bootstrap_bypassrls=True,
            )
            self.assertTrue(result.created)
            binding = conn.execute(
                "SELECT provisioner_role FROM app_one_control.ownership_binding"
            ).fetchone()[0]
            self.assertEqual(binding, "mdb_trusted")

        # Migrator LOGIN (not admin SET ROLE) applies migrations.
        mig_url = _dsn("app_one_migrator", mig, self.port)
        migrated = mdb.migrate_app(
            namespace="app_one",
            repository_id="1000000001",
            migrations_dir=APP_ONE_DIR,
            migrator_dsn=mig_url,
        )
        self.assertGreaterEqual(migrated.applied_count, 1)

        # Runtime RLS smoke with independent SQLSTATE checks (never 25P02 alone).
        run_url = _dsn("app_one_runtime", run, self.port)
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        with psycopg.connect(run_url, autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)", (user_a,)
            )
            conn.execute(
                "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                (user_a, "a"),
            )
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)", (user_b,)
            )
            denied_states = []
            try:
                conn.execute(
                    "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                    (user_a, "steal"),
                )
                self.fail("expected RLS denial")
            except Exception as exc:
                state = getattr(exc, "sqlstate", None)
                denied_states.append(state)
                self.assertNotEqual(state, "25P02")
            visible = conn.execute("SELECT count(*) FROM items").fetchone()[0]
            self.assertEqual(visible, 0)
            self.assertTrue(denied_states)

    def test_direct_non_bypass_bootstrap_still_works(self):
        mig = _gen_secret("mig2")
        run = _gen_secret("run2")
        result = mdb.provision_app(
            namespace="deliv_direct",
            repository_id="1000000002",
            project_ref="delivref00000002",
            migrator_password=mig,
            runtime_password=run,
            provisioner_dsn=self.direct_url,
        )
        self.assertTrue(result.created)
        with self.assertRaises(mdb.ManagedDatabaseError) as ctx:
            # Delegated session without binding must fail closed.
            import psycopg

            with psycopg.connect(self.cli_url, autocommit=True) as conn:
                delivery.assume_trusted_provisioner(conn, role="mdb_trusted")
                mdb.provision_app(
                    namespace="deliv_unbound",
                    repository_id="1000000003",
                    project_ref="delivref00000003",
                    migrator_password=_gen_secret("m"),
                    runtime_password=_gen_secret("r"),
                    provisioner_connection=conn,
                )
        self.assertEqual(ctx.exception.code, mdb.FailureCode.AUTH_DENIED)

    def _custody_transport(self, stored: Dict[str, Any]):
        class CustodyTransport:
            def request(self, *, method, url, headers, body, timeout):
                from urllib.parse import urlparse

                path = urlparse(url).path
                if method == "GET" and path.endswith("/env"):
                    public = [
                        {k: v for k, v in row.items() if k != "value"}
                        for row in stored["envs"]
                    ]
                    return 200, json.dumps({"envs": public}).encode()
                if method == "POST" and path.endswith("/env"):
                    payload = json.loads(body.decode())
                    if any(e["key"] == payload["key"] for e in stored["envs"]):
                        return 409, b"{}"
                    env_id = "dB71" + uuid.uuid4().hex[:10]
                    row = {
                        "id": env_id,
                        "key": payload["key"],
                        "type": payload["type"],
                        "target": payload["target"],
                        "comment": payload["comment"],
                        "value": payload["value"],
                    }
                    stored["envs"].append(row)
                    return 201, json.dumps({"created": row, "failed": []}).encode()
                if method == "GET" and "/env/" in path:
                    env_id = path.rsplit("/", 1)[-1]
                    for row in stored["envs"]:
                        if row["id"] == env_id:
                            return 200, json.dumps({"value": row["value"]}).encode()
                    return 404, b"{}"
                raise AssertionError(path)

        return CustodyTransport()

    def test_execute_delivery_catalog_before_custody_and_no_runtime_reset(self):
        import psycopg

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig_dir = root / "db" / "migrations"
            mig_dir.mkdir(parents=True)
            (mig_dir / "0001_marker.sql").write_text(
                """
CREATE TABLE IF NOT EXISTS app_marker (
  app_name text PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE app_marker ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_marker FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_marker_runtime_select ON app_marker;
CREATE POLICY app_marker_runtime_select ON app_marker
  FOR SELECT TO deliv_flow_runtime USING (true);
GRANT SELECT ON app_marker TO deliv_flow_runtime;
""".strip()
                + "\n",
                encoding="utf-8",
            )
            ref = "delivref00000004"
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=True,
                namespace="deliv_flow",
                migrations_path="db/migrations",
                expected_project_ref=ref,
                rotate_migrator=False,
            )
            sha = git_init_commit(root)
            config = cfg.load_database_delivery_config(
                root, require_tracked_clean=True, expected_sha=sha
            )
            repo_id = "1000000004"
            project_id = "prj_DelivFlow1"
            team_id = "team_DelivFlow1"
            binding = custody.EnvBinding(
                repository_id=repo_id,
                project_ref=ref,
                namespace="deliv_flow",
                vercel_project_id=project_id,
            )
            stored: Dict[str, Any] = {"envs": []}
            artifact_root = Path(tempfile.mkdtemp(prefix="mdb-deliv-art-"))
            transport = self._custody_transport(stored)

            pooler = provider.SessionPoolerTarget(
                project_ref=ref,
                host="127.0.0.1",
                port=self.port,
                database="mdb",
                db_user_suffix=ref,
            )
            login = provider.CliLoginRole(
                role="mdb_cli_login",
                password=provider._Secret(self.cli_password),
                ttl_seconds=600,
            )
            env = {
                "SUPABASE_URL": f"https://{ref}.supabase.co",
                "SUPABASE_ACCESS_TOKEN": _gen_secret("mgmt"),
                "VERCEL_API_TOKEN": _gen_secret("vercel"),
                "VERCEL_TEAM_ID": team_id,
            }

            report = delivery.execute_delivery(
                source=root,
                config=config,
                env=env,
                transport=transport,
                vercel_project_id=project_id,
                repository_id=repo_id,
                loopback_fixture=True,
                allow_test_orchestration=True,
                expected_sha=sha,
                pooler_override=pooler,
                cli_login_override=login,
                provisioner_role="mdb_trusted",
                artifact_dir=artifact_root / "artifacts",
            )
            self.assertEqual(report["status"], "delivery_completed_local_runtime_proof")
            self.assertTrue(report["runtime_env_created"])
            self.assertTrue(report["provision_created"])
            self.assertIn("catalog_probe", report["call_order"])
            self.assertLess(
                report["call_order"].index("catalog_probe"),
                report["call_order"].index("custody_persist"),
            )
            self.assertEqual(len(stored["envs"]), 1)
            first_value = stored["envs"][0]["value"]
            self.assertIs(report["database_ready"], False)
            self.assertIs(report["full_app_ready"], False)

            # Repeat: no runtime reset; migrator requires explicit rotation flag.
            report2 = delivery.execute_delivery(
                source=root,
                config=config,
                env=env,
                transport=transport,
                vercel_project_id=project_id,
                repository_id=repo_id,
                loopback_fixture=True,
                allow_test_orchestration=True,
                expected_sha=sha,
                pooler_override=pooler,
                cli_login_override=login,
                provisioner_role="mdb_trusted",
                artifact_dir=artifact_root / "artifacts2",
            )
            self.assertEqual(
                report2["status"],
                "migrator_credential_job_only_requires_rotation_flag",
            )
            self.assertEqual(stored["envs"][0]["value"], first_value)
            self.assertFalse(report2.get("runtime_env_created", False))
            self.assertIn("runtime_auth_before_mutation", report2["call_order"])

            # recovery_required when env exists but undecryptable / wrong binding
            stored["envs"][0]["comment"] = "broken"
            state = custody.inspect_runtime_custody(
                project_id=project_id,
                team_id=team_id,
                token=env["VERCEL_API_TOKEN"],
                binding=binding,
                transport=transport,
            )
            self.assertEqual(state["status"], "recovery_required")

            # Unrelated second app + grants preserved under delegated path.
            with psycopg.connect(self.direct_url, autocommit=True) as conn:
                mdb.provision_app(
                    namespace="deliv_other",
                    repository_id="1000000005",
                    project_ref="delivref00000005",
                    migrator_password=_gen_secret("om"),
                    runtime_password=_gen_secret("or"),
                    provisioner_connection=conn,
                )
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                self.assertTrue(
                    conn.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='deliv_flow')"
                    ).fetchone()[0]
                )
                self.assertTrue(
                    conn.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='deliv_other')"
                    ).fetchone()[0]
                )

    def test_owned_app_missing_custody_is_recovery_without_writes(self):
        import psycopg

        ns = "deliv_recov"
        ref = "delivref00000006"
        repo_id = "1000000006"
        mig = _gen_secret("mig")
        run = _gen_secret("run")
        with psycopg.connect(self.cli_url, autocommit=True) as conn:
            delivery.assume_trusted_provisioner(conn, role="mdb_trusted")
            mdb.provision_app(
                namespace=ns,
                repository_id=repo_id,
                project_ref=ref,
                migrator_password=mig,
                runtime_password=run,
                provisioner_connection=conn,
                expected_provisioner_role="mdb_trusted",
                allow_trusted_bootstrap_bypassrls=True,
            )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig_dir = root / "db" / "migrations"
            mig_dir.mkdir(parents=True)
            (mig_dir / "0001_a.sql").write_text("select 1;\n", encoding="utf-8")
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=True,
                namespace=ns,
                migrations_path="db/migrations",
                expected_project_ref=ref,
                rotate_migrator=True,
            )
            sha = git_init_commit(root)
            config = cfg.load_database_delivery_config(
                root, require_tracked_clean=True, expected_sha=sha
            )
            stored: Dict[str, Any] = {"envs": []}
            posts: List[str] = []
            inner = self._custody_transport(stored)

            class CountingTransport:
                def request(self, *, method, url, headers, body, timeout):
                    if method == "POST":
                        posts.append(url)
                    return inner.request(
                        method=method,
                        url=url,
                        headers=headers,
                        body=body,
                        timeout=timeout,
                    )

            pooler = provider.SessionPoolerTarget(
                project_ref=ref,
                host="127.0.0.1",
                port=self.port,
                database="mdb",
                db_user_suffix=ref,
            )
            login = provider.CliLoginRole(
                role="mdb_cli_login",
                password=provider._Secret(self.cli_password),
                ttl_seconds=600,
            )
            env = {
                "SUPABASE_URL": f"https://{ref}.supabase.co",
                "SUPABASE_ACCESS_TOKEN": _gen_secret("mgmt"),
                "VERCEL_API_TOKEN": _gen_secret("vercel"),
                "VERCEL_TEAM_ID": "team_DelivRecov1",
            }
            report = delivery.execute_delivery(
                source=root,
                config=config,
                env=env,
                transport=CountingTransport(),
                vercel_project_id="prj_DelivRecov1",
                repository_id=repo_id,
                loopback_fixture=True,
                allow_test_orchestration=True,
                expected_sha=sha,
                pooler_override=pooler,
                cli_login_override=login,
                provisioner_role="mdb_trusted",
            )
            self.assertEqual(report["status"], "recovery_required")
            self.assertEqual(report.get("reason"), "owned_app_missing_runtime_custody")
            self.assertEqual(posts, [])
            self.assertEqual(stored["envs"], [])
            # Runtime password unchanged (still authenticates).
            with psycopg.connect(_dsn(f"{ns}_runtime", run, self.port), autocommit=True) as conn:
                self.assertEqual(
                    conn.execute("SELECT current_user").fetchone()[0], f"{ns}_runtime"
                )

    def test_stale_runtime_credential_blocks_rotation(self):
        import psycopg

        ns = "deliv_stale"
        ref = "delivref00000007"
        repo_id = "1000000007"
        mig = _gen_secret("mig")
        run = _gen_secret("run")
        with psycopg.connect(self.cli_url, autocommit=True) as conn:
            delivery.assume_trusted_provisioner(conn, role="mdb_trusted")
            mdb.provision_app(
                namespace=ns,
                repository_id=repo_id,
                project_ref=ref,
                migrator_password=mig,
                runtime_password=run,
                provisioner_connection=conn,
                expected_provisioner_role="mdb_trusted",
                allow_trusted_bootstrap_bypassrls=True,
            )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig_dir = root / "db" / "migrations"
            mig_dir.mkdir(parents=True)
            (mig_dir / "0001_a.sql").write_text("select 1;\n", encoding="utf-8")
            write_delivery_config(
                root,
                enabled=True,
                auto_apply=True,
                namespace=ns,
                migrations_path="db/migrations",
                expected_project_ref=ref,
                rotate_migrator=True,
            )
            sha = git_init_commit(root)
            config = cfg.load_database_delivery_config(
                root, require_tracked_clean=True, expected_sha=sha
            )
            project_id = "prj_DelivStale1"
            team_id = "team_DelivStale1"
            binding = custody.EnvBinding(
                repository_id=repo_id,
                project_ref=ref,
                namespace=ns,
                vercel_project_id=project_id,
            )
            stale_pw = _gen_secret("stale")
            stale_dsn = provider.build_psycopg_dsn(
                host="127.0.0.1",
                port=self.port,
                database="mdb",
                user=f"{ns}_runtime",
                password=stale_pw,
                sslmode="disable",
            )
            stored = {
                "envs": [
                    {
                        "id": "dB71stale01",
                        "key": custody.RUNTIME_ENV_KEY,
                        "type": "encrypted",
                        "target": ["production"],
                        "comment": custody.ownership_comment(binding),
                        "value": stale_dsn,
                    }
                ]
            }
            pooler = provider.SessionPoolerTarget(
                project_ref=ref,
                host="127.0.0.1",
                port=self.port,
                database="mdb",
                db_user_suffix=ref,
            )
            login = provider.CliLoginRole(
                role="mdb_cli_login",
                password=provider._Secret(self.cli_password),
                ttl_seconds=600,
            )
            env = {
                "SUPABASE_URL": f"https://{ref}.supabase.co",
                "SUPABASE_ACCESS_TOKEN": _gen_secret("mgmt"),
                "VERCEL_API_TOKEN": _gen_secret("vercel"),
                "VERCEL_TEAM_ID": team_id,
            }
            report = delivery.execute_delivery(
                source=root,
                config=config,
                env=env,
                transport=self._custody_transport(stored),
                vercel_project_id=project_id,
                repository_id=repo_id,
                loopback_fixture=True,
                allow_test_orchestration=True,
                expected_sha=sha,
                pooler_override=pooler,
                cli_login_override=login,
                provisioner_role="mdb_trusted",
            )
            self.assertEqual(report["status"], "recovery_required")
            self.assertEqual(report.get("password_writes"), 0)
            self.assertEqual(report.get("ddl_writes"), 0)
            # Original runtime password still works; stale never applied.
            with psycopg.connect(_dsn(f"{ns}_runtime", run, self.port), autocommit=True) as conn:
                self.assertEqual(
                    conn.execute("SELECT current_user").fetchone()[0], f"{ns}_runtime"
                )

    def test_set_owned_login_password_enforces_invariants(self):
        import psycopg

        ns = "deliv_pwd"
        ref = "delivref00000008"
        repo_id = "1000000008"
        mig = _gen_secret("mig")
        run = _gen_secret("run")
        with psycopg.connect(self.cli_url, autocommit=True) as conn:
            delivery.assume_trusted_provisioner(conn, role="mdb_trusted")
            mdb.provision_app(
                namespace=ns,
                repository_id=repo_id,
                project_ref=ref,
                migrator_password=mig,
                runtime_password=run,
                provisioner_connection=conn,
                expected_provisioner_role="mdb_trusted",
                allow_trusted_bootstrap_bypassrls=True,
            )
            # Wrong project_ref rejected before password change.
            with self.assertRaises(mdb.ManagedDatabaseError) as ctx:
                mdb.set_owned_login_password(
                    namespace=ns,
                    repository_id=repo_id,
                    project_ref="wrongref00000001",
                    role_kind="migrator",
                    new_password=_gen_secret("new"),
                    provisioner_connection=conn,
                    expected_provisioner_role="mdb_trusted",
                    allow_trusted_bootstrap_bypassrls=True,
                )
            self.assertEqual(ctx.exception.code, mdb.FailureCode.OWNERSHIP_MISMATCH)
            # Active transaction rejected.
            conn.execute("BEGIN")
            try:
                with self.assertRaises(mdb.ManagedDatabaseError) as ctx2:
                    mdb.set_owned_login_password(
                        namespace=ns,
                        repository_id=repo_id,
                        project_ref=ref,
                        role_kind="migrator",
                        new_password=_gen_secret("new"),
                        provisioner_connection=conn,
                        expected_provisioner_role="mdb_trusted",
                        allow_trusted_bootstrap_bypassrls=True,
                    )
                self.assertEqual(ctx2.exception.code, mdb.FailureCode.ACTIVE_TRANSACTION)
            finally:
                conn.execute("ROLLBACK")

    def test_delivery_runtime_rls_two_apps(self):
        """Delivery-generated credentials: own CRUD + cross-user/app denials."""
        import psycopg

        items_sql = """
CREATE TABLE IF NOT EXISTS items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL,
  note text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS items_select_own ON items;
CREATE POLICY items_select_own ON items
  FOR SELECT TO {runtime}
  USING (owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), ''));
DROP POLICY IF EXISTS items_insert_own ON items;
CREATE POLICY items_insert_own ON items
  FOR INSERT TO {runtime}
  WITH CHECK (owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), ''));
DROP POLICY IF EXISTS items_update_own ON items;
CREATE POLICY items_update_own ON items
  FOR UPDATE TO {runtime}
  USING (owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), ''))
  WITH CHECK (owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), ''));
DROP POLICY IF EXISTS items_delete_own ON items;
CREATE POLICY items_delete_own ON items
  FOR DELETE TO {runtime}
  USING (owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), ''));
GRANT SELECT, INSERT, UPDATE, DELETE ON items TO {runtime};
""".strip()

        results = []
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for ns, repo, pref in (
                ("rls_one", "1000000091", "delivref00000091"),
                ("rls_two", "1000000092", "delivref00000092"),
            ):
                mig_dir = base / ns
                mig_dir.mkdir()
                (mig_dir / "0001_items.sql").write_text(
                    items_sql.format(runtime=f"{ns}_runtime") + "\n",
                    encoding="utf-8",
                )
                mig = _gen_secret(ns + "m")
                run = _gen_secret(ns + "r")
                with psycopg.connect(self.cli_url, autocommit=True) as conn:
                    delivery.assume_trusted_provisioner(conn, role="mdb_trusted")
                    mdb.provision_app(
                        namespace=ns,
                        repository_id=repo,
                        project_ref=pref,
                        migrator_password=mig,
                        runtime_password=run,
                        provisioner_connection=conn,
                        expected_provisioner_role="mdb_trusted",
                        allow_trusted_bootstrap_bypassrls=True,
                    )
                mdb.migrate_app(
                    namespace=ns,
                    repository_id=repo,
                    migrations_dir=mig_dir,
                    migrator_dsn=_dsn(f"{ns}_migrator", mig, self.port),
                )
                results.append((ns, run))

        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        run_a = results[0][1]
        with psycopg.connect(_dsn("rls_one_runtime", run_a, self.port), autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)", (user_a,)
            )
            conn.execute(
                "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                (user_a, "a"),
            )
            conn.execute(
                "UPDATE items SET note=%s WHERE owner_id=%s::uuid",
                ("a2", user_a),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT note FROM items WHERE owner_id=%s::uuid", (user_a,)
                ).fetchone()[0],
                "a2",
            )
            conn.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, false)", (user_b,)
            )
            try:
                conn.execute(
                    "INSERT INTO items (owner_id, note) VALUES (%s::uuid, %s)",
                    (user_a, "steal"),
                )
                self.fail("expected cross-user denial")
            except Exception as exc:
                self.assertNotEqual(getattr(exc, "sqlstate", None), "25P02")
                self.assertIsNotNone(getattr(exc, "sqlstate", None))
            self.assertEqual(conn.execute("SELECT count(*) FROM items").fetchone()[0], 0)
            try:
                conn.execute("SELECT count(*) FROM rls_two.items")
                self.fail("expected cross-app denial")
            except Exception as exc:
                self.assertNotEqual(getattr(exc, "sqlstate", None), "25P02")
                self.assertIsNotNone(getattr(exc, "sqlstate", None))
            try:
                conn.execute("CREATE TABLE evil (id int)")
                self.fail("expected DDL denial")
            except Exception as exc:
                self.assertNotEqual(getattr(exc, "sqlstate", None), "25P02")
                self.assertIsNotNone(getattr(exc, "sqlstate", None))
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            self.assertTrue(
                conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='legacy_other_app')"
                ).fetchone()[0]
            )
            self.assertEqual(
                conn.execute(
                    "SELECT note FROM legacy_other_app.private_history WHERE id=1"
                ).fetchone()[0],
                "unrelated-preexisting",
            )


class SecretArtifactCanaryTests(unittest.TestCase):
    def test_modules_have_no_literal_password_assignments(self):
        # GitGuardian / canary: delivery modules must not embed fixture passwords.
        pattern = re.compile(
            r"""(?i)password\s*=\s*['"](?!.*\{)[A-Za-z0-9+/=_\-]{12,}['"]"""
        )
        for rel in (
            "scripts/managed_database_delivery.py",
            "scripts/supabase_management_database.py",
            "scripts/vercel_env_custody.py",
            "tests/managed_database_delivery_test.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            match = pattern.search(text)
            self.assertIsNone(match, msg=f"{rel}: {match.group(0) if match else ''}")

    def test_workflow_uses_runner_temp_venv_not_pip_user(self):
        wf = (ROOT / ".github/workflows/merge-onboarding.yml").read_text()
        self.assertIn("database-delivery-config.py --workflow-deps-required", wf)
        self.assertIn('python3 -m venv "$RUNNER_TEMP/mdb-delivery-venv"', wf)
        self.assertIn("requirements-bootstrap.txt", wf)
        self.assertNotIn("pip install --user", wf)
        self.assertIn("MDB_DELIVERY_INTERPRETER_FILE", wf)
        self.assertIn('INTERPRETER_FILE="$RUNNER_TEMP/mdb-delivery-interpreter"', wf)
        self.assertIn('MDB_PY="$(tr -d \'\\n\' < "$MDB_DELIVERY_INTERPRETER_FILE")"', wf)
        self.assertIn("$MDB_PY", wf)
        self.assertNotIn("${MDB_DELIVERY_PYTHON:-python3}", wf)
        self.assertIn("current migration workflow", (ROOT / "AGENTS.md").read_text())
        self.assertIn(
            "explicitly disables live migrations",
            (ROOT / "docs/end-to-end-onboarding.md").read_text(),
        )


if __name__ == "__main__":
    unittest.main()

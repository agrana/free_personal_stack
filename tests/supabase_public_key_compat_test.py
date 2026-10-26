#!/usr/bin/env python3
"""Regression: modern publishable + legacy anon coexistence and fail-closed screening.

Fixtures (including JWT-shaped values) are generated at runtime — no static
key literals or secret-scanner exemptions. Does not prove live project binding.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _runtime_jwt(*, role: str, seed: str, ref: Optional[str] = None) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"role": role, "iss": "supabase-fixture", "ref": ref if ref is not None else seed},
            separators=(",", ":"),
        ).encode()
    )
    sig = _b64url(hashlib.sha256(f"{seed}:{role}".encode()).digest())
    return f"{header}.{payload}.{sig}"


def _runtime_publishable(seed: str) -> str:
    digest = hashlib.sha256(f"opaque-public:{seed}".encode()).hexdigest()
    return f"sb_publishable_{digest}"


def _runtime_opaque_secret(seed: str) -> str:
    digest = hashlib.sha256(f"opaque-secret:{seed}".encode()).hexdigest()
    return f"sb_secret_{digest}"


def _load_preflight():
    import sys

    path = REPO_ROOT / "scripts" / "secrets-preflight.py"
    spec = importlib.util.spec_from_file_location("secrets_preflight_compat", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
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
    }


def _node_classify(value: str) -> str:
    script = (
        "const m=require('./app/lib/supabasePublicKey.js');"
        "process.stdout.write(m.classifySupabasePublicKey(process.argv[1]));"
    )
    proc = subprocess.run(
        ["node", "-e", script, value],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node classify failed: {proc.stderr[:200]}")
    return proc.stdout


def _node_assert_env(env: Mapping[str, str]) -> subprocess.CompletedProcess:
    script = textwrap.dedent(
        """\
        const m = require('./app/lib/supabasePublicKey.js');
        try {
          m.assertSafePublicSupabaseEnv(process.env, { requirePresent: true });
          process.stdout.write('ok');
        } catch (err) {
          const code = (err && err.code) || (err && err.message) || 'error.unspecified';
          process.stderr.write(String(code));
          process.exit(2);
        }
        """
    )
    return subprocess.run(
        ["node", "-e", script],
        cwd=str(REPO_ROOT),
        env={**os.environ, **dict(env)},
        capture_output=True,
        text=True,
        check=False,
    )


def _load_next_config(env: Mapping[str, str]) -> subprocess.CompletedProcess:
    script = textwrap.dedent(
        """\
        try {
          const cfg = require('./next.config.js');
          const anon = cfg.env && cfg.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
          process.stdout.write(JSON.stringify({
            hasAnon: typeof anon === 'string' && anon.length > 0,
            anonLen: typeof anon === 'string' ? anon.length : 0,
          }));
        } catch (err) {
          const code = (err && err.code) || (err && err.message) || 'error.unspecified';
          process.stderr.write(String(code));
          process.exit(2);
        }
        """
    )
    isolated = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "NODE_PATH": os.environ.get("NODE_PATH", ""),
    }
    isolated.update(dict(env))
    return subprocess.run(
        ["node", "-e", script],
        cwd=str(REPO_ROOT),
        env=isolated,
        capture_output=True,
        text=True,
        check=False,
    )


class SupabasePublicKeyCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_preflight()
        cls.project_id = "abcdefghijklmnop"
        cls.other_project_id = "qrstuvwxyzabcdef"
        cls.supabase_url = f"https://{cls.project_id}.supabase.co"
        cls.anon_jwt = _runtime_jwt(role="anon", seed=cls.project_id)
        cls.anon_jwt_other = _runtime_jwt(
            role="anon", seed="other-anon", ref=cls.other_project_id
        )
        cls.service_jwt = _runtime_jwt(role="service_role", seed="compat-service")
        cls.authenticated_jwt = _runtime_jwt(role="authenticated", seed=cls.project_id)
        cls.malformed_jwt = (
            _b64url(b'{"alg":"HS256"}')
            + "."
            + _b64url(b"not-json")
            + "."
            + _b64url(b"sig")
        )
        cls.publishable = _runtime_publishable("compat")
        cls.secret_opaque = _runtime_opaque_secret("compat")
        cls.random_opaque = hashlib.sha256(b"not-a-supabase-key").hexdigest()

    def _values(self, **overrides: str) -> Dict[str, str]:
        base = {
            "CLOUDFLARE_API_TOKEN": "cf_fixture_token_not_real",
            "VERCEL_API_TOKEN": "vct_fixture_token_not_real_aaaaaaaa",
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_ANON_KEY": self.anon_jwt,
            "SUPABASE_SERVICE_ROLE_KEY": self.service_jwt,
            "SUPABASE_ACCESS_TOKEN": "sbp_fixture_access_token_not_real",
            "TERRAFORM_STATE_PASSWORD": "fixture_state_password_32chars_xx",
            "SUPABASE_PROJECT_ID": self.project_id,
            "VERCEL_PROJECT_ID": "prj_agent_arena_fixture_002",
            "SAVEDTUBE_VERCEL_PROJECT_ID": "prj_savedtube_fixture_001",
            "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
            "NEXT_PUBLIC_SITE_URL": "https://example.com",
        }
        base.update(overrides)
        return base

    def _run(self, values: Mapping[str, str]):
        source = self.mod.CallerProvidedEnvSource(values)
        return self.mod.run_preflight(
            alias_map=_base_aliases(),
            source=source,
            context={},
        )

    def _assert_no_fixture_leak(self, text: str) -> None:
        for fragment in (
            self.anon_jwt,
            self.anon_jwt_other,
            self.service_jwt,
            self.authenticated_jwt,
            self.publishable,
            self.secret_opaque,
            self.malformed_jwt,
            self.random_opaque,
        ):
            self.assertNotIn(fragment, text)

    def test_public_allowlist_includes_publishable_not_arbitrary_next_public(self):
        allow = self.mod.PUBLIC_CONFIG_ALLOWLIST
        self.assertIn("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", allow)
        self.assertIn("NEXT_PUBLIC_SUPABASE_ANON_KEY", allow)
        self.assertIn("NEXT_PUBLIC_SUPABASE_URL", allow)
        self.assertIn("NEXT_PUBLIC_SITE_URL", allow)
        self.assertNotIn("NEXT_PUBLIC_ARBITRARY", allow)
        self.assertLessEqual(len(allow), 8)

    def test_resolve_prefers_modern_when_nonempty_blank_fallback(self):
        resolve = getattr(self.mod, "resolve_public_supabase_key", None)
        self.assertIsNotNone(resolve, "resolve_public_supabase_key must exist")
        modern = self.publishable
        legacy = self.anon_jwt
        self.assertEqual(
            resolve(
                {
                    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": modern,
                    "NEXT_PUBLIC_SUPABASE_ANON_KEY": legacy,
                }
            ),
            modern,
        )
        self.assertEqual(
            resolve(
                {
                    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "  ",
                    "NEXT_PUBLIC_SUPABASE_ANON_KEY": legacy,
                }
            ),
            legacy,
        )
        self.assertEqual(resolve({"NEXT_PUBLIC_SUPABASE_ANON_KEY": legacy}), legacy)
        self.assertIsNone(
            resolve(
                {
                    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "  ",
                    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "",
                }
            )
        )

    def test_modern_only_public_config_passes_preflight_local_checks(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=self.publishable,
        )
        values.pop("NEXT_PUBLIC_SUPABASE_ANON_KEY", None)
        report = self._run(values)
        public = report.to_public_dict()
        self._assert_no_fixture_leak(json.dumps(public))
        self.assertTrue(
            report.ok,
            f"modern-only should pass local preflight; errors={public.get('errors')}",
        )

    def test_legacy_only_public_config_still_passes(self):
        values = self._values(NEXT_PUBLIC_SUPABASE_ANON_KEY=self.anon_jwt)
        values.pop("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", None)
        report = self._run(values)
        self.assertTrue(report.ok, report.to_public_dict().get("errors"))

    def test_modern_and_legacy_different_values_coexist(self):
        """sb_publishable_* and legacy anon JWT are legitimately different for one project."""
        values = self._values(
            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=self.publishable,
            NEXT_PUBLIC_SUPABASE_ANON_KEY=self.anon_jwt,
        )
        report = self._run(values)
        public = report.to_public_dict()
        self.assertTrue(
            report.ok,
            f"modern+legacy coexistence must pass; errors={public.get('errors')}",
        )
        blob = json.dumps(public).lower()
        self.assertNotIn("supabase_key_conflict", blob)
        self._assert_no_fixture_leak(json.dumps(public))

    def test_each_candidate_validated_even_when_not_selected(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=self.publishable,
            NEXT_PUBLIC_SUPABASE_ANON_KEY=self.service_jwt,
        )
        report = self._run(values)
        public = report.to_public_dict()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("privileged" in e.lower() or "key_type" in e.lower() for e in report.errors),
            f"unselected legacy privileged candidate must fail; errors={public.get('errors')}",
        )
        self._assert_no_fixture_leak(json.dumps(public))

    def test_equal_modern_and_legacy_allowed(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=self.anon_jwt,
            NEXT_PUBLIC_SUPABASE_ANON_KEY=self.anon_jwt,
        )
        report = self._run(values)
        self.assertTrue(
            report.ok,
            f"identical modern/legacy should be allowed; errors={report.to_public_dict().get('errors')}",
        )

    def test_privileged_jwt_in_public_slot_rejected(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=self.service_jwt,
        )
        values.pop("NEXT_PUBLIC_SUPABASE_ANON_KEY", None)
        report = self._run(values)
        public = report.to_public_dict()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("privileged" in e.lower() or "key_type" in e.lower() for e in report.errors)
            or any(
                "privileged" in json.dumps(c).lower() or "key_type" in json.dumps(c).lower()
                for c in public.get("capabilities", [])
            ),
            f"expected privileged rejection; errors={public.get('errors')}",
        )
        self._assert_no_fixture_leak(json.dumps(public))

    def test_privileged_opaque_secret_prefix_rejected_on_public_slot(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_ANON_KEY=self.secret_opaque,
        )
        report = self._run(values)
        self.assertFalse(report.ok)
        self._assert_no_fixture_leak(json.dumps(report.to_public_dict()))

    def test_malformed_jwt_public_key_rejected(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_ANON_KEY=self.malformed_jwt,
        )
        report = self._run(values)
        public = report.to_public_dict()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("malformed" in e.lower() or "key_type" in e.lower() for e in report.errors),
            f"expected malformed rejection; errors={public.get('errors')}",
        )
        self._assert_no_fixture_leak(json.dumps(public))

    def test_classify_fails_closed_unknown_opaque_and_authenticated_role(self):
        classify = getattr(self.mod, "classify_supabase_public_key", None)
        self.assertIsNotNone(classify, "classify_supabase_public_key must exist")
        self.assertEqual(classify(self.anon_jwt), "public")
        self.assertEqual(classify(self.publishable), "public")
        self.assertEqual(classify(self.service_jwt), "privileged")
        self.assertEqual(classify(self.secret_opaque), "privileged")
        self.assertEqual(classify(self.malformed_jwt), "malformed")
        self.assertEqual(classify(self.authenticated_jwt), "malformed")
        self.assertEqual(classify(self.random_opaque), "malformed")
        self.assertEqual(classify(""), "malformed")
        self.assertEqual(classify("short"), "malformed")
        self.assertEqual(classify("x" * 9000), "malformed")
        self.assertEqual(classify(None), "malformed")  # type: ignore[arg-type]
        self.assertEqual(classify(12345), "malformed")  # type: ignore[arg-type]

    def test_python_js_classify_parity(self):
        classify = self.mod.classify_supabase_public_key
        samples = [
            self.anon_jwt,
            self.publishable,
            self.service_jwt,
            self.secret_opaque,
            self.malformed_jwt,
            self.authenticated_jwt,
            self.random_opaque,
            "",
            "short",
        ]
        for sample in samples:
            with self.subTest(sample_kind=classify(sample) if isinstance(sample, str) else "bad"):
                self.assertEqual(classify(sample), _node_classify(sample))

    def test_cross_project_jwt_ref_rejected_when_url_evidence_explicit(self):
        values = self._values(NEXT_PUBLIC_SUPABASE_ANON_KEY=self.anon_jwt_other)
        values.pop("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", None)
        report = self._run(values)
        public = report.to_public_dict()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("project_mismatch" in e.lower() or "mismatch" in e.lower() for e in report.errors),
            f"expected project mismatch; errors={public.get('errors')}",
        )
        self._assert_no_fixture_leak(json.dumps(public))

    def test_missing_url_ref_leaves_project_binding_unknown(self):
        values = self._values(NEXT_PUBLIC_SUPABASE_ANON_KEY=self.anon_jwt)
        values.pop("NEXT_PUBLIC_SUPABASE_URL", None)
        values.pop("SUPABASE_URL", None)
        values.pop("SUPABASE_PROJECT_ID", None)
        values["NEXT_PUBLIC_SUPABASE_URL"] = "https://example.com/not-supabase"
        report = self._run(values)
        public = report.to_public_dict()
        # No explicit supabase.co ref ⇒ do not invent mismatch; local type checks may still pass.
        self.assertFalse(
            any("project_mismatch" in e.lower() for e in report.errors),
            f"without explicit URL/ref evidence, mismatch must stay unknown; errors={public.get('errors')}",
        )
        self._assert_no_fixture_leak(json.dumps(public))

    def test_errors_never_include_key_material(self):
        values = self._values(
            NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=self.service_jwt,
            NEXT_PUBLIC_SUPABASE_ANON_KEY=self.secret_opaque,
        )
        report = self._run(values)
        blob = json.dumps(report.to_public_dict()) + "\n".join(report.errors)
        self._assert_no_fixture_leak(blob)

    def test_public_name_alone_does_not_authorize_service_role_alias(self):
        values = self._values()
        values["NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY"] = self.service_jwt
        del values["SUPABASE_SERVICE_ROLE_KEY"]
        report = self._run(values)
        self.assertFalse(report.ok)
        self.assertTrue(
            any("allowlist" in e.lower() for e in report.errors)
            or any("alias.missing" in e for e in report.errors),
        )
        self._assert_no_fixture_leak(json.dumps(report.to_public_dict()))

    def test_next_config_rejects_privileged_modern_only(self):
        proc = _load_next_config(
            {
                "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": self.secret_opaque,
            }
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("privileged", proc.stderr.lower())
        self.assertNotIn(self.secret_opaque, proc.stderr)
        self.assertNotIn(self.secret_opaque, proc.stdout)

    def test_next_config_rejects_privileged_legacy_only(self):
        proc = _load_next_config(
            {
                "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
                "NEXT_PUBLIC_SUPABASE_ANON_KEY": self.service_jwt,
            }
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("privileged", proc.stderr.lower())
        self.assertNotIn(self.service_jwt, proc.stderr)

    def test_next_config_rejects_mixed_privileged_with_public(self):
        proc = _load_next_config(
            {
                "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": self.publishable,
                "NEXT_PUBLIC_SUPABASE_ANON_KEY": self.service_jwt,
            }
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("privileged", proc.stderr.lower())
        self.assertNotIn(self.service_jwt, proc.stderr)
        self.assertNotIn(self.publishable, proc.stderr)

    def test_next_config_accepts_coexisting_modern_and_legacy(self):
        proc = _load_next_config(
            {
                "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": self.publishable,
                "NEXT_PUBLIC_SUPABASE_ANON_KEY": self.anon_jwt,
                "SUPABASE_PROJECT_ID": self.project_id,
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["hasAnon"])

    def test_js_assert_safe_checks_both_candidate_slots(self):
        proc = _node_assert_env(
            {
                "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": self.publishable,
                "NEXT_PUBLIC_SUPABASE_ANON_KEY": self.service_jwt,
                "SUPABASE_PROJECT_ID": self.project_id,
            }
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("privileged", proc.stderr.lower())
        self.assertNotIn(self.service_jwt, proc.stderr)

    def test_auth_helper_env_contract_receives_resolved_public_key(self):
        """createClientComponentClient reads NEXT_PUBLIC_SUPABASE_ANON_KEY from env."""
        proc = _load_next_config(
            {
                "NEXT_PUBLIC_SUPABASE_URL": self.supabase_url,
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": self.publishable,
                "SUPABASE_PROJECT_ID": self.project_id,
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Executable parity: resolved anon contract length matches publishable (mapped).
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["anonLen"], len(self.publishable))

        helper = (REPO_ROOT / "app" / "lib" / "supabase.ts").read_text(encoding="utf-8")
        self.assertIn("createClientComponentClient", helper)

        ts_helper = (REPO_ROOT / "app" / "lib" / "supabasePublicEnv.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("classifySupabasePublicKey", ts_helper)
        self.assertIn("assertSafePublicSupabaseEnv", ts_helper)

    def test_configuration_check_mentions_modern_or_legacy(self):
        text = (REPO_ROOT / "app" / "components" / "ConfigurationCheck.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", text)
        self.assertIn("NEXT_PUBLIC_SUPABASE_ANON_KEY", text)

    def test_env_example_and_binding_list_publishable(self):
        env_example = (REPO_ROOT / "env.example").read_text(encoding="utf-8")
        binding = (REPO_ROOT / "examples" / "credential-binding.example.json").read_text(
            encoding="utf-8"
        )
        self.assertIn("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", env_example)
        self.assertIn("NEXT_PUBLIC_SUPABASE_ANON_KEY", env_example)
        self.assertIn("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", binding)
        self.assertNotIn("Do not set both to different values", env_example)

    def test_terraform_hard_enforcement_not_soft_check_only(self):
        variables = (REPO_ROOT / "terraform" / "variables.tf").read_text(encoding="utf-8")
        main = (REPO_ROOT / "terraform" / "main.tf").read_text(encoding="utf-8")
        vercel_main = (REPO_ROOT / "terraform" / "modules" / "vercel" / "main.tf").read_text(
            encoding="utf-8"
        )
        self.assertIn('variable "supabase_publishable_key"', variables)
        self.assertIn("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", main)
        combined = variables + "\n" + main + "\n" + vercel_main
        self.assertTrue(
            "precondition" in combined or "validation {" in variables,
            "public key rules must use validation/precondition hard paths",
        )
        # Soft check-only inequality conflict must not remain the coexistence gate.
        self.assertNotRegex(
            main,
            re.compile(
                r'check\s+"supabase_public_key_no_conflict"[\s\S]{0,400}'
                r"supabase_publishable_trimmed\s*==\s*local\.supabase_anon_trimmed",
            ),
        )
        self.assertRegex(
            combined,
            re.compile(r"sb_secret_|service_role|privileged|sb_publishable_", re.I),
        )
        # Format screening beyond mere equality with service_role.
        self.assertIn("sb_publishable_", variables + main)

    def test_terraform_local_predicates_mirror_coexistence_and_privileged_reject(self):
        """Evaluate documented Terraform local predicates without terraform plan."""
        evaluate = getattr(self.mod, "terraform_public_key_predicates", None)
        self.assertIsNotNone(
            evaluate,
            "terraform_public_key_predicates helper must exist for offline hard-path tests",
        )
        ok_coexist = evaluate(
            publishable=self.publishable,
            anon=self.anon_jwt,
            service_role=self.service_jwt,
        )
        self.assertTrue(ok_coexist["public_keys_safe"], ok_coexist)
        self.assertTrue(ok_coexist["coexistence_allowed"])

        bad_secret = evaluate(
            publishable=self.secret_opaque,
            anon="",
            service_role=self.service_jwt,
        )
        self.assertFalse(bad_secret["public_keys_safe"])

        bad_other_priv = evaluate(
            publishable="",
            anon=self.service_jwt,
            service_role=_runtime_jwt(role="service_role", seed="different-service"),
        )
        self.assertFalse(
            bad_other_priv["public_keys_safe"],
            "privileged JWT must fail even when unequal to supplied service_role",
        )

        missing = evaluate(publishable="", anon="", service_role=self.service_jwt)
        self.assertFalse(missing["public_keys_safe"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""APP-006 workflow and deployment gate safety tests (static, no live deploys)."""

from __future__ import annotations

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
from typing import Dict, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_QUALITY_WORKFLOW = ".github/workflows/app-quality.yml"
CI_ANON_SEED = b"app006-ci-anon-seed"
LEGACY_INVALID_FIXTURE_PREFIX = "fixture-anon-"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _load_secrets_preflight():
    path = REPO_ROOT / "scripts" / "secrets-preflight.py"
    spec = importlib.util.spec_from_file_location("secrets_preflight_workflow_safety", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_synthetic_fixture_run_block(wf: str) -> str:
    """Return the shell body of the quality workflow synthetic public-key step."""
    match = re.search(
        r"(?m)^      - name: Synthetic public publishable fixture[^\n]*\n"
        r"        run: \|\n"
        r"((?:          [^\n]*\n)+)",
        wf,
    )
    if match is None:
        raise AssertionError(
            "app-quality.yml missing Synthetic public publishable fixture run block"
        )
    lines = []
    for raw in match.group(1).splitlines():
        if not raw.startswith("          "):
            raise AssertionError(f"unexpected indentation in fixture run block: {raw!r}")
        lines.append(raw[10:])
    script = "\n".join(lines).rstrip() + "\n"
    if "app006-ci-anon-seed" not in script:
        raise AssertionError("extracted fixture block lost deterministic seed")
    if "NEXT_PUBLIC_SUPABASE_ANON_KEY" not in script:
        raise AssertionError("extracted fixture block lost ANON_KEY alias export")
    if LEGACY_INVALID_FIXTURE_PREFIX in script:
        raise AssertionError("extracted fixture block still uses invalid fixture-anon- prefix")
    if "sb_publishable_" not in script:
        raise AssertionError("extracted fixture block must emit sb_publishable_ values")
    return script


def _parse_github_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _node_assert_safe_public_env(env: Mapping[str, str]) -> subprocess.CompletedProcess:
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
        env={"PATH": os.environ.get("PATH", ""), **dict(env)},
        capture_output=True,
        text=True,
        check=False,
    )


class WorkflowSafetyTests(unittest.TestCase):
    def test_first_party_runtime_actions_use_reviewed_node24_releases(self) -> None:
        expected = {
            'actions/checkout': 'v7.0.1',
            'actions/setup-node': 'v7.1.0',
            'actions/setup-python': 'v7.1.0',
        }
        for path in (REPO_ROOT / '.github/workflows').glob('*.yml'):
            for action, ref in re.findall(r'uses:\s*(actions/[a-z-]+)@([^\s#]+)', path.read_text()):
                if action in expected:
                    self.assertEqual(ref, expected[action], f'{path.name}: {action}')

    def test_legacy_deploy_and_terraform_remain_disabled(self) -> None:
        deploy = _read(".github/workflows/deploy.yml")
        terraform = _read(".github/workflows/terraform.yml")
        self.assertIn("if: false", deploy)
        self.assertIn("APP003_MIGRATIONS_DISABLED", deploy)
        self.assertIn("if: false", terraform)
        self.assertTrue(
            "APP-002" in terraform or "APP-005" in terraform,
            "legacy terraform mutate path must stay documented as disabled",
        )

    def test_app_quality_workflow_exists_without_secrets_or_pr_target(self) -> None:
        path = REPO_ROOT / APP_QUALITY_WORKFLOW
        self.assertTrue(path.is_file())
        wf = path.read_text(encoding="utf-8")
        on_block = wf.split("jobs:", 1)[0]
        self.assertNotRegex(on_block, r"(?m)^\s*pull_request_target\s*:")
        self.assertNotIn("pull_request_target", wf)
        self.assertIn("branches: ['**']", on_block)
        self.assertIn("github.event.repository.default_branch", wf)
        self.assertNotRegex(wf, r"secrets\.(CLOUDFLARE|VERCEL|SUPABASE|TERRAFORM|GOOGLE)_")
        self.assertNotIn("secrets.DOMAIN_NAME", wf)
        self.assertIn("permissions:", wf)
        self.assertRegex(wf, r"contents:\s*read")
        for action in ("actions/checkout@", "actions/setup-node@", "actions/setup-python@"):
            self.assertIn(action, wf)
        self.assertNotRegex(wf, r"uses:\s*actions/[a-z-]+@main\b")
        self.assertNotRegex(wf, r"uses:\s*actions/[a-z-]+@master\b")
        # Pin verification needs the historical TEMPLATE_REVISION commit object.
        # Default actions/checkout shallow depth omits it; require full history.
        self.assertRegex(
            wf,
            r"(?s)uses:\s*actions/checkout@v7\.0\.1\s*\n\s*with:\s*\n(?:\s*#[^\n]*\n)*\s*fetch-depth:\s*0\b",
            msg="app-quality checkout must set fetch-depth: 0 for pinned template history",
        )
        # Generated repos lack upstream ancestry; CI must materialize the exact
        # pin from the allowlisted reviewed upstream identity only.
        self.assertIn("Ensure pinned template commit object", wf)
        self.assertIn("ensure_pinned_commit_object", wf)
        self.assertIn("https://github.com/agrana/free_personal_stack.git", wf)
        self.assertIn("PINNED_TEMPLATE_REVISION", wf)
        self.assertIn("is_reviewed_template_remote", wf)
        self.assertNotIn("template.snapshot_unverified — weaken", wf)
        self.assertNotRegex(
            wf,
            r"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            msg="quality workflow must not hardcode JWT-shaped synthetic anon keys",
        )
        self.assertIn("app006-ci-anon-seed", wf)
        self.assertIn("NEXT_PUBLIC_SUPABASE_ANON_KEY", wf)
        self.assertIn("sb_publishable_", wf)
        self.assertRegex(
            wf,
            r'ANON="sb_publishable_\$\(',
            msg="quality workflow must generate sb_publishable_<sha256> at runtime",
        )
        self.assertNotRegex(
            wf,
            r'ANON="fixture-anon-',
            msg="quality workflow must not generate invalid fixture-anon- public keys",
        )
        self.assertIn("Synthetic public publishable fixture", wf)
        pkg = json.loads(_read("package.json"))
        engines = str(pkg.get("engines", {}).get("node", ""))
        self.assertIn("24", engines)
        self.assertTrue("24" in wf or "node-version-file" in wf)
        self.assertTrue(
            "npm ci" in wf or "npm ci --" in wf,
            "quality workflow must use lockfile install (npm ci)",
        )
        for needle in ("lint", "type-check", "build", "test"):
            self.assertIn(needle, wf.lower())
        self.assertIn("run-all-tests.py", _read("package.json"))
        self.assertIn("npm test", wf)
        # Mandatory Postgres: harness mode 1 + provision; fail on skip
        self.assertIn("APP003_PG_TESTS", wf)
        self.assertRegex(wf, r"APP003_PG_TESTS:\s*['\"]?1['\"]?")
        self.assertIn("podman", wf.lower())
        self.assertTrue(
            "mandatory" in wf.lower() or "APP003_PG_TESTS=1" in wf,
            "quality workflow must use mandatory Postgres mode",
        )
        # Enforced security vs informative audit must be separated
        self.assertIn("Enforced security", wf)
        self.assertIn("Informative npm audit", wf)
        self.assertIn("non-gating", wf.lower())
        self.assertIn("claimsFullProductionBuild", wf)
        self.assertIn("built-bundle", wf)
        # Do not imply .secrets.baseline is executed as a live scan
        self.assertIn("not executed as a live secret scan", wf)
        self.assertTrue(
            "fork" in wf.lower() or "head.repo.full_name" in wf or "pull_request" in wf
        )
        self.assertTrue(
            "fixture" in wf.lower() or "NEXT_PUBLIC_" in wf,
            "build must document synthetic/public fixture config",
        )
        # No production secret injection; fixture canary alias names in scan OK.
        self.assertNotRegex(wf, r"\$\{\{\s*secrets\.")
        self.assertNotIn("secrets.SUPABASE_SERVICE_ROLE_KEY", wf)
        self.assertIn("test:health-http", wf)
        self.assertIn("APP006_LOCAL_TEST_REVISION", wf)

    def test_app_quality_synthetic_fixture_run_block_passes_real_public_key_validators(
        self,
    ) -> None:
        """Execute the workflow's real fixture script; validators must accept output.

        Also proves the retired fixture-anon-<sha256> shape fails the same
        JS/Python parsers (no duplicated generator as the sole pass criterion).
        """
        wf = _read(APP_QUALITY_WORKFLOW)
        script = _extract_synthetic_fixture_run_block(wf)
        preflight = _load_secrets_preflight()
        classify = preflight.classify_supabase_public_key
        seed_digest = hashlib.sha256(CI_ANON_SEED).hexdigest()
        retired = f"{LEGACY_INVALID_FIXTURE_PREFIX}{seed_digest}"

        self.assertEqual(classify(retired), "malformed")
        retired_js = _node_assert_safe_public_env(
            {"NEXT_PUBLIC_SUPABASE_ANON_KEY": retired}
        )
        self.assertNotEqual(
            retired_js.returncode,
            0,
            msg="retired fixture-anon- shape must fail assertSafePublicSupabaseEnv",
        )
        self.assertIn("public.config.supabase_key_malformed", retired_js.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            github_env = Path(tmp) / "github_env"
            github_env.write_text("", encoding="utf-8")
            isolated = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": tmp,
                "GITHUB_ENV": str(github_env),
                "LC_ALL": "C",
            }
            proc = subprocess.run(
                ["bash", "-c", script],
                cwd=str(REPO_ROOT),
                env=isolated,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"workflow fixture script failed: stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            parsed = _parse_github_env(github_env)

        anon = parsed.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
        self.assertTrue(anon, "fixture script must export NEXT_PUBLIC_SUPABASE_ANON_KEY")
        self.assertTrue(
            anon.startswith("sb_publishable_"),
            msg="workflow fixture must emit sb_publishable_* (not fixture-anon-*)",
        )
        self.assertNotIn(LEGACY_INVALID_FIXTURE_PREFIX, anon)
        self.assertEqual(classify(anon), "public")
        preds = preflight.terraform_public_key_predicates(
            publishable="",
            anon=anon,
            service_role="",
        )
        self.assertTrue(
            preds.get("public_keys_safe"),
            msg=f"Python public-key predicates must accept workflow fixture: {preds}",
        )

        js = _node_assert_safe_public_env({"NEXT_PUBLIC_SUPABASE_ANON_KEY": anon})
        self.assertEqual(
            js.returncode,
            0,
            msg=f"assertSafePublicSupabaseEnv rejected workflow fixture: {js.stderr}",
        )
        self.assertEqual(js.stdout, "ok")

    def test_app_deploy_workflow_manual_gate_not_automatic(self) -> None:
        path = REPO_ROOT / ".github/workflows/app-deploy.yml"
        self.assertTrue(path.is_file())
        wf = path.read_text(encoding="utf-8")
        self.assertTrue(
            "workflow_call" in wf or "workflow_dispatch" in wf,
            "deploy workflow must be workflow_call and/or workflow_dispatch",
        )
        on_block = wf.split("jobs:", 1)[0]
        self.assertNotRegex(
            on_block,
            r"(?m)^\s+push:\s*$",
            msg="app-deploy must not trigger on push",
        )
        self.assertNotRegex(
            on_block,
            r"(?m)^\s+pull_request:\s*$",
            msg="app-deploy must not trigger on pull_request",
        )
        self.assertIn("environment:", wf)
        self.assertIn("production", wf)
        self.assertTrue(
            "prerequisite" in wf.lower() or "not guarantee" in wf.lower(),
            "production environment keyword must not be documented as guaranteed approval",
        )
        self.assertTrue(
            ("disabled" in wf.lower() or "blocked" in wf.lower() or "if: false" in wf),
            "deploy apply path must stay visibly blocked",
        )
        self.assertTrue(
            "live_integration_missing" in wf
            or "BlockedLiveApplyAdapter" in wf
            or "scaffold" in wf.lower(),
            "deploy gate must name the actual APP-005 live-integration boundary",
        )
        self.assertTrue(
            "reviewed_ref" in wf or "reviewed-ref" in wf,
            "deploy workflow must require an immutable reviewed git ref input",
        )
        self.assertIn("ref:", wf)
        self.assertRegex(
            wf,
            r"ref:\s*\$\{\{\s*inputs\.reviewed_ref\s*\}\}",
            msg="checkout must pin to inputs.reviewed_ref",
        )
        self.assertNotRegex(
            wf,
            r"(?i)hash[-_ ]?check.*(deploy|authoriz|approv)",
        )
        self.assertNotIn("fake_hash", wf.lower())
        self.assertTrue(
            "validate" in wf.lower() or "verification" in wf.lower() or "guard" in wf.lower()
        )
        self.assertTrue(
            "VERCEL_PROJECT" in wf or "vercel" in wf.lower(),
            "must reference distinct Vercel project/scope coordination",
        )
        self.assertTrue(
            "migrat" in wf.lower() or "APP-003" in wf,
            "must coordinate scoped migrations (not unattended shared push)",
        )
        self.assertTrue(
            "free" in wf.lower() or "paid" in wf.lower() or "FREE_TIER" in wf,
            "must flag free-tier compatibility / paid features",
        )
        self.assertNotIn("tfplan", wf)
        self.assertNotRegex(wf, r"upload-artifact:[\s\S]{0,200}\.env")
        self.assertNotIn("pull_request_target", wf)
        # Live apply/migrations remain disabled
        self.assertGreaterEqual(wf.count("if: false"), 2)

    def test_docs_app_deployment_exists_and_is_honest(self) -> None:
        docs = _read("docs/app-deployment.md")
        self.assertIn("APP-006", docs)
        self.assertIn("APP-005", docs)
        self.assertIn("APP-007", docs)
        lowered = docs.lower()
        self.assertIn("rollback", lowered)
        self.assertTrue(
            "browser" in lowered and ("pending" in lowered or "http probe" in lowered),
            "docs must distinguish HTTP probes from browser verification",
        )
        self.assertTrue(
            "not" in lowered
            and (
                "live" in lowered
                or "deployed verification" in lowered
                or "unverified" in lowered
            ),
            "docs must not claim completed live deployed verification",
        )
        self.assertIn("production", lowered)
        self.assertTrue(
            "workflow_call" in lowered
            or "workflow_dispatch" in lowered
            or "manual" in lowered
        )
        self.assertTrue(
            "live_integration_missing" in docs
            or "BlockedLiveApplyAdapter" in docs
            or "scaffold-only" in lowered
            or "scaffold only" in lowered,
            "docs must state scaffold-only / live_integration_missing status",
        )
        self.assertTrue(
            "fixture" in lowered and ("not" in lowered or "never" in lowered),
            "docs must separate fixture checks from production proof",
        )
        self.assertTrue(
            "verification tooling" in lowered or "guarded orchestration" in lowered,
            "docs must describe this slice as verification/orchestration",
        )
        self.assertTrue(
            "adapter remains missing" in lowered or "adapter not" in lowered or "rollback adapter" in lowered,
            "docs must state deploy/rollback adapter is missing",
        )
        self.assertIn("getaddrinfo", lowered)
        self.assertTrue(
            "cannot be cancell" in lowered or "no portable cancellation" in lowered
        )
        self.assertIn("process liveness", lowered)
        self.assertIn("APP006_LOCAL_TEST_REVISION", docs)
        self.assertIn("run-all-tests.py", docs)
        self.assertIn("fetch-depth", docs)
        self.assertIn("template.snapshot_unverified", docs)
        self.assertIn("configured admin prerequisite", lowered)
        self.assertIn("does not by itself guarantee", lowered)
        self.assertNotIn("app-new.py --deploy", docs)
        self.assertNotIn("./scripts/app-new.sh deploy", docs)

    def test_bootstrap_md_not_rewritten_by_app006(self) -> None:
        docs = _read("docs/bootstrap.md")
        self.assertIn("APP-004", docs)
        self.assertIn("APP-005", docs)
        self.assertNotRegex(docs, r"(?m)^##\s*APP-006\b")
        self.assertNotIn("verify-deployment.py", docs)

    def test_pinned_python_requirements_referenced(self) -> None:
        req = REPO_ROOT / "requirements-bootstrap.txt"
        self.assertTrue(req.is_file())
        text = req.read_text(encoding="utf-8")
        self.assertRegex(text, r"jsonschema==")
        quality = _read(".github/workflows/app-quality.yml")
        self.assertIn("requirements-bootstrap.txt", quality)

    def test_package_scripts_include_verify_deployment(self) -> None:
        pkg = json.loads(_read("package.json"))
        scripts = pkg["scripts"]
        self.assertIn("verify:deployment", scripts)
        self.assertIn("verify-deployment", scripts["verify:deployment"])
        test_script = scripts["test"]
        self.assertNotIn("No tests specified yet", test_script)
        self.assertIn("run-all-tests.py", test_script)
        self.assertIn("test:health-http", scripts)


if __name__ == "__main__":
    unittest.main()

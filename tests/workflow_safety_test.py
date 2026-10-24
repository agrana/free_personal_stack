#!/usr/bin/env python3
"""APP-006 workflow and deployment gate safety tests (static, no live deploys)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class WorkflowSafetyTests(unittest.TestCase):
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
        path = REPO_ROOT / ".github/workflows/app-quality.yml"
        self.assertTrue(path.is_file())
        wf = path.read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target", wf)
        self.assertNotRegex(wf, r"secrets\.(CLOUDFLARE|VERCEL|SUPABASE|TERRAFORM|GOOGLE)_")
        self.assertNotIn("secrets.DOMAIN_NAME", wf)
        self.assertIn("permissions:", wf)
        self.assertRegex(wf, r"contents:\s*read")
        for action in ("actions/checkout@", "actions/setup-node@", "actions/setup-python@"):
            self.assertIn(action, wf)
        self.assertNotRegex(wf, r"uses:\s*actions/[a-z-]+@main\b")
        self.assertNotRegex(wf, r"uses:\s*actions/[a-z-]+@master\b")
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

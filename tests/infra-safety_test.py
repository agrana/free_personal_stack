#!/usr/bin/env python3
"""Static safety checks for APP-002 shared-Supabase template hardening."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class InfraSafetyTests(unittest.TestCase):
    def test_supabase_module_has_no_null_resource_or_secret_triggers(self):
        main = _read("terraform/modules/supabase/main.tf")
        self.assertNotIn("null_resource", main)
        self.assertNotIn("local-exec", main)
        self.assertNotIn("access_token", main)
        self.assertNotIn("projects delete", main)
        create_example = REPO_ROOT / "terraform/modules/supabase/create_project.tf.example"
        self.assertFalse(
            create_example.exists(),
            "create/delete project example must be removed for shared-project safety",
        )

    def test_supabase_deletion_flag_defaults_false_or_removed(self):
        root_vars = _read("terraform/variables.tf")
        mod_vars = _read("terraform/modules/supabase/variables.tf")
        # Deletion must not default to enabled
        self.assertNotRegex(
            root_vars,
            r"enable_supabase_project_deletion[\s\S]{0,400}default\s*=\s*true",
        )
        self.assertNotRegex(
            mod_vars,
            r"enable_project_deletion[\s\S]{0,200}default\s*=\s*true",
        )
        # Prefer hard-disable: either variable removed or default false with no destroy hook
        if "enable_supabase_project_deletion" in root_vars:
            self.assertRegex(
                root_vars,
                r"enable_supabase_project_deletion[\s\S]{0,400}default\s*=\s*false",
            )

    def test_dns_defaults_disabled_and_no_www_api_defaults(self):
        root_vars = _read("terraform/variables.tf")
        main = _read("terraform/main.tf")
        dns_main = _read("terraform/modules/dns/main.tf")
        self.assertRegex(root_vars, r"enable_dns[\s\S]{0,200}default\s*=\s*false")
        self.assertRegex(
            root_vars,
            r'enable_email_routing[\s\S]{0,200}default\s*=\s*false',
        )
        self.assertIn('create_cloudflare_zone', root_vars)
        self.assertRegex(
            root_vars,
            r"create_cloudflare_zone[\s\S]{0,200}default\s*=\s*false",
        )
        # Zone creation resource must not be owned by template apps
        self.assertNotIn('resource "cloudflare_zone"', main)
        # www/api default records removed; only approved hostname when DNS enabled
        self.assertNotIn('cloudflare_record" "www"', dns_main)
        self.assertNotIn('cloudflare_record" "api"', dns_main)
        self.assertNotIn('cloudflare_record" "root"', dns_main)

    def test_dns_fail_closed_on_unresolved_and_cloudfront_not_faked(self):
        main = _read("terraform/main.tf")
        root_vars = _read("terraform/variables.tf")
        self.assertIn("dns_provider", root_vars)
        self.assertTrue(
            "unresolved" in root_vars or "unresolved" in main,
            "dns_provider must document unresolved fail-closed",
        )
        self.assertTrue(
            "cloudfront" in main.lower() or "cloudfront" in root_vars.lower(),
            "CloudFront must be explicitly called out as not implemented via Cloudflare",
        )
        # Enabling DNS with unresolved/cloudfront must fail closed in Terraform
        self.assertTrue(
            'check "' in main or "precondition" in main or "validation" in root_vars,
            "Terraform must fail closed when DNS enabled with unresolved/cloudfront",
        )

    def test_vercel_project_protected_from_deletion(self):
        vercel = _read("terraform/modules/vercel/main.tf")
        self.assertIn("prevent_destroy", vercel)

    def test_per_app_state_key_required_without_shared_default(self):
        root_vars = _read("terraform/variables.tf")
        self.assertIn("deployment_state_key", root_vars)
        # No default that could clobber another app
        block = re.search(
            r'variable\s+"deployment_state_key"\s*\{([\s\S]*?)\n\}',
            root_vars,
        )
        self.assertIsNotNone(block, "deployment_state_key variable must exist")
        self.assertNotRegex(
            block.group(1),
            r"(?m)^\s*default\s*=",
            msg="deployment_state_key must not declare a Terraform default value",
        )

    def test_approved_hostname_required(self):
        root_vars = _read("terraform/variables.tf")
        self.assertTrue(
            'variable "approved_hostname"' in root_vars
            or 'variable "domain_name"' in root_vars
        )
        # domain_name/approved hostname must not silently invent DNS ownership
        main = _read("terraform/main.tf")
        self.assertIn("enable_dns", main)

    def test_terraform_workflow_destroy_and_apply_disabled(self):
        wf = _read(".github/workflows/terraform.yml")
        self.assertNotRegex(wf, r"^\s*-\s*destroy\s*$", msg="destroy must not be a dispatch option")
        self.assertTrue(
            "APP-002" in wf or "disabled" in wf.lower() or "APP-005" in wf,
            "workflow must document why apply/destroy are blocked",
        )
        # Destroy job must not invoke a live destroy command
        self.assertNotRegex(wf, r"(?m)^\s*run:\s*.*terraform\s+destroy\b")
        self.assertNotRegex(wf, r"terraform destroy -")
        # Apply must not auto-run on push without guard; prefer hard disable
        self.assertTrue(
            "if: false" in wf
            or "APP-005" in wf
            and ("apply disabled" in wf.lower() or "plan guard" in wf.lower() or "disabled" in wf.lower()),
            "legacy apply path must be safely disabled pending APP-005 enforcement",
        )

    def test_deploy_workflow_migrations_disabled_pending_app003(self):
        wf = _read(".github/workflows/deploy.yml")
        self.assertTrue(
            "APP-003" in wf or "disabled" in wf.lower(),
            "automatic shared DB migrations must remain disabled after APP-003",
        )
        self.assertIn("if: false", wf)
        self.assertNotRegex(
            wf,
            r"(?m)^\s*run:.*supabase\s+db\s+push\b",
            msg="db push must not be an executable workflow step",
        )
        self.assertIn("APP003_MIGRATIONS_DISABLED", wf)

    def test_app003_harness_and_docs_exist_without_unattended_path(self):
        self.assertTrue((REPO_ROOT / "scripts" / "migrate_app_namespace.py").is_file())
        self.assertTrue(
            (REPO_ROOT / "db" / "shared_isolation" / "provision" / "01_control_and_roles.sql").is_file()
        )
        self.assertTrue(
            (
                REPO_ROOT
                / "db"
                / "shared_isolation"
                / "provision"
                / "00_disposable_test_baseline.sql"
            ).is_file()
        )
        provision = _read("db/shared_isolation/provision/01_control_and_roles.sql")
        self.assertNotRegex(
            provision,
            r"(?m)^\s*REVOKE CREATE ON SCHEMA public FROM PUBLIC\s*;",
        )
        self.assertNotRegex(
            provision,
            r"(?m)^\s*GRANT anon TO authenticator\s*;",
        )
        self.assertIn("realtime_allowlist", provision)
        docs = _read("docs/bootstrap.md")
        self.assertIn("APP-003", docs)
        self.assertIn("advisory", docs.lower())
        self.assertIn("service_role", docs)
        self.assertIn("example-owner", docs)
        self.assertIn("transaction-control", docs.lower())
        pkg = json.loads(_read("package.json"))
        body = pkg["scripts"]["db:migrate"].lower()
        self.assertTrue("disabled" in body or "exit 1" in body)

    def test_package_scripts_block_destroy_reset_apply(self):
        pkg = json.loads(_read("package.json"))
        scripts = pkg["scripts"]
        for name in (
            "terraform:destroy",
            "terraform:apply",
            "infra:destroy",
            "infra:apply",
            "db:reset",
            "db:migrate",
        ):
            self.assertIn(name, scripts)
            body = scripts[name].lower()
            self.assertTrue(
                "disabled" in body or "app-002" in body or "app-003" in body or "exit 1" in body,
                f"{name} must be disabled/guarded, got: {scripts[name]!r}",
            )
            self.assertNotRegex(
                scripts[name],
                r"(^|[^\w-])(terraform\s+destroy|terraform\s+apply|db\s+reset|db\s+push)([^\w-]|$)",
            )

    def test_quick_start_blocks_apply_destroy(self):
        qs = _read("scripts/quick-start.sh")
        self.assertTrue("APP-002" in qs or "disabled" in qs.lower())
        # apply/destroy branches must exit without calling terraform apply/destroy
        self.assertNotRegex(qs, r"^\s*terraform\s+apply\b", re.M)
        self.assertNotRegex(qs, r"^\s*terraform\s+destroy\b", re.M)

    def test_plan_guard_script_exists(self):
        self.assertTrue((REPO_ROOT / "scripts" / "validate-terraform-plan.py").is_file())
        self.assertTrue((REPO_ROOT / "scripts" / "validate-terraform-plan.sh").is_file())

    def test_bootstrap_docs_cover_app002_and_existing_state_warning(self):
        docs = _read("docs/bootstrap.md")
        self.assertIn("APP-002", docs)
        self.assertIn("APP-005", docs)
        self.assertTrue(
            "state" in docs.lower() and ("detach" in docs.lower() or "existing" in docs.lower()),
            "docs must warn existing template users about resource removal vs state",
        )
        self.assertIn("immutable", docs.lower())

    def test_app004_fork_pr_secret_safeguard_and_preflight_surface(self):
        wf = _read(".github/workflows/terraform.yml")
        self.assertIn("APP-004", wf)
        self.assertIn("head.repo.full_name", wf)
        self.assertTrue((REPO_ROOT / "scripts" / "secrets-preflight.py").is_file())
        self.assertTrue(
            (REPO_ROOT / "examples" / "credential-binding.example.json").is_file()
        )
        docs = _read("docs/bootstrap.md")
        self.assertIn("APP-004", docs)
        self.assertIn("secrets-preflight", docs)
        # Mutate paths must remain disabled (APP-004 must not re-enable deploy)
        self.assertIn("if: false", wf)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Focused tests for APP-002 offline Terraform JSON plan guard."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "plans"

DEFAULT_BINDINGS = {
    "module.vercel.vercel_project.main": "vercel_project",
    "module.vercel.vercel_project_domain.main": "vercel_project_domain",
    "module.vercel.vercel_project_environment_variable.env_vars[\"NEXT_PUBLIC_SITE_URL\"]": (
        "vercel_project_environment_variable"
    ),
}


def _load_guard():
    import sys

    path = REPO_ROOT / "scripts" / "validate-terraform-plan.py"
    spec = importlib.util.spec_from_file_location("validate_terraform_plan", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load plan guard from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _ownership(**overrides) -> dict:
    base = {
        "hostname": "arena.example.com",
        "deploymentIdentity": "agent-arena",
        "stateKey": "agent-arena",
        "dnsChoice": "unresolved",
        "vercel": {
            "teamId": "team_example",
            "projectId": "prj_example",
        },
        "cloudflare": None,
        "allowedResourceTypes": [
            "vercel_project",
            "vercel_project_domain",
            "vercel_project_environment_variable",
        ],
        "resourceBindings": dict(DEFAULT_BINDINGS),
    }
    base.update(overrides)
    return base


def _cloudflare_ownership(**overrides) -> dict:
    bindings = dict(DEFAULT_BINDINGS)
    bindings["module.dns[0].cloudflare_record.app"] = "cloudflare_record"
    own = _ownership(
        dnsChoice="cloudflare",
        cloudflare={
            "zoneId": "zone_example",
            "recordName": "arena",
            "recordType": "CNAME",
            "target": "agent-arena.vercel.app",
        },
        allowedResourceTypes=[
            "vercel_project",
            "vercel_project_domain",
            "vercel_project_environment_variable",
            "cloudflare_record",
        ],
        resourceBindings=bindings,
    )
    own.update(overrides)
    return own


def _load_plan(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PlanGuardTests(unittest.TestCase):
    def test_allowed_fresh_create_passes(self):
        result = guard.validate_plan(_load_plan("allowed-fresh-create.json"), _ownership())
        self.assertTrue(result.ok, result.errors)

    def test_allowed_noop_passes(self):
        result = guard.validate_plan(_load_plan("allowed-noop.json"), _ownership())
        self.assertTrue(result.ok, result.errors)

    def test_allowed_cloudflare_dns_passes(self):
        result = guard.validate_plan(
            _load_plan("allowed-cloudflare-dns.json"), _cloudflare_ownership()
        )
        self.assertTrue(result.ok, result.errors)

    def test_forbidden_delete_rejected(self):
        result = guard.validate_plan(_load_plan("forbidden-delete.json"), _ownership())
        self.assertFalse(result.ok)
        self.assertTrue(any("action" in e.lower() for e in result.errors))

    def test_forbidden_replace_rejected(self):
        result = guard.validate_plan(_load_plan("forbidden-replace.json"), _ownership())
        self.assertFalse(result.ok)
        self.assertTrue(any("action" in e.lower() for e in result.errors))

    def test_forbidden_shared_zone_rejected(self):
        result = guard.validate_plan(_load_plan("forbidden-shared-zone.json"), _ownership())
        self.assertFalse(result.ok)
        self.assertTrue(any("forbidden" in e.lower() for e in result.errors))

    def test_forbidden_mail_routing_rejected(self):
        result = guard.validate_plan(_load_plan("forbidden-mail-routing.json"), _ownership())
        self.assertFalse(result.ok)
        self.assertTrue(any("forbidden" in e.lower() for e in result.errors))

    def test_forbidden_null_resource_hook_rejected(self):
        result = guard.validate_plan(
            _load_plan("forbidden-null-resource-hook.json"), _ownership()
        )
        self.assertFalse(result.ok)
        joined = " ".join(result.errors).lower()
        self.assertTrue("forbidden" in joined or "provisioner" in joined or "hook" in joined)

    def test_unknown_ownership_not_treated_as_valid(self):
        result = guard.validate_plan(
            _load_plan("forbidden-unknown-ownership.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any("unknown" in e.lower() or "ownership" in e.lower() for e in result.errors)
        )

    def test_wrong_hostname_rejected(self):
        result = guard.validate_plan(
            _load_plan("forbidden-wrong-hostname.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))

    def test_wrong_project_identity_rejected(self):
        plan = _load_plan("allowed-fresh-create.json")
        result = guard.validate_plan(plan, _ownership(deploymentIdentity="other-app"))
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))

    def test_unresolved_dns_choice_rejects_dns_bindings_in_config(self):
        ownership = _ownership(
            dnsChoice="unresolved",
            cloudflare=None,
            allowedResourceTypes=[
                "vercel_project",
                "vercel_project_domain",
                "vercel_project_environment_variable",
                "cloudflare_record",
            ],
            resourceBindings={
                **DEFAULT_BINDINGS,
                "module.dns[0].cloudflare_record.app": "cloudflare_record",
            },
        )
        result = guard.validate_plan(
            _load_plan("adversarial-dns-outside-hostname.json"), ownership
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))

    def test_cloudfront_choice_rejects_cloudflare_dns_pretend(self):
        ownership = _ownership(
            dnsChoice="cloudfront",
            cloudflare=None,
            allowedResourceTypes=[
                "vercel_project",
                "vercel_project_domain",
                "vercel_project_environment_variable",
                "cloudflare_record",
            ],
            resourceBindings={
                **DEFAULT_BINDINGS,
                "module.dns[0].cloudflare_record.app": "cloudflare_record",
            },
        )
        result = guard.validate_plan(
            _load_plan("adversarial-dns-outside-hostname.json"), ownership
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))

    def test_unresolved_ownership_config_rejected(self):
        result = guard.validate_plan(
            _load_plan("allowed-noop.json"),
            _ownership(hostname="", deploymentIdentity="", stateKey=""),
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))

    def test_adversarial_same_type_wrong_address(self):
        result = guard.validate_plan(
            _load_plan("adversarial-same-type-wrong-address.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("unexpected resource address" in e for e in result.errors))
        joined = " ".join(result.errors)
        self.assertNotIn("other_app", joined)

    def test_adversarial_dns_outside_hostname(self):
        result = guard.validate_plan(
            _load_plan("adversarial-dns-outside-hostname.json"), _cloudflare_ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))
        joined = " ".join(result.errors)
        self.assertNotIn("attacker.example.net", joined)
        self.assertNotIn("evil", joined)

    def test_adversarial_noop_wrong_project(self):
        result = guard.validate_plan(
            _load_plan("adversarial-noop-wrong-project.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))
        joined = " ".join(result.errors)
        self.assertNotIn("prj_stolen", joined)
        self.assertNotIn("other-stolen-app", joined)

    def test_adversarial_crafted_allowed_resource_types(self):
        result = guard.validate_ownership_config(
            _ownership(
                allowedResourceTypes=[
                    "vercel_project",
                    "aws_instance",
                    "google_compute_instance",
                ]
            )
        )
        self.assertTrue(result)
        self.assertTrue(any("ownership" in e.lower() for e in result))

    def test_adversarial_arbitrary_action(self):
        result = guard.validate_plan(
            _load_plan("adversarial-arbitrary-action.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("action" in e.lower() for e in result.errors))

    def test_adversarial_secret_like_address_redacted(self):
        result = guard.validate_plan(
            _load_plan("adversarial-secret-like-address.json"), _ownership()
        )
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertNotIn("AKIA_SECRET_SHOULD_NOT_APPEAR", joined)
        self.assertNotIn("module.evil", joined)
        self.assertTrue(
            any(
                "forbidden" in e.lower()
                or "unexpected" in e.lower()
                or "provisioner" in e.lower()
                for e in result.errors
            )
        )

    def test_adversarial_malformed_nested_after_unknown(self):
        result = guard.validate_plan(
            _load_plan("adversarial-malformed-nested.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("malformed" in e.lower() for e in result.errors))

    def test_adversarial_update_rename_unrelated_checks_before(self):
        result = guard.validate_plan(
            _load_plan("adversarial-update-rename-unrelated.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))
        joined = " ".join(result.errors)
        self.assertNotIn("unrelated-victim", joined)
        self.assertNotIn("prj_victim", joined)

    def test_adversarial_domain_wrong_project_binding(self):
        result = guard.validate_plan(
            _load_plan("adversarial-domain-wrong-project-id.json"), _ownership()
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))
        joined = " ".join(result.errors)
        self.assertNotIn("prj_other_team", joined)

    def test_missing_resource_bindings_fail_closed(self):
        own = _ownership()
        del own["resourceBindings"]
        result = guard.validate_plan(_load_plan("allowed-noop.json"), own)
        self.assertFalse(result.ok)
        self.assertTrue(any("ownership" in e.lower() for e in result.errors))

    def test_create_without_trusted_project_expression_rejected(self):
        plan = _load_plan("allowed-fresh-create.json")
        # Strip trusted references from domain resource.
        resources = plan["configuration"]["root_module"]["module_calls"]["vercel"]["module"][
            "resources"
        ]
        for resource in resources:
            if resource.get("name") == "main" and resource.get("type") == "vercel_project_domain":
                resource["expressions"]["project_id"] = {"references": ["var.untrusted"]}
        result = guard.validate_plan(plan, _ownership())
        self.assertFalse(result.ok)
        self.assertTrue(any("project linkage" in e.lower() or "app-005" in e.lower() for e in result.errors))

    def test_cli_does_not_echo_secret_plan_values(self):
        plan_path = FIXTURES / "adversarial-secret-like-address.json"
        own_path = REPO_ROOT / "examples" / "plan-ownership.example.json"
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = guard.main(
                [
                    "--plan",
                    str(plan_path),
                    "--ownership",
                    str(own_path),
                ]
            )
        self.assertNotEqual(code, 0)
        combined = buf_out.getvalue() + buf_err.getvalue()
        self.assertNotIn("AKIA_SECRET_SHOULD_NOT_APPEAR", combined)
        self.assertNotIn("access_token", combined.lower())
        self.assertNotIn("REDACTED_SHOULD_NOT_APPEAR_IN_LOGS", combined)

    def test_cli_accepts_allowed_fixture(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as own_f:
            json.dump(_ownership(), own_f)
            own_path = own_f.name
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = guard.main(
                [
                    "--plan",
                    str(FIXTURES / "allowed-fresh-create.json"),
                    "--ownership",
                    own_path,
                ]
            )
        self.assertEqual(code, 0, buf_out.getvalue() + buf_err.getvalue())


if __name__ == "__main__":
    unittest.main()

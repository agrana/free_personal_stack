#!/usr/bin/env python3
"""Real Terraform plan regression: allowlisted env KEY for_each vs sensitive values.

Exercises actual root locals (extracted from terraform/main.tf) and the real
vercel module source with mock_provider — no network provider credentials.
Synthetic tokens are generated at runtime and must not appear in test logs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = REPO_ROOT / "terraform"
VERCEL_MODULE = TERRAFORM_ROOT / "modules" / "vercel"
PROVIDER_CACHE = TERRAFORM_ROOT / ".terraform" / "providers"

BASE_ENV_KEYS = (
    "NEXT_PUBLIC_SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "NEXT_PUBLIC_SITE_URL",
)
PUBLISHABLE_ENV_KEY = "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _runtime_jwt(*, role: str, seed: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"role": role, "iss": "supabase-fixture", "ref": seed},
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


def _hcl_string(value: str) -> str:
    return json.dumps(value)


def _extract_root_locals_body() -> str:
    main = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    start = main.index("# Prefer modern")
    end = main.index("\n}\n\ncheck", start)
    return main[start:end]


def _redact(text: str, secrets: Iterable[str]) -> str:
    out = text
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, "<redacted>")
    return out


def _assert_no_secrets(text: str, secrets: Iterable[str], *, label: str) -> None:
    for secret in secrets:
        if secret and secret in text:
            raise AssertionError(f"{label} leaked credential material")


def _terraform_available() -> Tuple[bool, str]:
    path = shutil.which("terraform")
    if not path:
        return False, "terraform binary not found on PATH"
    proc = subprocess.run(
        [path, "version", "-json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False, "terraform version failed"
    return True, path


class TerraformVercelEnvForeachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ok, detail = _terraform_available()
        if not ok:
            raise unittest.SkipTest(detail)
        cls.terraform = detail
        cls.locals_body = _extract_root_locals_body()
        if "vercel_env_var_keys" not in cls.locals_body:
            raise AssertionError("root locals must define vercel_env_var_keys allowlist")
        if "nonsensitive(local.supabase_publishable_trimmed != \"\")" not in cls.locals_body:
            raise AssertionError("publishable presence must use metadata-only nonsensitive boundary")
        vercel_main = (VERCEL_MODULE / "main.tf").read_text(encoding="utf-8")
        if "for_each = var.environment_variable_keys" not in vercel_main:
            raise AssertionError("vercel module must for_each allowlisted keys, not the values map")
        if "for_each = var.environment_variables" in vercel_main:
            raise AssertionError("vercel module must not for_each the environment values map")
        if "precondition" not in vercel_main or "supabase_public_keys_safe" not in vercel_main:
            raise AssertionError("vercel env resource must retain public-key precondition")

    def _secrets(self, *values: str) -> List[str]:
        return [v for v in values if v]

    def _write_harness(self, root: Path) -> None:
        module_src = str(VERCEL_MODULE.resolve())
        main = f'''
terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    vercel = {{
      source  = "vercel/vercel"
      version = "~> 1.0"
    }}
  }}
}}

variable "supabase_url" {{
  type      = string
  sensitive = true
}}
variable "supabase_anon_key" {{
  type      = string
  sensitive = true
  default   = ""
}}
variable "supabase_publishable_key" {{
  type      = string
  sensitive = true
  default   = ""
}}
variable "supabase_service_role_key" {{
  type      = string
  sensitive = true
}}
variable "site_url" {{
  type    = string
  default = ""
}}
variable "approved_hostname" {{
  type    = string
  default = "foreach-fixture.example.test"
}}
variable "vercel_project_name" {{
  type    = string
  default = "foreach-fixture"
}}
variable "github_repo" {{
  type    = string
  default = "example/foreach-fixture"
}}

locals {{
  site_url = var.site_url != "" ? var.site_url : "https://${{var.approved_hostname}}"
  {self.locals_body}
}}

module "vercel" {{
  source = {json.dumps(module_src)}

  project_name               = var.vercel_project_name
  domain_name                = var.approved_hostname
  github_repo                = var.github_repo
  team_id                    = ""
  environment_variable_keys  = local.vercel_env_var_keys
  environment_variables      = local.vercel_env_vars
  supabase_public_keys_safe  = local.supabase_public_keys_safe
}}

output "env_keys" {{
  value = sort(tolist(local.vercel_env_var_keys))
}}

output "keys_are_nonsensitive" {{
  value = !issensitive(local.vercel_env_var_keys)
}}

output "publishable_present" {{
  value = local.supabase_publishable_key_present
}}
'''
        (root / "main.tf").write_text(main, encoding="utf-8")

    def _write_gate_harness(self, root: Path) -> None:
        """Locals + terraform_data for_each mirroring the module gate (no providers)."""
        main = f'''
variable "supabase_url" {{
  type      = string
  sensitive = true
}}
variable "supabase_anon_key" {{
  type      = string
  sensitive = true
  default   = ""
}}
variable "supabase_publishable_key" {{
  type      = string
  sensitive = true
  default   = ""
}}
variable "supabase_service_role_key" {{
  type      = string
  sensitive = true
}}
variable "site_url" {{
  type    = string
  default = ""
}}
variable "approved_hostname" {{
  type    = string
  default = "foreach-fixture.example.test"
}}

locals {{
  site_url = var.site_url != "" ? var.site_url : "https://${{var.approved_hostname}}"
  {self.locals_body}
}}

resource "terraform_data" "env_vars" {{
  for_each = local.vercel_env_var_keys
  input    = each.key

  lifecycle {{
    precondition {{
      condition     = local.supabase_public_keys_safe
      error_message = "Public Supabase key required with public-class syntax (sb_publishable_* and/or legacy anon JWT). Privileged sb_secret_*/service_role/supabase_admin values are rejected even when unequal to supabase_service_role_key. Modern and legacy may coexist with different values."
    }}
  }}
}}
'''
        (root / "main.tf").write_text(main, encoding="utf-8")

    def _write_tftest(
        self,
        root: Path,
        *,
        runs: Sequence[Mapping[str, object]],
    ) -> None:
        lines = [
            'mock_provider "vercel" {}',
            "",
        ]
        for run in runs:
            name = str(run["name"])
            variables = run["variables"]
            expect_failures = run.get("expect_failures")
            asserts = run.get("asserts") or []
            lines.append(f'run "{name}" {{')
            lines.append("  command = plan")
            if expect_failures:
                lines.append("  expect_failures = [")
                for addr in expect_failures:
                    lines.append(f"    {addr},")
                lines.append("  ]")
            lines.append("  variables {")
            assert isinstance(variables, Mapping)
            for key, value in variables.items():
                if isinstance(value, bool):
                    rendered = "true" if value else "false"
                elif isinstance(value, str):
                    rendered = _hcl_string(value)
                else:
                    raise TypeError(f"unsupported var type for {key}")
                lines.append(f"    {key} = {rendered}")
            lines.append("  }")
            for assertion in asserts:
                cond = assertion["condition"]
                msg = assertion.get("error_message", "assertion failed")
                lines.append("  assert {")
                lines.append(f"    condition     = {cond}")
                lines.append(f"    error_message = {_hcl_string(str(msg))}")
                lines.append("  }")
            lines.append("}")
            lines.append("")
        tests_dir = root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "foreach.tftest.hcl").write_text("\n".join(lines), encoding="utf-8")

    def _init(self, root: Path) -> None:
        cmd = [self.terraform, "init", "-backend=false", "-input=false", "-no-color"]
        if PROVIDER_CACHE.is_dir():
            cmd.extend([f"-plugin-dir={PROVIDER_CACHE}"])
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            # Fall back to normal init (may download providers once into the tempfile).
            proc = subprocess.run(
                [self.terraform, "init", "-backend=false", "-input=false", "-no-color"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        if proc.returncode != 0:
            raise AssertionError(
                "terraform init failed: " + _redact(proc.stderr[-1500:], [])
            )

    def _run_test(
        self,
        root: Path,
        *,
        secrets: Sequence[str],
        filter_name: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        cmd = [self.terraform, "test", "-no-color"]
        if filter_name:
            cmd.append(f"-filter=tests/{filter_name}")
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        _assert_no_secrets(combined, secrets, label="terraform test output")
        return proc

    def _scenario_vars(
        self,
        *,
        publishable: str,
        anon: str,
        service: str,
        url: str = "https://foreach-fixture.supabase.co",
    ) -> Dict[str, object]:
        return {
            "supabase_url": url,
            "supabase_publishable_key": publishable,
            "supabase_anon_key": anon,
            "supabase_service_role_key": service,
            "site_url": "https://foreach-fixture.example.test",
            "approved_hostname": "foreach-fixture.example.test",
        }

    def test_module_source_uses_allowlisted_keys_only(self) -> None:
        variables = (VERCEL_MODULE / "variables.tf").read_text(encoding="utf-8")
        vercel_main = (VERCEL_MODULE / "main.tf").read_text(encoding="utf-8")
        self.assertIn('variable "environment_variable_keys"', variables)
        self.assertIn("Allowlisted environment variable NAMES only", variables)
        self.assertNotRegex(
            vercel_main,
            re.compile(r"for_each\s*=\s*var\.environment_variables\b"),
        )
        self.assertIn("for_each = var.environment_variable_keys", vercel_main)
        self.assertIn("nonsensitive(", self.locals_body)
        self.assertNotIn("nonsensitive(local.vercel_env_vars)", self.locals_body)
        self.assertNotIn("nonsensitive(var.supabase_service_role_key)", self.locals_body)

    def _plan_with_tfvars(
        self,
        root: Path,
        tfvars: Mapping[str, str],
        *,
        secrets: Sequence[str],
    ) -> subprocess.CompletedProcess:
        tfvars_path = root / "plan.auto.tfvars.json"
        tfvars_path.write_text(json.dumps(dict(tfvars)), encoding="utf-8")
        try:
            plan = subprocess.run(
                [
                    self.terraform,
                    "plan",
                    "-input=false",
                    "-lock=false",
                    "-no-color",
                    "-var-file=plan.auto.tfvars.json",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            if tfvars_path.exists():
                tfvars_path.unlink()
        combined = (plan.stdout or "") + "\n" + (plan.stderr or "")
        _assert_no_secrets(combined, secrets, label="terraform plan")
        return plan

    def test_sensitive_keys_defect_fixed_and_plans(self) -> None:
        publishable = _runtime_publishable("modern-only")
        anon = _runtime_jwt(role="anon", seed="legacy-coexist")
        service = _runtime_jwt(role="service_role", seed="service-a")
        modern = _runtime_publishable("modern-coexist")
        legacy_only_anon = _runtime_jwt(role="anon", seed="legacy-only")
        secrets = self._secrets(publishable, anon, service, modern, legacy_only_anon)

        modern_keys = sorted(BASE_ENV_KEYS + (PUBLISHABLE_ENV_KEY,))
        legacy_keys = sorted(BASE_ENV_KEYS)
        coexist_keys = modern_keys

        with tempfile.TemporaryDirectory(prefix="tf-vercel-foreach-") as tmp:
            root = Path(tmp)
            self._write_harness(root)
            self._write_tftest(
                root,
                runs=[
                    {
                        "name": "modern_only",
                        "variables": self._scenario_vars(
                            publishable=publishable, anon="", service=service
                        ),
                        "asserts": [
                            {
                                "condition": (
                                    "output.keys_are_nonsensitive == true && "
                                    "output.publishable_present == true && "
                                    f'join(",", output.env_keys) == {_hcl_string(",".join(modern_keys))}'
                                ),
                                "error_message": "modern-only must expose allowlisted keys including publishable alias",
                            }
                        ],
                    },
                    {
                        "name": "legacy_only",
                        "variables": self._scenario_vars(
                            publishable="", anon=legacy_only_anon, service=service
                        ),
                        "asserts": [
                            {
                                "condition": (
                                    "output.keys_are_nonsensitive == true && "
                                    "output.publishable_present == false && "
                                    f'join(",", output.env_keys) == {_hcl_string(",".join(legacy_keys))}'
                                ),
                                "error_message": "legacy-only must omit publishable env KEY",
                            }
                        ],
                    },
                    {
                        "name": "coexist_different_valid_keys",
                        "variables": self._scenario_vars(
                            publishable=modern, anon=anon, service=service
                        ),
                        "asserts": [
                            {
                                "condition": (
                                    "output.publishable_present == true && "
                                    f'join(",", output.env_keys) == {_hcl_string(",".join(coexist_keys))}'
                                ),
                                "error_message": "coexistence must keep both public inputs and publishable KEY",
                            }
                        ],
                    },
                ],
            )

            self._init(root)
            tfvars = {
                "supabase_url": "https://foreach-fixture.supabase.co",
                "supabase_publishable_key": publishable,
                "supabase_anon_key": anon,
                "supabase_service_role_key": service,
                "site_url": "https://foreach-fixture.example.test",
            }
            tfvars_path = root / "sens.auto.tfvars.json"
            tfvars_path.write_text(json.dumps(tfvars), encoding="utf-8")
            for expr, expected in (
                ("issensitive(local.vercel_env_var_keys)", "false"),
                ("issensitive(local.supabase_publishable_key_present)", "false"),
                ('issensitive(local.vercel_env_vars["SUPABASE_SERVICE_ROLE_KEY"].value)', "true"),
            ):
                cons = subprocess.run(
                    [
                        self.terraform,
                        "console",
                        "-var-file=sens.auto.tfvars.json",
                        "-no-color",
                    ],
                    cwd=root,
                    input=expr + "\n",
                    capture_output=True,
                    text=True,
                    check=False,
                )
                combined = (cons.stdout or "") + (cons.stderr or "")
                _assert_no_secrets(combined, secrets, label="terraform console")
                self.assertEqual(cons.returncode, 0, _redact(combined, secrets)[-500:])
                self.assertEqual(cons.stdout.strip(), expected, expr)
            tfvars_path.unlink()

            result = self._run_test(root, secrets=secrets)
            if result.returncode != 0:
                self.fail(
                    "terraform test failed:\n"
                    + _redact((result.stdout or "") + (result.stderr or ""), secrets)[-4000:]
                )
            out = result.stdout or ""
            self.assertIn("modern_only", out)
            self.assertIn("legacy_only", out)
            self.assertIn("coexist_different_valid_keys", out)
            self.assertRegex(out, re.compile(r"Success!\s+\d+ passed", re.I))

    def test_invalid_and_privileged_gates_fail_closed(self) -> None:
        service = _runtime_jwt(role="service_role", seed="gate-service")
        privileged = _runtime_jwt(role="service_role", seed="privileged-as-public")
        opaque_secret = _runtime_opaque_secret("gate-reject")
        other_service = _runtime_jwt(role="service_role", seed="other-service-role")
        secrets = self._secrets(service, privileged, opaque_secret, other_service)

        cases = (
            ("privileged_public_jwt", privileged, ""),
            ("missing_public_keys", "", ""),
            ("opaque_secret", opaque_secret, ""),
            ("privileged_unequal_to_service", other_service, ""),
        )

        with tempfile.TemporaryDirectory(prefix="tf-vercel-gate-") as tmp:
            root = Path(tmp)
            self._write_gate_harness(root)
            init = subprocess.run(
                [self.terraform, "init", "-backend=false", "-input=false", "-no-color"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, _redact(init.stderr, secrets)[-800:])

            for label, publishable, anon in cases:
                with self.subTest(label=label):
                    plan = self._plan_with_tfvars(
                        root,
                        {
                            "supabase_url": "https://foreach-fixture.supabase.co",
                            "supabase_publishable_key": publishable,
                            "supabase_anon_key": anon,
                            "supabase_service_role_key": service,
                            "site_url": "https://foreach-fixture.example.test",
                        },
                        secrets=secrets,
                    )
                    combined = (plan.stdout or "") + "\n" + (plan.stderr or "")
                    self.assertNotEqual(plan.returncode, 0, label)
                    self.assertRegex(
                        combined,
                        re.compile(r"Public Supabase key required|precondition", re.I),
                    )

    def test_deliberate_sensitive_map_foreach_still_rejected(self) -> None:
        """Guardrail: for_each over a map tainted by sensitive optional merge must fail."""
        publishable = _runtime_publishable("taint-demo")
        service = _runtime_jwt(role="service_role", seed="taint-service")
        secrets = self._secrets(publishable, service)

        broken = '''
variable "supabase_publishable_key" {
  type      = string
  sensitive = true
}
variable "supabase_service_role_key" {
  type      = string
  sensitive = true
}
variable "supabase_url" {
  type      = string
  sensitive = true
}

locals {
  trimmed = trimspace(var.supabase_publishable_key)
  env = merge(
    {
      NEXT_PUBLIC_SUPABASE_URL = {
        value  = var.supabase_url
        target = ["production"]
      }
      SUPABASE_SERVICE_ROLE_KEY = {
        value  = var.supabase_service_role_key
        target = ["production"]
      }
    },
    local.trimmed != "" ? {
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = {
        value  = local.trimmed
        target = ["production"]
      }
    } : {}
  )
}

resource "terraform_data" "env_vars" {
  for_each = local.env
  input    = each.key
}
'''
        with tempfile.TemporaryDirectory(prefix="tf-sensitive-foreach-") as tmp:
            root = Path(tmp)
            (root / "main.tf").write_text(broken, encoding="utf-8")
            (root / "sens.auto.tfvars.json").write_text(
                json.dumps(
                    {
                        "supabase_publishable_key": publishable,
                        "supabase_service_role_key": service,
                        "supabase_url": "https://foreach-fixture.supabase.co",
                    }
                ),
                encoding="utf-8",
            )
            init = subprocess.run(
                [self.terraform, "init", "-backend=false", "-input=false", "-no-color"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, _redact(init.stderr, secrets)[-500:])
            plan = subprocess.run(
                [
                    self.terraform,
                    "plan",
                    "-input=false",
                    "-lock=false",
                    "-no-color",
                    "-var-file=sens.auto.tfvars.json",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            combined = (plan.stdout or "") + "\n" + (plan.stderr or "")
            _assert_no_secrets(combined, secrets, label="broken for_each plan")
            self.assertNotEqual(plan.returncode, 0)
            self.assertRegex(
                combined,
                re.compile(r"sensitive|for_each", re.I),
            )


if __name__ == "__main__":
    unittest.main()

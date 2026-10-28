#!/usr/bin/env python3
"""APP-008 local bootstrap rehearsal regressions (stub/local only)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "integration" / "bootstrap-rehearsal.py"


def _load_rehearsal():
    if not MODULE_PATH.is_file():
        raise FileNotFoundError(f"missing rehearsal entrypoint: {MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("bootstrap_rehearsal_app008", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootstrapRehearsalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_rehearsal()

    def test_artifacts_label_stub_local_not_live(self) -> None:
        self.assertEqual(self.mod.PROVIDER_MODE, "stub")
        self.assertIs(self.mod.LIVE_CLOUD_REHEARSAL, False)
        labels = self.mod.fixture_labels()
        self.assertEqual(labels["provider_mode"], "stub")
        self.assertIs(labels["live_cloud_rehearsal"], False)
        self.assertIn("fixture", labels["app_slug"])
        self.assertNotEqual(labels["repository_id"], "1051601680")

    def test_sanitized_child_env_strips_provider_secrets_and_sentinels(self) -> None:
        poisoned = {
            "PATH": "/usr/bin",
            "HOME": "/tmp",
            "USER": "sentinel-user-must-not-pass",
            "LOGNAME": "sentinel-logname-must-not-pass",
            "NODE_OPTIONS": "--require=/tmp/sentinel-node-options.js",
            "VERCEL_API_TOKEN": "real-looking-token",
            "VERCEL_TOKEN": "real-looking-token",
            "SUPABASE_URL": "https://real.supabase.co",
            "SUPABASE_ANON_KEY": "sb_publishable_should_not_leak",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-must-not-pass",
            "GITHUB_TOKEN": "ghp_must_not_pass",
            "AWS_SECRET_ACCESS_KEY": "aws-must-not-pass",
            "NEXT_PUBLIC_SUPABASE_URL": "https://should-be-replaced.invalid",
            "UNRELATED_KEEP": "ok",
            "NO_PROXY": "*",
        }
        env = self.mod.sanitized_child_env(
            poisoned,
            extras={
                "NEXT_PUBLIC_SUPABASE_URL": self.mod.SYNTHETIC_SUPABASE_URL,
                "NEXT_PUBLIC_SUPABASE_ANON_KEY": self.mod.SYNTHETIC_SUPABASE_ANON_KEY,
            },
        )
        for banned in (
            "VERCEL_API_TOKEN",
            "VERCEL_TOKEN",
            "SUPABASE_URL",
            "SUPABASE_ANON_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "GITHUB_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "UNRELATED_KEEP",
            "USER",
            "LOGNAME",
            "NODE_OPTIONS",
            "NO_PROXY",
            "no_proxy",
        ):
            self.assertNotIn(banned, env)
        self.assertEqual(env["NEXT_PUBLIC_SUPABASE_URL"], self.mod.SYNTHETIC_SUPABASE_URL)
        self.assertEqual(
            env["NEXT_PUBLIC_SUPABASE_ANON_KEY"], self.mod.SYNTHETIC_SUPABASE_ANON_KEY
        )
        self.assertTrue(env["NEXT_PUBLIC_SUPABASE_ANON_KEY"].startswith("sb_publishable_"))
        self.assertIn("fixture", env["NEXT_PUBLIC_SUPABASE_URL"])
        self.assertNotIn("USER", self.mod.CHILD_ENV_ALLOWLIST)
        self.assertNotIn("LOGNAME", self.mod.CHILD_ENV_ALLOWLIST)
        self.assertNotIn("NODE_OPTIONS", self.mod.CHILD_ENV_ALLOWLIST)

    def test_export_tracked_tree_excludes_dependencies_and_env(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-export-") as tmp:
            work = Path(tmp)
            snapshot = self.mod.export_tracked_app_snapshot(
                source_repo=REPO_ROOT,
                revision=None,
                destination=work / "snapshot",
            )
            root = Path(snapshot["snapshot_root"])
            self.assertTrue((root / "package.json").is_file())
            self.assertTrue((root / "package-lock.json").is_file())
            self.assertTrue((root / "app" / "page.tsx").is_file())
            self.assertFalse((root / "node_modules").exists())
            self.assertFalse((root / ".env").exists())
            self.assertFalse((root / ".next").exists())
            # git archive excludes source .git; independent history is then initialized.
            self.assertTrue((root / ".git").exists())
            self.assertNotEqual(snapshot["source_revision"], snapshot["app_revision"])
            self.assertEqual(snapshot["app_slug"], self.mod.FIXTURE_APP_SLUG)
            self.assertEqual(snapshot["repository_id"], self.mod.FIXTURE_REPOSITORY_ID)
            self.assertEqual(snapshot["app_namespace"], self.mod.FIXTURE_NAMESPACE)
            self.assertIs(snapshot["namespace_provisioned"], False)
            self.assertEqual(snapshot["provider_mode"], "stub")
            self.assertIs(snapshot["live_cloud_rehearsal"], False)

    def test_export_rejects_tracked_env_production_before_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-poison-") as tmp:
            poisoned = Path(tmp) / "poisoned-src"
            dest = Path(tmp) / "must-not-exist"
            poisoned.mkdir()
            subprocess.run(
                ["git", "init", "-q", "-b", "main"],
                cwd=str(poisoned),
                check=True,
                capture_output=True,
            )
            (poisoned / "README.md").write_text("fixture\n", encoding="utf-8")
            (poisoned / ".env.production").write_text(
                "SUPER_SECRET=must-never-print-or-export\n", encoding="utf-8"
            )
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin"),
                "HOME": tmp,
                "GIT_AUTHOR_NAME": "Poison Fixture",
                "GIT_AUTHOR_EMAIL": "poison@example.invalid",
                "GIT_COMMITTER_NAME": "Poison Fixture",
                "GIT_COMMITTER_EMAIL": "poison@example.invalid",
            }
            subprocess.run(
                ["git", "add", "-A", "-f", ".env.production", "README.md"],
                cwd=str(poisoned),
                check=True,
                env=env,
            )
            tracked = subprocess.run(
                ["git", "ls-files"],
                cwd=str(poisoned),
                check=True,
                capture_output=True,
                text=True,
                env=env,
            ).stdout
            self.assertIn(".env.production", tracked)
            subprocess.run(
                ["git", "commit", "-qm", "poison: tracked dotenv"],
                cwd=str(poisoned),
                check=True,
                env=env,
                capture_output=True,
            )
            with self.assertRaises(self.mod.RehearsalError) as ctx:
                self.mod.export_tracked_app_snapshot(
                    source_repo=poisoned,
                    revision=None,
                    destination=dest,
                )
            self.assertEqual(str(ctx.exception), "tracked_secret_or_cache_path_rejected")
            self.assertFalse(dest.exists())

    def test_arena_status_schema_is_static_source_review_not_live(self) -> None:
        comparison = self.mod.compare_status_schema_to_arena()
        self.assertEqual(comparison["provider_mode"], "stub")
        self.assertIs(comparison["live_cloud_rehearsal"], False)
        self.assertFalse(comparison["invented_live_manifest"])
        self.assertEqual(comparison["review_kind"], "static_source_review_reference")
        self.assertIs(comparison["runtime_live_manifest_verified"], False)
        pointer = comparison["arena_evidence_pointer"]
        self.assertEqual(pointer["review_kind"], "static_source_review_reference")
        self.assertIs(pointer["runtime_live_manifest_verified"], False)
        self.assertIn("deploy-arena-shell-cli.py", pointer["source_path"])
        self.assertEqual(
            pointer["reviewed_source_sha_literal"],
            "fa9b793dec761ac859ada434510c79b7248635e4",
        )
        self.assertIn("APP-013", pointer["note"])
        for status in (
            "page_and_health_verified",
            "ready_but_verification_protected",
            "project_readback",
        ):
            self.assertIn(status, comparison["shared_status_categories"])
        self.assertIn("deployment_created", comparison["arena_only_status_categories"])
        self.assertIn("plan", comparison["template_launcher_only_status_categories"])
        self.assertIn("setup_required", comparison["template_launcher_only_status_categories"])
        self.assertEqual(
            comparison["absent_or_different"]["actions_and_second_repo_provider_e2e"],
            "absent_owned_by_APP-013",
        )

    def test_launcher_loaded_from_snapshot_not_controlling_repo(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-launcher-src-") as tmp:
            work = Path(tmp)
            snapshot = self.mod.export_tracked_app_snapshot(
                source_repo=REPO_ROOT,
                revision=None,
                destination=work / "snapshot",
            )
            root = Path(snapshot["snapshot_root"])
            launcher_path = root / "scripts" / "launch-app.py"
            original = launcher_path.read_text(encoding="utf-8")
            sentinel = 'APP008_SNAPSHOT_LAUNCHER_SENTINEL = "snapshot-launcher-active"\n'
            if "APP008_SNAPSHOT_LAUNCHER_SENTINEL" not in original:
                launcher_path.write_text(sentinel + original, encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin"),
                "HOME": tmp,
                "GIT_AUTHOR_NAME": "APP008 Fixture",
                "GIT_AUTHOR_EMAIL": "app008-fixture@example.invalid",
                "GIT_COMMITTER_NAME": "APP008 Fixture",
                "GIT_COMMITTER_EMAIL": "app008-fixture@example.invalid",
            }
            subprocess.run(["git", "add", "scripts/launch-app.py"], cwd=str(root), check=True, env=env)
            subprocess.run(
                ["git", "commit", "-qm", "fixture: mark snapshot launcher"],
                cwd=str(root),
                check=True,
                env=env,
                capture_output=True,
            )
            app_revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip().lower()

            controlling = REPO_ROOT / "scripts" / "launch-app.py"
            controlling_text = controlling.read_text(encoding="utf-8")
            self.assertNotIn("APP008_SNAPSHOT_LAUNCHER_SENTINEL", controlling_text)

            loaded = self.mod._load_launcher(root)
            self.assertTrue(str(Path(loaded.__file__).resolve()).startswith(str(root.resolve())))
            self.assertEqual(
                getattr(loaded, "APP008_SNAPSHOT_LAUNCHER_SENTINEL", None),
                "snapshot-launcher-active",
            )

            result = self.mod.run_launcher_scenarios(
                snapshot_root=root,
                app_revision=app_revision,
                work_dir=work / "stub",
            )
            self.assertTrue(result["unchanged_noop"])
            self.assertTrue(result["events"])
            verified = [
                e for e in result["events"] if e.get("status") == "page_and_health_verified"
            ]
            self.assertTrue(verified)
            self.assertTrue(all(e.get("url") for e in verified))
            self.assertTrue(all(e.get("source_sha") == app_revision for e in verified))

    def test_collision_interrupt_resume_and_unchanged_noop_capture_real_events(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-launch-") as tmp:
            work = Path(tmp)
            snapshot = self.mod.export_tracked_app_snapshot(
                source_repo=REPO_ROOT,
                revision=None,
                destination=work / "snapshot",
            )
            result = self.mod.run_launcher_scenarios(
                snapshot_root=Path(snapshot["snapshot_root"]),
                app_revision=snapshot["app_revision"],
                work_dir=work / "stub",
            )
            self.assertEqual(result["provider_mode"], "stub")
            self.assertIs(result["live_cloud_rehearsal"], False)
            self.assertTrue(result["collision_rejected"])
            self.assertTrue(result["interrupt_recovered"])
            self.assertTrue(result["unchanged_noop"])
            self.assertEqual(result["project_create_count"], 1)
            self.assertEqual(result["deployment_count_after_first"], 1)
            self.assertEqual(result["deployment_count_after_noop"], 1)
            self.assertGreaterEqual(result["operator_step_count"], 4)
            self.assertIs(result["os_network_sandbox"], False)
            self.assertEqual(
                result["safety_boundary"], "patched_provider_clients_and_stub_cli"
            )
            for key in ("project_id", "team_id", "deployment_id"):
                self.assertIn("fixture", str(result["synthetic_ids"][key]).lower())
            scenarios = {inv["scenario"] for inv in result["invocations"]}
            self.assertIn("first_deploy", scenarios)
            self.assertIn("interrupt", scenarios)
            self.assertIn("resume", scenarios)
            first = next(i for i in result["invocations"] if i["scenario"] == "first_deploy")
            statuses = [e["status"] for e in first["events"]]
            self.assertIn("plan", statuses)
            self.assertIn("page_and_health_verified", statuses)
            verified = next(
                e for e in first["events"] if e["status"] == "page_and_health_verified"
            )
            self.assertTrue(verified.get("url"))
            self.assertEqual(verified.get("source_sha"), snapshot["app_revision"])
            interrupt = next(i for i in result["invocations"] if i["scenario"] == "interrupt")
            self.assertEqual(interrupt["failure"], "cli_failed")
            self.assertTrue(any(e.get("status") == "plan" for e in interrupt["events"]))

    def test_launcher_scenario_env_clear_drops_poison_sentinels(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-envclear-") as tmp:
            work = Path(tmp)
            snapshot = self.mod.export_tracked_app_snapshot(
                source_repo=REPO_ROOT,
                revision=None,
                destination=work / "snapshot",
            )
            poison = {
                "USER": "sentinel-user-must-not-pass",
                "LOGNAME": "sentinel-logname-must-not-pass",
                "NODE_OPTIONS": "--require=/tmp/sentinel-node-options.js",
                "AWS_SECRET_ACCESS_KEY": "aws-sentinel",
                "VERCEL_API_TOKEN": "should-be-replaced-by-fixture",
            }
            seen_during: Dict[str, Any] = {}
            original_run = self.mod._load_launcher

            def tracking_load(snapshot_root: Path):
                app = original_run(snapshot_root)
                real_run = app.run

                def run_and_capture(*args: Any, **kwargs: Any):
                    seen_during["USER"] = os.environ.get("USER")
                    seen_during["LOGNAME"] = os.environ.get("LOGNAME")
                    seen_during["NODE_OPTIONS"] = os.environ.get("NODE_OPTIONS")
                    seen_during["AWS_SECRET_ACCESS_KEY"] = os.environ.get(
                        "AWS_SECRET_ACCESS_KEY"
                    )
                    seen_during["VERCEL_API_TOKEN"] = os.environ.get("VERCEL_API_TOKEN")
                    return real_run(*args, **kwargs)

                app.run = run_and_capture  # type: ignore[method-assign]
                return app

            with patch.dict(os.environ, poison, clear=False):
                with patch.object(self.mod, "_load_launcher", side_effect=tracking_load):
                    result = self.mod.run_launcher_scenarios(
                        snapshot_root=Path(snapshot["snapshot_root"]),
                        app_revision=snapshot["app_revision"],
                        work_dir=work / "stub",
                    )
            self.assertTrue(result["unchanged_noop"])
            self.assertIsNone(seen_during.get("USER"))
            self.assertIsNone(seen_during.get("LOGNAME"))
            self.assertIsNone(seen_during.get("NODE_OPTIONS"))
            self.assertIsNone(seen_during.get("AWS_SECRET_ACCESS_KEY"))
            self.assertEqual(seen_during.get("VERCEL_API_TOKEN"), "fixture-offline-token")
            child = self.mod.sanitized_child_env({**poison, "PATH": "/usr/bin", "HOME": "/tmp"})
            for banned in ("USER", "LOGNAME", "NODE_OPTIONS", "AWS_SECRET_ACCESS_KEY"):
                self.assertNotIn(banned, child)

    def test_caller_supplied_work_root_is_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-caller-") as tmp:
            caller = Path(tmp) / "keep-me"
            caller.mkdir()
            canary = caller / "canary.txt"
            canary.write_text("preserve", encoding="utf-8")
            self.mod.export_tracked_app_snapshot(
                source_repo=REPO_ROOT,
                revision=None,
                destination=caller / "snapshot",
            )
            self.assertTrue(canary.is_file())
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve")
            self.assertTrue(caller.is_dir())

    def test_cli_skip_npm_is_contract_only_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-cli-skip-") as tmp:
            out = Path(tmp) / "evidence"
            work = Path(tmp) / "work"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--output-dir",
                    str(out),
                    "--work-root",
                    str(work),
                    "--skip-npm",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])
            summary = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(summary["status"], "local_contract_only")
            self.assertIs(summary["acceptance_complete"], False)
            self.assertIs(summary["build_executed"], False)
            evidence = json.loads(
                (out / "app008-local-rehearsal-evidence.json").read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["acceptance_status"], "local_contract_only")
            self.assertIs(evidence["second_app_build"]["build_executed"], False)

    def test_cli_build_failure_returns_nonzero(self) -> None:
        import contextlib
        import io

        with patch.object(
            self.mod,
            "probe_runtime",
            return_value={
                "node_version": "v22.0.0",
                "npm_version": "10.0.0",
                "node_major": 22,
                "declared_engines_node": "24.x",
                "required_node_major": 24,
                "compatible_with_declared_engines": False,
                "pinned_ephemeral_node_package": self.mod.PINNED_NODE_PACKAGE,
            },
        ):
            with self.assertRaises(self.mod.RehearsalError) as ctx:
                self.mod.run_snapshot_quality(REPO_ROOT, skip_npm=False)
            self.assertEqual(str(ctx.exception), "runtime_mismatch")

        def failing_quality(*_a: Any, **_k: Any) -> Dict[str, Any]:
            return {
                "provider_mode": "stub",
                "live_cloud_rehearsal": False,
                "skipped": False,
                "executed": True,
                "build_executed": True,
                "exit_code": 1,
                "failed_step": "npm_build",
                "steps": [
                    {
                        "step": "npm_build",
                        "exit_code": 1,
                        "executed": True,
                        "elapsed_seconds": 0.1,
                    }
                ],
                "synthetic_public_config_labeled": True,
                "build_revision": "a" * 40,
                "runtime": {
                    "node_version": "v24.22.1",
                    "npm_version": "10.9.8",
                    "node_major": 24,
                    "declared_engines_node": "24.x",
                    "compatible_with_declared_engines": True,
                    "pinned_ephemeral_node_package": self.mod.PINNED_NODE_PACKAGE,
                },
            }

        with tempfile.TemporaryDirectory(prefix="app008-cli-fail-") as tmp:
            out = Path(tmp) / "evidence"
            work = Path(tmp) / "work"
            buf = io.StringIO()
            with patch.object(self.mod, "run_snapshot_quality", side_effect=failing_quality):
                with contextlib.redirect_stdout(buf):
                    code = self.mod.main([
                        "--output-dir", str(out),
                        "--work-root", str(work),
                    ])
            self.assertEqual(code, 1)
            summary = json.loads(buf.getvalue().strip().splitlines()[-1])
            self.assertEqual(summary["status"], "local_rehearsal_incomplete")
            self.assertIs(summary["build_executed"], True)
            self.assertEqual(summary["second_app_build_exit_code"], 1)

    def test_stub_checks_survive_python_optimize(self) -> None:
        with tempfile.TemporaryDirectory(prefix="app008-stub-opt-") as tmp:
            stub = Path(tmp) / "vercel"
            stub.write_text(self.mod.VERCEL_CLI_STUB, encoding="utf-8")
            stub.chmod(0o755)
            state = stub.with_suffix(".json")
            state.write_text(
                json.dumps({"project": None, "envs": [], "calls": []}), encoding="utf-8"
            )
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin"),
                "HOME": tmp,
                "VERCEL_TOKEN": "wrong-token",
                "PYTHONOPTIMIZE": "2",
            }
            result = subprocess.run(
                [sys.executable, "-OO", str(stub), "project", "inspect", "prj_x", "--scope", "team_x"],
                cwd=str(tmp),
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stub_token", (result.stderr or "") + (result.stdout or ""))


class BootstrapRehearsalBuildTests(unittest.TestCase):
    """Heavy path: real npm install/build inside a disposable second-app snapshot."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_rehearsal()

    def test_full_local_rehearsal_builds_second_app_and_writes_evidence(self) -> None:
        runtime = self.mod.probe_runtime()
        if not runtime.get("compatible_with_declared_engines"):
            self.fail(
                "full acceptance requires Node 24 on PATH "
                f"(got {runtime.get('node_version')!r}); "
                f"use npx --yes --package={self.mod.PINNED_NODE_PACKAGE}"
            )
        with tempfile.TemporaryDirectory(prefix="app008-full-") as tmp:
            out = Path(tmp) / "evidence"
            out.mkdir()
            manifest: Dict[str, Any] = self.mod.run_local_rehearsal(
                source_repo=REPO_ROOT,
                output_dir=out,
                skip_npm=False,
            )
            self.assertEqual(manifest["provider_mode"], "stub")
            self.assertIs(manifest["live_cloud_rehearsal"], False)
            self.assertEqual(manifest["acceptance_status"], "local_rehearsal_complete")
            self.assertTrue(manifest["second_app_build"]["executed"])
            self.assertTrue(manifest["second_app_build"]["build_executed"])
            self.assertEqual(manifest["second_app_build"]["exit_code"], 0)
            self.assertEqual(
                manifest["second_app_build"]["build_revision"],
                manifest["snapshot"]["app_revision"],
            )
            self.assertNotEqual(
                manifest["second_app_build"]["build_revision"],
                "abcdef0123456789abcdef0123456789abcdef01",
            )
            self.assertTrue(manifest["launcher_scenarios"]["unchanged_noop"])
            self.assertGreater(manifest["operator_step_count"], 0)
            self.assertGreater(manifest["elapsed_seconds_total"], 0)
            self.assertEqual(manifest["runtime"]["node_major"], 24)
            self.assertTrue(manifest["runtime"]["compatible_with_declared_engines"])
            evidence = out / "app008-local-rehearsal-evidence.json"
            self.assertTrue(evidence.is_file())
            loaded = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(loaded["provider_mode"], "stub")
            self.assertIs(loaded["live_cloud_rehearsal"], False)
            self.assertEqual(
                loaded["status_schema_comparison"]["review_kind"],
                "static_source_review_reference",
            )
            health_steps = [
                s for s in loaded["second_app_build"]["steps"]
                if s["step"] == "built_health_revision_check"
            ]
            self.assertTrue(health_steps)
            self.assertEqual(health_steps[0]["exit_code"], 0)
            blob = evidence.read_text(encoding="utf-8")
            for banned in ("VERCEL_API_TOKEN", "sb_secret_", "service_role", "ghp_"):
                self.assertNotIn(banned, blob)
            # No absolute home path leakage of real usernames in evidence.
            self.assertNotIn("/home/", blob)


if __name__ == "__main__":
    unittest.main()

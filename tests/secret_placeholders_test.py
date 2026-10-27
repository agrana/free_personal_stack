"""Offline GitHub boundary and executable onboarding regression tests."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup = load("placeholder_setup", "scripts/setup-secret-placeholders.py")
preflight = load("placeholder_preflight", "scripts/secrets-preflight.py")
NAMES = sorted(json.loads(setup.EXAMPLE.read_text())["secretAliases"].values())


class PlaceholderTests(unittest.TestCase):
    def invoke(self, apply=False, existing=(), fail=None, inventory=None, identity="other-owner/generated-app", profile="legacy"):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if args[1:3] == ["secret", "set"]:
                output = ""
                code = 1 if args[3] == fail else 0
            elif args[1:4] == ["api", "--paginate", "--slurp"]:
                output = inventory if inventory is not None else json.dumps([
                    {"secrets": [{"name": name} for name in existing]}, {"secrets": []}])
                code = 1 if fail == "list" else 0
            else:
                output, code = json.dumps({"full_name": identity}), 0
            return subprocess.CompletedProcess(args, code, output, "DO_NOT_PRINT_REMOTE_CONTENT")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(setup.subprocess, "run", side_effect=run), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = setup.main(["--repo", "other-owner/generated-app", "--profile", profile] + (["--apply"] if apply else []))
        self.assertNotIn("DO_NOT_PRINT_REMOTE_CONTENT", out.getvalue() + err.getvalue())
        return rc, calls, out.getvalue()

    def test_default_dry_run_lists_only_seven_example_aliases(self):
        rc, calls, output = self.invoke()
        self.assertEqual(rc, 0)
        self.assertEqual(output.count("Would create placeholder:"), 7)
        self.assertFalse(any(c[0][1] == "secret" for c in calls))

    def test_launch_profile_creates_only_four_names_and_preserves_existing(self):
        rc, calls, output = self.invoke(True, ("SUPABASE_URL",), profile="launch")
        self.assertEqual(rc, 0)
        writes = [c[0][3] for c in calls if c[0][1] == "secret"]
        self.assertEqual(writes, sorted(set(setup.LAUNCH_NAMES) - {"SUPABASE_URL"}))
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", output)

    def test_apply_preserves_existing_and_sends_only_sentinel_via_stdin(self):
        rc, calls, _ = self.invoke(True, NAMES[:2])
        self.assertEqual(rc, 0)
        writes = [c for c in calls if c[0][1] == "secret"]
        self.assertEqual([c[0][3] for c in writes], NAMES[2:])
        for args, kwargs in writes:
            self.assertEqual(args[-2:], ["--repo", "other-owner/generated-app"])
            self.assertEqual(kwargs["input"], setup.SENTINEL)
            self.assertNotIn(setup.SENTINEL, args)
        self.assertEqual(calls[1][0][1:4], ["api", "--paginate", "--slurp"])

    def test_rerun_preserves_all_existing_entries(self):
        rc, calls, _ = self.invoke(True, NAMES)
        self.assertEqual(rc, 0)
        self.assertFalse(any(c[0][1] == "secret" for c in calls))

    def test_inventory_errors_fail_closed(self):
        for kwargs in ({"fail": "list"}, {"inventory": "not JSON"}, {"inventory": "[]"}, {"inventory": '[{"secrets": null}]'}):
            with self.subTest(kwargs=kwargs):
                rc, calls, _ = self.invoke(True, **kwargs)
                self.assertEqual(rc, 1)
                self.assertFalse(any(c[0][1] == "secret" for c in calls))

    def test_target_mismatch_fails_before_list_or_writes(self):
        rc, calls, _ = self.invoke(True, identity="upstream/template")
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)

    def test_partial_failure_is_nonzero_and_stops(self):
        rc, calls, _ = self.invoke(True, fail=NAMES[1])
        self.assertEqual(rc, 1)
        self.assertEqual([c[0][3] for c in calls if c[0][1] == "secret"], NAMES[:2])

    def test_invalid_target_never_executes_gh(self):
        with patch.object(setup.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(["--repo", "https://github.com/upstream/template", "--apply"]), 1)
            run.assert_not_called()

    def test_missing_gh_is_nonzero(self):
        with patch.object(setup.subprocess, "run", side_effect=FileNotFoundError), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(["--repo", "owner/repo"]), 1)

    def test_sentinel_is_missing_for_all_central_source_aliases(self):
        aliases = preflight.ALLOWLISTED_ALIASES | preflight.PUBLIC_CONFIG_ALLOWLIST
        for value in (setup.SENTINEL, " \n" + setup.SENTINEL + "\t "):
            source = preflight.CallerProvidedEnvSource({name: value for name in aliases})
            for name in aliases:
                self.assertIsNone(source.resolve(name), name)
            self.assertEqual(source.known_server_only_values(), [])

    def test_real_cli_dry_run_with_stub_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "gh"
            stub.write_text('#!' + sys.executable + '\nimport json, sys\nif sys.argv[1:3] == ["api", "repos/owner/app"]:\n print(json.dumps({"full_name": "owner/app"}))\nelif sys.argv[1:4] == ["api", "--paginate", "--slurp"]:\n print(\'[ {"secrets": []} ]\')\nelse:\n sys.exit(99)\n')
            stub.chmod(0o700)
            result = subprocess.run([sys.executable, str(ROOT / "scripts/setup-secret-placeholders.py"), "--repo", "owner/app"], env={**os.environ, "PATH": tmp}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("Would create placeholder:"), 7)

    def test_workflow_all_sentinels_fail_as_missing(self):
        workflow = (ROOT / ".github/workflows/setup.yml").read_text()
        block = workflow.split("        run: |\n", 1)[1].split("\n  create-template:", 1)[0]
        script = "\n".join(line[10:] for line in block.splitlines())
        names = NAMES + ["DOMAIN_NAME", "SUPABASE_DATABASE_PASSWORD"]
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary"
            result = subprocess.run(["bash", "-c", script], env={**os.environ, **dict.fromkeys(names, setup.SENTINEL), "GITHUB_STEP_SUMMARY": str(summary)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            report = summary.read_text()
            self.assertIn("9 required secret(s)", report)
            self.assertNotIn("All required secrets are configured", report)
            self.assertNotIn(setup.SENTINEL, report)
            for name in names:
                self.assertIn(name, report)


if __name__ == "__main__":
    unittest.main()

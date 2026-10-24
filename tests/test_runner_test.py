#!/usr/bin/env python3
"""Regression tests for the deterministic multi-suite runner (APP-006)."""

from __future__ import annotations

import importlib.util
import io
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = REPO_ROOT / "scripts" / "run-all-tests.py"
    spec = importlib.util.spec_from_file_location("run_all_tests_app006", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load run-all-tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRunnerCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load_runner()

    def test_explicit_inventory_includes_hyphenated_and_underscore_suites(self) -> None:
        rels = set(self.runner.EXPLICIT_SUITES)
        for required in (
            "tests/config-validation_test.py",
            "tests/plan-guard_test.py",
            "tests/infra-safety_test.py",
            "tests/deployment_verification_test.py",
            "tests/workflow_safety_test.py",
            "tests/shared_db_isolation_test.py",
            "tests/app_initializer_test.py",
            "tests/secrets_preflight_test.py",
            "tests/test_runner_test.py",
        ):
            self.assertIn(required, rels)

    def test_explicit_inventory_covers_every_on_disk_test_module(self) -> None:
        on_disk = {p.resolve() for p in self.runner.discover_test_files()}
        explicit = {p.resolve() for p in self.runner.explicit_suite_paths()}
        missing = on_disk - explicit
        extra = explicit - on_disk
        self.assertFalse(
            missing,
            f"runner omits on-disk suites (hyphen discover gap): {sorted(missing)}",
        )
        self.assertFalse(extra, f"runner lists missing files: {sorted(extra)}")

    def test_unittest_discover_cannot_import_hyphenated_suites(self) -> None:
        """fnmatch finds hyphenated names, but discover cannot import them as modules."""
        import fnmatch

        self.assertTrue(fnmatch.fnmatch("config-validation_test.py", "*_test.py"))
        with self.assertRaises((ModuleNotFoundError, ImportError)):
            __import__("config-validation_test")
        rels = set(self.runner.EXPLICIT_SUITES)
        self.assertIn("tests/config-validation_test.py", rels)
        self.assertIn("tests/plan-guard_test.py", rels)
        self.assertIn("tests/infra-safety_test.py", rels)

    def test_runner_propagates_failure_from_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite_path = root / "failing_suite_test.py"
            suite_path.write_text(
                textwrap.dedent(
                    """
                    import unittest
                    class Failing(unittest.TestCase):
                        def test_boom(self):
                            self.fail("intentional failure for runner")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            stream = io.StringIO()
            result = self.runner.run_suites(
                [suite_path], repo_root=root, verbosity=1, stream=stream
            )
            self.assertFalse(result.wasSuccessful())
            self.assertGreaterEqual(len(result.failures) + len(result.errors), 1)

    def test_runner_propagates_error_from_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite_path = root / "error_suite_test.py"
            suite_path.write_text(
                textwrap.dedent(
                    """
                    import unittest
                    class Broken(unittest.TestCase):
                        def test_error(self):
                            raise RuntimeError("intentional error for runner")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            stream = io.StringIO()
            result = self.runner.run_suites(
                [suite_path], repo_root=root, verbosity=1, stream=stream
            )
            self.assertFalse(result.wasSuccessful())
            self.assertGreaterEqual(len(result.errors), 1)

    def test_runner_success_on_passing_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite_path = root / "passing_suite_test.py"
            suite_path.write_text(
                textwrap.dedent(
                    """
                    import unittest
                    class Passing(unittest.TestCase):
                        def test_ok(self):
                            self.assertTrue(True)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            stream = io.StringIO()
            result = self.runner.run_suites(
                [suite_path], repo_root=root, verbosity=1, stream=stream
            )
            self.assertTrue(result.wasSuccessful())

    def test_package_json_uses_unified_runner(self) -> None:
        import json

        pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        test_script = pkg["scripts"]["test"]
        self.assertIn("run-all-tests.py", test_script)
        self.assertNotIn("unittest discover", test_script)


if __name__ == "__main__":
    unittest.main()

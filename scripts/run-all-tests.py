#!/usr/bin/env python3
"""Deterministic test runner for all explicit suites (hyphen + underscore).

Invokes every listed suite via unittest file paths so hyphenated modules are
not omitted by discover patterns like '*_test.py'. Propagates any failure or
error as a non-zero exit. Skips alone do not fail the process.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# Explicit inventory — keep in sync with tests/ on disk (see tests/test_runner_test.py).
EXPLICIT_SUITES: Tuple[str, ...] = (
    "tests/config-validation_test.py",
    "tests/plan-guard_test.py",
    "tests/infra-safety_test.py",
    "tests/secrets_preflight_test.py",
    "tests/shared_db_isolation_test.py",
    "tests/app_initializer_test.py",
    "tests/deployment_verification_test.py",
    "tests/workflow_safety_test.py",
    "tests/test_runner_test.py",
)


def explicit_suite_paths(repo_root: Optional[Path] = None) -> List[Path]:
    root = repo_root or REPO_ROOT
    return [root / rel for rel in EXPLICIT_SUITES]


def discover_test_files(repo_root: Optional[Path] = None) -> List[Path]:
    """All *test*.py files directly under tests/ (excluding __pycache__)."""
    root = (repo_root or REPO_ROOT) / "tests"
    found: List[Path] = []
    for path in sorted(root.glob("*test*.py")):
        if path.name.startswith("_"):
            continue
        found.append(path)
    return found


def load_tests_from_path(path: Path) -> unittest.TestSuite:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    module_name = f"app006_suite_{path.stem.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


def build_suite(
    paths: Optional[Iterable[Path]] = None,
    *,
    repo_root: Optional[Path] = None,
) -> unittest.TestSuite:
    root = repo_root or REPO_ROOT
    selected = list(paths) if paths is not None else explicit_suite_paths(root)
    suite = unittest.TestSuite()
    for path in selected:
        suite.addTests(load_tests_from_path(path))
    return suite


def run_suites(
    paths: Optional[Sequence[Path]] = None,
    *,
    repo_root: Optional[Path] = None,
    verbosity: int = 2,
    stream=None,
) -> unittest.TestResult:
    suite = build_suite(paths, repo_root=repo_root)
    runner = unittest.TextTestRunner(
        verbosity=verbosity,
        stream=stream or sys.stderr,
        failfast=False,
    )
    return runner.run(suite)


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ = argv  # reserved for future filters; keep CLI stable
    missing = [p for p in explicit_suite_paths() if not p.is_file()]
    if missing:
        for path in missing:
            print(f"FAIL missing suite: {path}", file=sys.stderr)
        return 1
    result = run_suites()
    if result.wasSuccessful():
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

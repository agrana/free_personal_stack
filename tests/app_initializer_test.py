#!/usr/bin/env python3
"""APP-005 initializer regression tests (offline generation + fixture adapters).

No live provider, DNS, Terraform apply, or remote database mutations.
Live apply remains gated by BlockedLiveApplyAdapter.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(REPO_ROOT))

from bootstrap import PINNED_TEMPLATE_REVISION  # noqa: E402
from bootstrap.adapters import (  # noqa: E402
    BlockedLiveApplyAdapter,
    CapabilityReport,
    ClaimingLiveAdapter,
    RecordingApplyAdapter,
)
from bootstrap.engine import apply_app, plan_app, resume_app  # noqa: E402
from bootstrap.errors import (  # noqa: E402
    ERR_ADOPT_BY_NAME,
    ERR_CAPABILITY_BINDING,
    ERR_CAPABILITY_UNVERIFIED,
    ERR_CHECKPOINT_RESET,
    ERR_CONCURRENT,
    ERR_DRIFT_CONFIG,
    ERR_DRIFT_PLAN,
    ERR_GENERATED_EDIT,
    ERR_LIVE_CLAIM,
    ERR_LIVE_MISSING,
    ERR_OUTPUT_ROOT,
    ERR_OVERWRITE,
    ERR_PATH_ESCAPE,
    ERR_PLAN_DIGEST,
    ERR_SCHEMA,
    ERR_SYMLINK,
    ERR_TEMPLATE_DIRTY,
    ERR_TEMPLATE_REV,
    ERR_TEMPLATE_SNAPSHOT,
    ERR_TEMPLATE_SOURCE_UNAVAILABLE,
    ERR_TEMPLATE_SOURCE_UNTRUSTED,
    InitializerError,
)
from bootstrap.lock import AppLock, exclusive_app_lock  # noqa: E402
from bootstrap.paths import iter_template_files  # noqa: E402
from bootstrap.provenance import (  # noqa: E402
    DEFAULT_REVIEWED_TEMPLATE_SOURCE_SSH,
    DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
    approve_reviewed_template_source,
    ensure_pinned_commit_object,
    is_reviewed_template_remote,
    normalize_git_remote_identity,
)
from bootstrap.safe_fs import ensure_owned_directory  # noqa: E402
from bootstrap.scaffold import materialize_scaffold  # noqa: E402
from bootstrap.snapshot import (  # noqa: E402
    assert_git_commit_object,
    verify_template_snapshot,
)

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "bootstrap"
TEMPLATE_ROOT = FIXTURE_ROOT / "template"
CONFIG_ARENA = FIXTURE_ROOT / "configs" / "agent-arena.cloudflare.json"
CONFIG_ARENA_UNRESOLVED = FIXTURE_ROOT / "configs" / "agent-arena.unresolved.json"
CONFIG_BOARD = FIXTURE_ROOT / "configs" / "decisions-board.cloudflare.json"

# Scoped overrides for temporary fixture clones/fetches. Background
# `git maintenance` / `gc` (maintenance.autoDetach / gc.autoDetach) can race
# TemporaryDirectory cleanup and raise OSError ENOTEMPTY on .git/objects.
_FIXTURE_GIT_CONFIG_PAIRS: tuple[tuple[str, str], ...] = (
    ("gc.auto", "0"),
    ("gc.autoDetach", "false"),
    ("maintenance.auto", "false"),
    ("maintenance.autoDetach", "false"),
)


def _fixture_git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return env with deterministic, command-scoped git gc/maintenance off."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_"):
            del env[key]
        elif key.startswith("GIT_CONFIG_VALUE_"):
            del env[key]
    env["GIT_CONFIG_COUNT"] = str(len(_FIXTURE_GIT_CONFIG_PAIRS))
    for index, (config_key, config_value) in enumerate(_FIXTURE_GIT_CONFIG_PAIRS):
        env[f"GIT_CONFIG_KEY_{index}"] = config_key
        env[f"GIT_CONFIG_VALUE_{index}"] = config_value
    return env


def _run_fixture_git(
    args: list[str],
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run git for temp fixtures with gc/maintenance auto+detach suppressed."""
    env = kwargs.pop("env", None)
    return subprocess.run(
        ["git", *args],
        env=_fixture_git_env(env),
        **kwargs,
    )


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_repo_pin_present() -> str:
    """Materialize PINNED_TEMPLATE_REVISION into REPO_ROOT when missing.

    No-op when the commit object is already present (common after CI ensure or
    a fetched allowlisted ``template`` remote). Does not weaken snapshot checks.
    """
    try:
        return assert_git_commit_object(REPO_ROOT, PINNED_TEMPLATE_REVISION)
    except InitializerError:
        pass
    try:
        return ensure_pinned_commit_object(
            REPO_ROOT,
            PINNED_TEMPLATE_REVISION,
            remote_name="template",
        )
    except InitializerError:
        return ensure_pinned_commit_object(
            REPO_ROOT,
            PINNED_TEMPLATE_REVISION,
            reviewed_source_url=DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
        )


def setUpModule() -> None:
    _ensure_repo_pin_present()


def _git_commit(repo: Path, message: str) -> str:
    committed = _run_fixture_git(
        [
            "-C",
            str(repo),
            "-c",
            "user.name=Synthetic Provenance",
            "-c",
            "user.email=provenance@example.test",
            "commit",
            "-m",
            message,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if committed.returncode != 0:
        raise AssertionError(f"synthetic commit failed: {committed.stderr}")
    resolved = _run_fixture_git(
        ["-C", str(repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if resolved.returncode != 0:
        raise AssertionError(resolved.stderr)
    return resolved.stdout.strip().lower()


def _build_synthetic_two_commit_history(root: Path) -> tuple[Path, str, str]:
    """Isolated deterministic git history with distinct old/new commit objects."""
    repo = root / "synthetic-history"
    repo.mkdir()
    init = _run_fixture_git(
        ["-C", str(repo), "init"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if init.returncode != 0:
        raise AssertionError(init.stderr)
    (repo / "old.txt").write_text("old-revision\n", encoding="utf-8")
    added = _run_fixture_git(
        ["-C", str(repo), "add", "old.txt"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if added.returncode != 0:
        raise AssertionError(added.stderr)
    old_commit = _git_commit(repo, "synthetic-old")
    (repo / "new.txt").write_text("new-revision\n", encoding="utf-8")
    added_new = _run_fixture_git(
        ["-C", str(repo), "add", "new.txt"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if added_new.returncode != 0:
        raise AssertionError(added_new.stderr)
    new_commit = _git_commit(repo, "synthetic-new")
    if old_commit == new_commit:
        raise AssertionError("synthetic history must yield distinct commits")
    if len(old_commit) != 40 or len(new_commit) != 40:
        raise AssertionError("synthetic commits must be full hashes")
    return repo, old_commit, new_commit


def _build_independent_generated_app_history(root: Path) -> Path:
    """Temp repo whose history never advertises PINNED_TEMPLATE_REVISION.

    Models a GitHub ``Use this template`` application checkout with its own
    Initial commit. Portable on both the upstream template repository (where
    REPO_ROOT already contains the pin) and independent generated-app histories.
    Does not rewrite or weaken the exact pin; reviewed-source fixtures still
    materialize the pin from pin_object_repo (typically REPO_ROOT).
    """
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "generated-history"
    repo.mkdir()
    init = _run_fixture_git(
        ["-C", str(repo), "init"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if init.returncode != 0:
        raise AssertionError(init.stderr)
    (repo / "README.md").write_text(
        "independent-generated-app-initial\n", encoding="utf-8"
    )
    added = _run_fixture_git(
        ["-C", str(repo), "add", "README.md"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if added.returncode != 0:
        raise AssertionError(added.stderr)
    tip = _git_commit(repo, "Initial commit")
    if len(tip) != 40:
        raise AssertionError("generated fixture tip must be a full hash")
    if tip == PINNED_TEMPLATE_REVISION:
        raise AssertionError("generated fixture must not collide with the pin")
    try:
        assert_git_commit_object(repo, PINNED_TEMPLATE_REVISION)
    except InitializerError as exc:
        if exc.code != ERR_TEMPLATE_SNAPSHOT:
            raise
    else:
        raise AssertionError(
            "independent generated fixture must not advertise the pin"
        )
    return repo


def _clone_independent_generated_app(root: Path, name: str) -> Path:
    """Clone an independent generated-app history into root/name."""
    history = _build_independent_generated_app_history(root / f"{name}-src")
    app = root / name
    clone = _run_fixture_git(
        ["clone", f"file://{history.resolve()}", str(app)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if clone.returncode != 0:
        raise AssertionError(f"generated-app clone failed: {clone.stderr}")
    try:
        assert_git_commit_object(app, PINNED_TEMPLATE_REVISION)
    except InitializerError as exc:
        if exc.code != ERR_TEMPLATE_SNAPSHOT:
            raise
    else:
        raise AssertionError("cloned generated app must not advertise the pin")
    return app


def _materialize_local_reviewed_pin_source(root: Path, pin_object_repo: Path) -> Path:
    """Bare repo advertising PINNED_TEMPLATE_REVISION (local path fetch only)."""
    assert_git_commit_object(pin_object_repo, PINNED_TEMPLATE_REVISION)
    bare = root / "reviewed-template.git"
    init = _run_fixture_git(
        ["init", "--bare", str(bare)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if init.returncode != 0:
        raise AssertionError(init.stderr)
    fetched = _run_fixture_git(
        [
            "-C",
            str(bare),
            "fetch",
            "--depth",
            "1",
            str(pin_object_repo.resolve()),
            f"{PINNED_TEMPLATE_REVISION}:refs/heads/pinned-template",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if fetched.returncode != 0:
        raise AssertionError(fetched.stderr)
    tip = _run_fixture_git(
        ["-C", str(bare), "rev-parse", "refs/heads/pinned-template"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if tip.returncode != 0 or tip.stdout.strip().lower() != PINNED_TEMPLATE_REVISION:
        raise AssertionError("reviewed bare must advertise the exact pin")
    return bare


class SnapshotPinTests(unittest.TestCase):
    def test_git_commit_object_required_for_pin(self):
        rev = assert_git_commit_object(REPO_ROOT, PINNED_TEMPLATE_REVISION)
        self.assertEqual(rev, PINNED_TEMPLATE_REVISION)

    def test_unknown_revision_fails_closed(self):
        fake = "0123456789abcdef0123456789abcdef01234567"
        with self.assertRaises(InitializerError) as ctx:
            assert_git_commit_object(REPO_ROOT, fake)
        self.assertEqual(ctx.exception.code, ERR_TEMPLATE_SNAPSHOT)

    def test_marker_alone_insufficient_without_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "not-a-repo"
            bare.mkdir()
            (bare / "TEMPLATE_REVISION").write_text(
                PINNED_TEMPLATE_REVISION + "\n", encoding="utf-8"
            )
            (bare / "app").mkdir()
            (bare / "app" / "README.md").write_text("x\n", encoding="utf-8")
            with self.assertRaises(InitializerError) as ctx:
                verify_template_snapshot(
                    bare,
                    PINNED_TEMPLATE_REVISION,
                    git_repo=bare,
                )
            self.assertEqual(ctx.exception.code, ERR_TEMPLATE_SNAPSHOT)

    def test_fixture_snapshot_verifies_against_controlling_repo(self):
        digest = verify_template_snapshot(
            TEMPLATE_ROOT,
            PINNED_TEMPLATE_REVISION,
            git_repo=REPO_ROOT,
        )
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(digest), len("sha256:") + 64)

    def test_shallow_clone_misses_old_commit_until_full_history_available(self):
        """Portable Git shallow-history regression (synthetic commits only).

        Does not use PINNED_TEMPLATE_REVISION or network. Proves shallow clones
        omit older commit objects and that a bounded exact-hash fetch restores
        them without weakening assert_git_commit_object.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history, old_commit, new_commit = _build_synthetic_two_commit_history(root)
            self.assertNotEqual(old_commit, new_commit)
            shallow = root / "shallow"
            full = root / "full"
            clone_shallow = _run_fixture_git(
                [
                    "clone",
                    "--depth",
                    "1",
                    f"file://{history.resolve()}",
                    str(shallow),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(
                clone_shallow.returncode,
                0,
                msg="shallow clone setup failed",
            )
            tip = assert_git_commit_object(shallow, new_commit)
            self.assertEqual(tip, new_commit)
            with self.assertRaises(InitializerError) as ctx:
                assert_git_commit_object(shallow, old_commit)
            self.assertEqual(ctx.exception.code, ERR_TEMPLATE_SNAPSHOT)

            clone_full = _run_fixture_git(
                ["clone", f"file://{history.resolve()}", str(full)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(
                clone_full.returncode,
                0,
                msg="full-history clone setup failed",
            )
            self.assertEqual(assert_git_commit_object(full, old_commit), old_commit)
            self.assertEqual(assert_git_commit_object(full, new_commit), new_commit)

            fetch_old = _run_fixture_git(
                [
                    "-C",
                    str(shallow),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    old_commit,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(
                fetch_old.returncode,
                0,
                msg="fetch of old synthetic revision into shallow clone failed",
            )
            self.assertEqual(assert_git_commit_object(shallow, old_commit), old_commit)

    def test_reviewed_remote_identity_allowlist(self):
        self.assertTrue(
            is_reviewed_template_remote(DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL)
        )
        self.assertTrue(
            is_reviewed_template_remote(
                "git@github.com:agrana/free_personal_stack.git"
            )
        )
        self.assertEqual(
            approve_reviewed_template_source(
                "https://github.com/agrana/free_personal_stack"
            ),
            DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
        )
        self.assertEqual(
            approve_reviewed_template_source(
                "git@github.com:agrana/free_personal_stack.git"
            ),
            DEFAULT_REVIEWED_TEMPLATE_SOURCE_SSH,
        )
        self.assertEqual(
            normalize_git_remote_identity(
                "git@github.com:agrana/free_personal_stack.git"
            ),
            "github.com/agrana/free_personal_stack",
        )
        self.assertFalse(
            is_reviewed_template_remote("https://github.com/example/not-the-template.git")
        )
        self.assertFalse(is_reviewed_template_remote("file:///tmp/arbitrary.git"))

    def test_reviewed_source_rejects_unsafe_transport_syntax(self):
        """Allowlist requires complete supported HTTPS/SSH syntax only."""
        rejected = (
            "ftp://github.com/agrana/free_personal_stack.git",
            "http://github.com/agrana/free_personal_stack.git",
            "git://github.com/agrana/free_personal_stack.git",
            "ssh://git@github.com/agrana/free_personal_stack.git",
            "https://user:pass@github.com/agrana/free_personal_stack.git",
            "https://user@github.com/agrana/free_personal_stack.git",
            "https://github.com:8443/agrana/free_personal_stack.git",
            "https://github.com:443/agrana/free_personal_stack.git",
            "https://github.com/agrana/free_personal_stack.git?foo=1",
            "https://github.com/agrana/free_personal_stack.git#frag",
            "https://github.com/agrana/free_personal_stack.git ",
            " https://github.com/agrana/free_personal_stack.git",
            "https://github.com/agrana/free_personal_stack.git\n",
            "ext::sh -c evil%github.com/agrana/free_personal_stack",
            "git@github.com:agrana/free_personal_stack.git/../../../etc/passwd",
            "git@github.com:other/free_personal_stack.git",
            "https://github.com/agrana/free_personal_stack.git/extra",
            "file:///tmp/agrana/free_personal_stack.git",
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(is_reviewed_template_remote(source))
                with self.assertRaises(InitializerError) as ctx:
                    approve_reviewed_template_source(source)
                self.assertEqual(
                    ctx.exception.code, ERR_TEMPLATE_SOURCE_UNTRUSTED
                )

    def test_ensure_pin_rejects_option_like_revision_without_fetch(self):
        """Option-like revisions must fail before any git fetch spawn."""
        fetch_calls: list[list[str]] = []
        real_run = subprocess.run

        def guarded_run(cmd, **kwargs):
            if isinstance(cmd, (list, tuple)) and "fetch" in cmd:
                fetch_calls.append(list(cmd))
            return real_run(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            clone = _run_fixture_git(
                ["clone", f"file://{REPO_ROOT.resolve()}", str(app)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(clone.returncode, 0, msg=clone.stderr)
            with (
                unittest.mock.patch(
                    "bootstrap.provenance.subprocess.run", side_effect=guarded_run
                ),
                unittest.mock.patch(
                    "bootstrap.snapshot.subprocess.run", side_effect=guarded_run
                ),
            ):
                with self.assertRaises(InitializerError) as ctx:
                    ensure_pinned_commit_object(
                        app,
                        "--upload-pack=evil",
                        reviewed_source_url=DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                    )
            self.assertEqual(ctx.exception.code, ERR_TEMPLATE_REV)
            self.assertEqual(fetch_calls, [])

    def test_ensure_pin_rejects_option_like_remote_without_fetch(self):
        """Option-like remote names must fail before any git fetch spawn.

        Validation runs even when the pin object is already present.
        """
        fetch_calls: list[list[str]] = []
        real_run = subprocess.run

        def guarded_run(cmd, **kwargs):
            if isinstance(cmd, (list, tuple)) and "fetch" in cmd:
                fetch_calls.append(list(cmd))
            return real_run(cmd, **kwargs)

        with (
            unittest.mock.patch(
                "bootstrap.provenance.subprocess.run", side_effect=guarded_run
            ),
            unittest.mock.patch(
                "bootstrap.snapshot.subprocess.run", side_effect=guarded_run
            ),
        ):
            with self.assertRaises(InitializerError) as present:
                ensure_pinned_commit_object(
                    REPO_ROOT,
                    PINNED_TEMPLATE_REVISION,
                    remote_name="--upload-pack=evil",
                )
            self.assertEqual(
                present.exception.code, ERR_TEMPLATE_SOURCE_UNTRUSTED
            )

            with tempfile.TemporaryDirectory() as tmp:
                app = _clone_independent_generated_app(Path(tmp), "app")
                with self.assertRaises(InitializerError) as missing:
                    ensure_pinned_commit_object(
                        app,
                        PINNED_TEMPLATE_REVISION,
                        remote_name="--upload-pack=evil",
                    )
                self.assertEqual(
                    missing.exception.code, ERR_TEMPLATE_SOURCE_UNTRUSTED
                )
        self.assertEqual(fetch_calls, [])

    def test_ensure_pin_fetch_uses_canonical_approved_source(self):
        """Fetch must receive the canonical approved URL, not raw variants."""
        fetch_cmds: list[list[str]] = []
        real_run = subprocess.run

        def capture_run(cmd, **kwargs):
            if isinstance(cmd, (list, tuple)) and "fetch" in cmd:
                fetch_cmds.append(list(cmd))
            return real_run(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _clone_independent_generated_app(root, "generated-app")
            reviewed = _materialize_local_reviewed_pin_source(root, REPO_ROOT)
            _run_fixture_git(
                [
                    "-C",
                    str(app),
                    "config",
                    f"url.file://{reviewed.resolve()}.insteadOf",
                    DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            with unittest.mock.patch(
                "bootstrap.provenance.subprocess.run", side_effect=capture_run
            ):
                rev = ensure_pinned_commit_object(
                    app,
                    PINNED_TEMPLATE_REVISION,
                    reviewed_source_url=(
                        "https://github.com/agrana/free_personal_stack"
                    ),
                )
            self.assertEqual(rev, PINNED_TEMPLATE_REVISION)
            self.assertEqual(len(fetch_cmds), 1)
            self.assertIn(DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL, fetch_cmds[0])
            self.assertNotIn(
                "https://github.com/agrana/free_personal_stack",
                fetch_cmds[0],
            )
            self.assertIn("--", fetch_cmds[0])
            self.assertIn("protocol.allow=never", fetch_cmds[0])

    def test_generated_repo_clone_lacks_pin_until_reviewed_source(self):
        """Independent generated-app history does not advertise the upstream pin.

        Uses a temporary fixture repo (not REPO_ROOT) so the regression holds on
        both the upstream template history and generated-app checkouts.
        Offline: refuse untrusted/wrong sources, then materialize the exact pin
        from an allowlisted identity redirected to a local reviewed bare.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _clone_independent_generated_app(root, "generated-app")
            with self.assertRaises(InitializerError) as missing:
                assert_git_commit_object(app, PINNED_TEMPLATE_REVISION)
            self.assertEqual(missing.exception.code, ERR_TEMPLATE_SNAPSHOT)

            with self.assertRaises(InitializerError) as untrusted:
                ensure_pinned_commit_object(
                    app,
                    PINNED_TEMPLATE_REVISION,
                    reviewed_source_url="https://github.com/example/untrusted.git",
                )
            self.assertEqual(
                untrusted.exception.code, ERR_TEMPLATE_SOURCE_UNTRUSTED
            )

            wrong = root / "wrong-source.git"
            init_wrong = _run_fixture_git(
                ["init", "--bare", str(wrong)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(init_wrong.returncode, 0, msg=init_wrong.stderr)
            donor = root / "wrong-work"
            donor.mkdir()
            _run_fixture_git(
                ["-C", str(donor), "init"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            (donor / "only.txt").write_text("not-the-pin\n", encoding="utf-8")
            _run_fixture_git(
                ["-C", str(donor), "add", "only.txt"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            _git_commit(donor, "unrelated")
            _run_fixture_git(
                [
                    "-C",
                    str(wrong),
                    "fetch",
                    str(donor),
                    "+refs/heads/*:refs/heads/*",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            _run_fixture_git(
                [
                    "-C",
                    str(app),
                    "config",
                    f"url.file://{wrong.resolve()}.insteadOf",
                    DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            with self.assertRaises(InitializerError) as wrong_src:
                ensure_pinned_commit_object(
                    app,
                    PINNED_TEMPLATE_REVISION,
                    reviewed_source_url=DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                )
            self.assertEqual(
                wrong_src.exception.code, ERR_TEMPLATE_SOURCE_UNAVAILABLE
            )
            with self.assertRaises(InitializerError) as still_missing:
                assert_git_commit_object(app, PINNED_TEMPLATE_REVISION)
            self.assertEqual(still_missing.exception.code, ERR_TEMPLATE_SNAPSHOT)

            reviewed = _materialize_local_reviewed_pin_source(root, REPO_ROOT)
            _run_fixture_git(
                [
                    "-C",
                    str(app),
                    "config",
                    "--unset-all",
                    f"url.file://{wrong.resolve()}.insteadOf",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            _run_fixture_git(
                [
                    "-C",
                    str(app),
                    "config",
                    f"url.file://{reviewed.resolve()}.insteadOf",
                    DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            rev = ensure_pinned_commit_object(
                app,
                PINNED_TEMPLATE_REVISION,
                reviewed_source_url=DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
            )
            self.assertEqual(rev, PINNED_TEMPLATE_REVISION)
            self.assertEqual(
                assert_git_commit_object(app, PINNED_TEMPLATE_REVISION),
                PINNED_TEMPLATE_REVISION,
            )

            app2 = _clone_independent_generated_app(root, "generated-app-2")
            _run_fixture_git(
                [
                    "-C",
                    str(app2),
                    "remote",
                    "add",
                    "template",
                    "https://github.com/example/untrusted.git",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            with self.assertRaises(InitializerError) as bad_remote:
                ensure_pinned_commit_object(
                    app2,
                    PINNED_TEMPLATE_REVISION,
                    remote_name="template",
                )
            self.assertEqual(
                bad_remote.exception.code, ERR_TEMPLATE_SOURCE_UNTRUSTED
            )

    def test_ensure_pin_noops_when_already_present(self):
        rev = ensure_pinned_commit_object(
            REPO_ROOT,
            PINNED_TEMPLATE_REVISION,
            reviewed_source_url="https://github.com/example/would-be-rejected.git",
        )
        self.assertEqual(rev, PINNED_TEMPLATE_REVISION)

    def test_ensure_pin_unavailable_reviewed_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _clone_independent_generated_app(root, "empty-app")
            empty = root / "empty.git"
            _run_fixture_git(
                ["init", "--bare", str(empty)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            _run_fixture_git(
                [
                    "-C",
                    str(app),
                    "config",
                    f"url.file://{empty.resolve()}.insteadOf",
                    DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            with self.assertRaises(InitializerError) as ctx:
                ensure_pinned_commit_object(
                    app,
                    PINNED_TEMPLATE_REVISION,
                    reviewed_source_url=DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL,
                )
            self.assertEqual(ctx.exception.code, ERR_TEMPLATE_SOURCE_UNAVAILABLE)

    def test_fixture_git_suppresses_detached_maintenance(self):
        """Temp fixture git must not enable auto/detached maintenance or gc.

        Guards against ENOTEMPTY races on TemporaryDirectory cleanup of
        .git/objects after clone/fetch. Does not swallow cleanup errors.
        """
        env = _fixture_git_env()
        self.assertEqual(env.get("GIT_CONFIG_COUNT"), "4")
        for index, (config_key, config_value) in enumerate(_FIXTURE_GIT_CONFIG_PAIRS):
            self.assertEqual(env.get(f"GIT_CONFIG_KEY_{index}"), config_key)
            self.assertEqual(env.get(f"GIT_CONFIG_VALUE_{index}"), config_value)

        # CI-like outer overrides that would otherwise re-enable auto maintenance.
        hostile = dict(os.environ)
        hostile["GIT_CONFIG_COUNT"] = "2"
        hostile["GIT_CONFIG_KEY_0"] = "maintenance.auto"
        hostile["GIT_CONFIG_VALUE_0"] = "true"
        hostile["GIT_CONFIG_KEY_1"] = "gc.auto"
        hostile["GIT_CONFIG_VALUE_1"] = "1"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clone = root / "clone"
            src = REPO_ROOT.resolve()
            cloned = _run_fixture_git(
                ["clone", "--depth", "1", f"file://{src}", str(clone)],
                env=hostile,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(cloned.returncode, 0, msg=cloned.stderr)

            # Local repo config may still default to auto; command-scoped
            # overrides must win for every fixture git invocation.
            for config_key, expected in (
                ("maintenance.auto", "false"),
                ("maintenance.autoDetach", "false"),
                ("gc.auto", "0"),
                ("gc.autoDetach", "false"),
            ):
                probed = _run_fixture_git(
                    ["-C", str(clone), "config", "--get", config_key],
                    env=hostile,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(probed.returncode, 0, msg=probed.stderr)
                self.assertEqual(probed.stdout.strip(), expected)

            fetched = _run_fixture_git(
                ["-C", str(clone), "fetch", "--depth", "1", "origin", "HEAD"],
                env=hostile,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(fetched.returncode, 0, msg=fetched.stderr)
            objects_dir = clone / ".git" / "objects"
            self.assertTrue(objects_dir.is_dir())
            # No maintenance lock left that would imply a detached worker.
            lock_paths = list(objects_dir.glob("**/*.lock"))
            self.assertEqual(lock_paths, [])


class OfflinePlanTests(unittest.TestCase):
    def test_two_distinct_configs_generate_offline(self):
        arena = _load(CONFIG_ARENA)
        board = _load(CONFIG_BOARD)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r1 = plan_app(
                config=arena,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out-arena",
                private_state_root=root / "state",
            )
            r2 = plan_app(
                config=board,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out-board",
                private_state_root=root / "state",
            )
            self.assertTrue(r1.ok)
            self.assertTrue(r2.ok)
            self.assertEqual(r1.code, "plan.ok")
            self.assertEqual(r2.code, "plan.ok")
            self.assertNotEqual(r1.plan_digest, r2.plan_digest)
            self.assertTrue((root / "out-arena" / "app.config.json").is_file())
            self.assertTrue((root / "out-board" / "app.config.json").is_file())
            arena_cfg = json.loads(
                (root / "out-arena" / "app.config.json").read_text(encoding="utf-8")
            )
            board_cfg = json.loads(
                (root / "out-board" / "app.config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(arena_cfg["app"]["slug"], "agent-arena")
            self.assertEqual(board_cfg["app"]["slug"], "decisions-board")
            self.assertNotEqual(arena_cfg["hostname"], board_cfg["hostname"])
            self.assertNotEqual(
                arena_cfg["database"]["namespace"], board_cfg["database"]["namespace"]
            )

    def test_identical_rerun_is_idempotent(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            second = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertEqual(first.plan_digest, second.plan_digest)
            # After plan, status is planned (not completed) so re-plan rewrites.
            self.assertIn(second.code, {"plan.ok", "plan.noop"})

    def test_secret_paths_excluded_from_scaffold(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Hostile extras under a copied template tree.
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            (staged / "state").mkdir()
            (staged / "state" / "secret.db").write_text("nope\n", encoding="utf-8")
            (staged / "temp").mkdir()
            (staged / "temp" / "cache.bin").write_bytes(b"\x00\x01")
            plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            out = root / "out"
            self.assertFalse((out / ".env").exists())
            self.assertFalse((out / "platforms.json").exists())
            self.assertFalse((out / "supabase" / ".temp").exists())
            self.assertFalse((out / "state" / "secret.db").exists())
            self.assertFalse((out / "temp" / "cache.bin").exists())
            self.assertTrue((out / "app" / "README.md").is_file())
            self.assertTrue(
                (out / ".github" / "workflows.pending" / "fixture.yml").is_file()
            )
            self.assertFalse((out / ".github" / "workflows").exists())
            # Execute bit stripped from copied hook.
            hook = out / "evil-hook.sh"
            self.assertTrue(hook.is_file())
            self.assertFalse(os.access(hook, os.X_OK))

    def test_hostile_symlink_rejected(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            target = root / "outside.txt"
            target.write_text("escape\n", encoding="utf-8")
            link = staged / "linked.txt"
            link.symlink_to(target)
            # Symlink files are skipped by walker; ensure root symlink fails.
            bad_root = root / "sym-root"
            bad_root.symlink_to(staged)
            with self.assertRaises(InitializerError) as ctx:
                materialize_scaffold(
                    template_root=bad_root,
                    output_root=root / "out",
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    git_repo=REPO_ROOT,
                )
            self.assertIn(ctx.exception.code, {ERR_SYMLINK, ERR_TEMPLATE_REV, "template.root_invalid"})

    def test_path_escape_rejected(self):
        from bootstrap.paths import safe_join

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)
            with self.assertRaises(InitializerError) as ctx:
                safe_join(root, "../outside")
            self.assertEqual(ctx.exception.code, ERR_PATH_ESCAPE)

    def test_user_file_overwrite_refused(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            (out / "app").mkdir()
            (out / "app" / "README.md").write_text("user-owned\n", encoding="utf-8")
            with self.assertRaises(InitializerError) as ctx:
                materialize_scaffold(
                    template_root=TEMPLATE_ROOT,
                    output_root=out,
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    owned_relative=set(),
                    git_repo=REPO_ROOT,
                )
            self.assertEqual(ctx.exception.code, ERR_OVERWRITE)

    def test_wrong_template_revision_rejected(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    template_root=TEMPLATE_ROOT,
                    output_root=root / "out",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_TEMPLATE_REV)


class DriftAndTamperTests(unittest.TestCase):
    def test_config_drift_blocks_apply(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            drifted = json.loads(json.dumps(config))
            drifted["hostname"] = "drifted.example.com"
            result = apply_app(
                config=drifted,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_DRIFT_CONFIG)

    def test_tampered_plan_digest_blocks_apply(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest="sha256:" + ("ab" * 32),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_PLAN_DIGEST)
            self.assertIsNotNone(planned.plan_digest)

    def test_tampered_plan_artifact_blocks_apply(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            plan_path = root / "state" / "agent-arena" / "reviewed.plan.json"
            doc = json.loads(plan_path.read_text(encoding="utf-8"))
            doc["naming"]["hostname"] = "tampered.example.com"
            plan_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(result.ok)
            self.assertIn(result.code, {ERR_DRIFT_PLAN, ERR_PLAN_DIGEST})


class ApplyGateTests(unittest.TestCase):
    def test_default_adapter_blocks_live_apply(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_LIVE_MISSING)
            manifest = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotEqual(manifest["status"], "completed")
            self.assertFalse(manifest["gates"]["liveApplyIntegrated"])

    def test_unresolved_dns_blocks_apply(self):
        config = _load(CONFIG_ARENA_UNRESOLVED)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "dns.unresolved_blocks_apply")

    def test_dry_run_does_not_mark_completed(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertTrue(dry.ok)
            self.assertEqual(dry.code, "apply.dry_run_ok")
            self.assertEqual(dry.status, "planned")
            self.assertFalse((dry.details or {}).get("durableMutation", True))
            # Real apply still blocked (not short-circuited as noop completed).
            blocked = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
            )
            self.assertEqual(blocked.code, ERR_LIVE_MISSING)

    def test_claiming_live_adapter_rejected(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=ClaimingLiveAdapter(),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_LIVE_CLAIM)


class FixtureAdapterTests(unittest.TestCase):
    """Injectable RecordingApplyAdapter only — not evidence of live providers."""

    def test_fixture_apply_and_idempotent_rerun(self):
        config = _load(CONFIG_ARENA)
        adapter = RecordingApplyAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            first = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertTrue(first.ok)
            self.assertEqual(first.code, "apply.ok")
            self.assertEqual(first.status, "completed")
            self.assertEqual(
                (first.details or {}).get("operationKind"), "provider_operation"
            )
            project_id = (first.details or {}).get("resourceIds", {}).get(
                "vercelProjectId"
            )
            self.assertEqual(project_id, "prj_fixture_new_001")
            second = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertTrue(second.ok)
            self.assertEqual(second.code, "apply.noop")

    def test_interrupted_resume_without_duplicate(self):
        config = _load(CONFIG_ARENA)
        adapter = RecordingApplyAdapter(interrupt_before="ensure_dns_records")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            interrupted = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertFalse(interrupted.ok)
            self.assertEqual(interrupted.code, "stage.interrupted")
            self.assertEqual(interrupted.status, "interrupted")
            manifest = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )
            self.assertEqual(manifest["resumeState"]["nextStage"], "ensure_dns_records")
            # Resume with a clean adapter that already knows the created project.
            resume_adapter = RecordingApplyAdapter(
                existing_projects_by_id={
                    "prj_fixture_new_001": "agent-arena",
                },
                existing_projects_by_name={"agent-arena": "prj_fixture_new_001"},
                existing_dns={},
            )
            resumed = resume_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=resume_adapter,
            )
            self.assertTrue(resumed.ok)
            self.assertEqual(resumed.status, "completed")
            final = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                final["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )
            # Ensure path reused known id (no second create id).
            self.assertNotIn("ensure_vercel_project", resume_adapter.calls)

    def test_duplicate_name_without_ownership_refused(self):
        config = _load(CONFIG_ARENA)
        adapter = RecordingApplyAdapter(
            existing_projects_by_name={"agent-arena": "prj_stranger_999"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_ADOPT_BY_NAME)
            self.assertEqual(adapter.delete_attempts, 1)

    def test_lock_contention_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "bootstrap.lock"
            holder = AppLock(lock_path)
            holder.acquire(blocking=False)
            try:
                with self.assertRaises(InitializerError) as ctx:
                    with exclusive_app_lock(lock_path, blocking=False):
                        pass
                self.assertEqual(ctx.exception.code, ERR_CONCURRENT)
            finally:
                holder.release()

    def test_lock_contention_during_plan(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state" / "agent-arena"
            state_dir.mkdir(parents=True)
            lock_path = state_dir / "bootstrap.lock"
            holder = AppLock(lock_path)
            holder.acquire(blocking=False)
            err_box: list[BaseException] = []

            def _runner() -> None:
                try:
                    plan_app(
                        config=config,
                        template_revision=PINNED_TEMPLATE_REVISION,
                        template_root=TEMPLATE_ROOT,
                        output_root=root / "out",
                        private_state_root=root / "state",
                    )
                except BaseException as exc:  # noqa: BLE001 — capture for parent
                    err_box.append(exc)

            try:
                thread = threading.Thread(target=_runner)
                thread.start()
                thread.join(timeout=5)
                self.assertTrue(err_box)
                self.assertIsInstance(err_box[0], InitializerError)
                self.assertEqual(err_box[0].code, ERR_CONCURRENT)
            finally:
                holder.release()


class NoLiveOperationsTests(unittest.TestCase):
    def test_plan_artifact_has_no_secret_material_or_raw_tf(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(planned.ok)
            plan_path = root / "state" / "agent-arena" / "reviewed.plan.json"
            text = plan_path.read_text(encoding="utf-8")
            self.assertNotIn("resource_changes", text)
            self.assertNotIn("secret_should_not_copy", text)
            self.assertNotIn("Bearer ", text)
            doc = json.loads(text)
            self.assertEqual(doc["operationKind"], "planning_simulation")
            self.assertFalse(doc["gates"]["liveApplyIntegrated"])
            self.assertIn("CLOUDFLARE_API_TOKEN", doc["secretAliasNames"])
            # Alias names only — values never present.
            self.assertNotIn("secretAliases", doc)

    def test_fixture_env_canary_not_copied(self):
        # .env is gitignored; ensure a local canary exists for exclusion coverage.
        env_path = TEMPLATE_ROOT / ".env"
        if not env_path.is_file():
            env_path.write_text("secret_should_not_copy=true\n", encoding="utf-8")
        canary = env_path.read_text(encoding="utf-8").strip()
        self.assertTrue(canary)
        # Ensure walker never yields excluded secrets.
        rels = {
            p.relative_to(TEMPLATE_ROOT).as_posix()
            for p in iter_template_files(TEMPLATE_ROOT)
        }
        self.assertNotIn(".env", rels)
        self.assertNotIn("platforms.json", rels)
        self.assertNotIn("supabase/.temp/project-ref", rels)


class SafetyRegressionTests(unittest.TestCase):
    """PR #11 required corrections — fail closed before any live adapter work."""

    def test_content_change_with_unchanged_revision_invalidates_plan_digest(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out-a",
                private_state_root=root / "state-a",
            )
            self.assertTrue(first.ok)
            readme = staged / "app" / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "operator-edit\n",
                encoding="utf-8",
            )
            # Declared revision marker unchanged — content must still invalidate review.
            marker = (staged / "TEMPLATE_REVISION").read_text(encoding="utf-8").strip()
            self.assertEqual(marker, PINNED_TEMPLATE_REVISION)
            second = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out-b",
                private_state_root=root / "state-b",
            )
            self.assertTrue(second.ok)
            self.assertNotEqual(first.plan_digest, second.plan_digest)
            plan_a = json.loads(
                (root / "state-a" / "agent-arena" / "reviewed.plan.json").read_text(
                    encoding="utf-8"
                )
            )
            plan_b = json.loads(
                (root / "state-b" / "agent-arena" / "reviewed.plan.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("contentSnapshotDigest", plan_a)
            self.assertIn("scaffoldDigest", plan_a)
            self.assertNotEqual(
                plan_a["contentSnapshotDigest"], plan_b["contentSnapshotDigest"]
            )
            self.assertNotEqual(plan_a["scaffoldDigest"], plan_b["scaffoldDigest"])

    def test_dirty_tracked_source_at_pin_fails_closed(self):
        """When selected paths exist in the pinned commit, dirty bytes are rejected."""
        # Use a tiny selected tree that exists at the pin when available; otherwise
        # skip with explicit offline-bound coverage above. Prefer a real tracked file.
        pin_paths = []
        import subprocess

        listed = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "ls-tree",
                "-r",
                "--name-only",
                PINNED_TEMPLATE_REVISION,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if listed.returncode == 0:
            pin_paths = [line for line in listed.stdout.splitlines() if line.endswith(".md")]
        if not pin_paths:
            self.skipTest("pinned revision has no markdown paths to dirty-check")
        selected = pin_paths[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            staged.mkdir()
            (staged / "TEMPLATE_REVISION").write_text(
                PINNED_TEMPLATE_REVISION + "\n", encoding="utf-8"
            )
            rel = Path(selected)
            # Reconstruct only this selected path under staged root with dirty bytes.
            # Map: treat staged as a synthetic root claiming pin provenance for `selected`.
            # Use verify API with selected_source relative to repo.
            from bootstrap.snapshot import verify_template_snapshot

            # Copy a real repo file into a fake template layout keyed by basename only is
            # insufficient; instead mutate a checkout of the selected blob under repo-relative path.
            target = root / "work" / selected
            target.parent.mkdir(parents=True, exist_ok=True)
            blob = subprocess.run(
                [
                    "git",
                    "-C",
                    str(REPO_ROOT),
                    "show",
                    f"{PINNED_TEMPLATE_REVISION}:{selected}",
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            target.write_bytes(blob.stdout + b"\ndirty\n")
            (root / "work" / "TEMPLATE_REVISION").write_text(
                PINNED_TEMPLATE_REVISION + "\n", encoding="utf-8"
            )
            with self.assertRaises(InitializerError) as ctx:
                verify_template_snapshot(
                    root / "work",
                    PINNED_TEMPLATE_REVISION,
                    git_repo=REPO_ROOT,
                    require_marker=True,
                    require_git_content_match=True,
                )
            self.assertEqual(ctx.exception.code, ERR_TEMPLATE_DIRTY)

    def test_symlink_lock_does_not_truncate_external_canary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state" / "agent-arena"
            state.mkdir(parents=True)
            canary = root / "outside-canary.txt"
            canary.write_text("preserve-me\n", encoding="utf-8")
            lock_path = state / "bootstrap.lock"
            lock_path.symlink_to(canary)
            with self.assertRaises(InitializerError) as ctx:
                AppLock(lock_path).acquire(blocking=False)
            self.assertEqual(ctx.exception.code, ERR_SYMLINK)
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve-me\n")

    def test_symlink_state_manifest_tmp_does_not_clobber_external_canary(self):
        from bootstrap.manifest import atomic_write_text

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state" / "agent-arena"
            state.mkdir(parents=True)
            canary = root / "outside-canary.txt"
            canary.write_text("preserve-me\n", encoding="utf-8")
            # Hostile: make parent of target a symlink escape via temp name collision pattern
            # by pointing the destination path itself at the canary.
            dest = state / "bootstrap.manifest.json"
            dest.symlink_to(canary)
            with self.assertRaises(InitializerError) as ctx:
                atomic_write_text(dest, '{"schemaVersion":1}\n')
            self.assertIn(ctx.exception.code, {ERR_SYMLINK, ERR_PATH_ESCAPE})
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve-me\n")

    def test_replan_preserves_interrupted_resource_checkpoint(self):
        config = _load(CONFIG_ARENA)
        adapter = RecordingApplyAdapter(interrupt_before="ensure_dns_records")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            interrupted = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertEqual(interrupted.status, "interrupted")
            before = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                before["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )
            replanned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(replanned.ok)
            after = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                after["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )
            self.assertEqual(after["status"], "interrupted")
            # Incompatible re-plan must fail closed instead of wiping ownership.
            drifted = json.loads(json.dumps(config))
            drifted["hostname"] = "other.example.com"
            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=drifted,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=TEMPLATE_ROOT,
                    output_root=root / "out-drift",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_CHECKPOINT_RESET)
            final = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                final["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )

    def test_user_edit_to_generated_file_blocks_silent_overwrite(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            edited = root / "out" / "app" / "README.md"
            original = edited.read_text(encoding="utf-8")
            edited.write_text(original + "user-note\n", encoding="utf-8")
            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=TEMPLATE_ROOT,
                    output_root=root / "out",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_GENERATED_EDIT)
            self.assertEqual(edited.read_text(encoding="utf-8"), original + "user-note\n")
            # Unchanged content remains idempotent.
            edited.write_text(original, encoding="utf-8")
            second = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(second.ok)
            self.assertEqual(second.plan_digest, first.plan_digest)

    def test_missing_or_denied_capabilities_block_mutation(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            empty = RecordingApplyAdapter(capabilities={})
            denied = RecordingApplyAdapter(
                capabilities={
                    "vercel.project.write": False,
                    "dns.record.write": True,
                    "supabase.shared.reference": True,
                }
            )
            for adapter, expected in (
                (empty, ERR_CAPABILITY_UNVERIFIED),
                (denied, ERR_CAPABILITY_UNVERIFIED),
            ):
                result = apply_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    private_state_root=root / "state",
                    plan_digest=str(planned.plan_digest),
                    adapter=adapter,
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.code, expected)
                self.assertNotIn("ensure_vercel_project", adapter.calls)
            manifest = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(manifest["resourceIds"]["vercelProjectId"])

    def test_forged_mismatched_capability_binding_rejected(self):
        config = _load(CONFIG_ARENA)

        class ForgedBindingAdapter(RecordingApplyAdapter):
            def verify_capabilities(self, ctx):  # type: ignore[override]
                self.calls.append("verify_capabilities")
                return CapabilityReport(
                    verified={
                        "vercel.project.write": True,
                        "dns.record.write": True,
                        "supabase.shared.reference": True,
                    },
                    operation_kind="provider_operation",
                    app_slug="other-app",
                    config_hash="sha256:" + ("00" * 32),
                    plan_digest="sha256:" + ("11" * 32),
                    provider="vercel",
                    operation="apply",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            adapter = ForgedBindingAdapter()
            result = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_CAPABILITY_BINDING)
            self.assertNotIn("ensure_vercel_project", adapter.calls)

    def test_completed_resume_validates_bindings(self):
        config = _load(CONFIG_ARENA)
        adapter = RecordingApplyAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            applied = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertEqual(applied.status, "completed")
            ok = resume_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertTrue(ok.ok)
            self.assertEqual(ok.code, "resume.noop")
            drifted = json.loads(json.dumps(config))
            drifted["hostname"] = "changed.example.com"
            bad_config = resume_app(
                config=drifted,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=adapter,
            )
            self.assertFalse(bad_config.ok)
            self.assertEqual(bad_config.code, ERR_DRIFT_CONFIG)
            bad_digest = resume_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest="sha256:" + ("cd" * 32),
                adapter=adapter,
            )
            self.assertFalse(bad_digest.ok)
            self.assertEqual(bad_digest.code, ERR_PLAN_DIGEST)

    def test_ensure_owned_directory_rejects_nested_existing_ancestor_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            existing = outside / "existing"
            existing.mkdir(parents=True)
            canary = existing / "canary.txt"
            canary.write_text("preserve-me\n", encoding="utf-8")
            link = root / "link"
            link.symlink_to(outside)
            target = link / "existing" / "private-state"
            with self.assertRaises(InitializerError) as ctx:
                ensure_owned_directory(target)
            self.assertEqual(ctx.exception.code, ERR_SYMLINK)
            self.assertFalse((existing / "private-state").exists())
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve-me\n")

    def test_state_lock_manifest_reject_symlink_ancestor_no_canary_mutation(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            canary = outside / "canary.txt"
            canary.write_text("preserve-me\n", encoding="utf-8")
            (outside / "existing").mkdir()
            state_link = root / "state"
            state_link.symlink_to(outside)
            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=TEMPLATE_ROOT,
                    output_root=root / "out",
                    private_state_root=state_link / "existing",
                )
            self.assertEqual(ctx.exception.code, ERR_SYMLINK)
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve-me\n")
            self.assertFalse((outside / "existing" / "agent-arena").exists())

    def test_dry_run_and_completed_noop_revalidate_generated_files(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            generated = root / "out" / "app.config.json"
            original = generated.read_bytes()
            generated.write_bytes(b"arbitrary-not-reviewed-bytes")
            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertFalse(dry.ok)
            self.assertEqual(dry.code, ERR_GENERATED_EDIT)

            generated.write_bytes(original)
            applied = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertEqual(applied.status, "completed")

            generated.write_bytes(b"post-complete-tamper")
            noop = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(noop.ok)
            self.assertEqual(noop.code, ERR_GENERATED_EDIT)
            resume = resume_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(resume.ok)
            self.assertEqual(resume.code, ERR_GENERATED_EDIT)

            generated.unlink()
            deleted = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(deleted.ok)
            self.assertEqual(deleted.code, ERR_GENERATED_EDIT)

            # Symlink replacement of a generated leaf must fail closed.
            outside = root / "outside-leaf.txt"
            outside.write_text("leaf-canary\n", encoding="utf-8")
            generated.symlink_to(outside)
            linked = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertFalse(linked.ok)
            self.assertIn(
                linked.code, {ERR_SYMLINK, ERR_GENERATED_EDIT, ERR_PATH_ESCAPE}
            )
            self.assertEqual(outside.read_text(encoding="utf-8"), "leaf-canary\n")

    def test_completed_plan_noop_revalidates_generated_files(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            applied = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertEqual(applied.status, "completed")
            (root / "out" / "app.config.json").write_bytes(b"tampered-after-complete")
            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=TEMPLATE_ROOT,
                    output_root=root / "out",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_GENERATED_EDIT)

    def test_interrupted_replan_changed_source_rechecks_denied_capabilities(self):
        import shutil

        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            interrupted = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(interrupt_before="ensure_dns_records"),
            )
            self.assertEqual(interrupted.status, "interrupted")
            before = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                before["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )

            tpl = root / "tpl"
            shutil.copytree(TEMPLATE_ROOT, tpl, symlinks=False)
            readme = tpl / "app" / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8") + "source-change\n", encoding="utf-8")
            replanned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=tpl,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(replanned.ok)
            self.assertNotEqual(replanned.plan_digest, planned.plan_digest)
            after = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                after["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )
            self.assertNotIn("verify_capabilities", after.get("completedStages") or [])

            denied = RecordingApplyAdapter(
                capabilities={
                    "vercel.project.write": False,
                    "dns.record.write": True,
                    "supabase.shared.reference": True,
                }
            )
            result = resume_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(replanned.plan_digest),
                adapter=denied,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, ERR_CAPABILITY_UNVERIFIED)
            self.assertNotIn("ensure_dns_records", denied.calls)
            self.assertNotIn("bind_infra_state", denied.calls)
            final = json.loads(
                (root / "state" / "agent-arena" / "bootstrap.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                final["resourceIds"]["vercelProjectId"], "prj_fixture_new_001"
            )
            self.assertEqual(final["resourceIds"].get("dnsRecordIds") or [], [])

    def test_malformed_manifest_fail_closed_does_not_reset_resources(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=TEMPLATE_ROOT,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            interrupted = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(planned.plan_digest),
                adapter=RecordingApplyAdapter(interrupt_before="ensure_dns_records"),
            )
            self.assertEqual(interrupted.status, "interrupted")
            manifest_path = root / "state" / "agent-arena" / "bootstrap.manifest.json"
            corrupt = "{not-valid-json"
            manifest_path.write_text(corrupt, encoding="utf-8")
            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=TEMPLATE_ROOT,
                    output_root=root / "out",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_SCHEMA)
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), corrupt)


class Pr12ScaffoldConsistencyTests(unittest.TestCase):
    """PR #12 P2 findings — ownership reconcile + validate-before-write."""

    def test_removed_source_file_reconciles_owned_hashes_for_apply_resume(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            readme_out = root / "out" / "app" / "README.md"
            self.assertTrue(readme_out.is_file())
            (staged / "app" / "README.md").unlink()

            second = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(second.ok)
            self.assertNotEqual(second.plan_digest, first.plan_digest)
            self.assertFalse(readme_out.exists())

            state_dir = root / "state" / "agent-arena"
            owned = json.loads((state_dir / "scaffold.owned.json").read_text(encoding="utf-8"))
            manifest = json.loads(
                (state_dir / "bootstrap.manifest.json").read_text(encoding="utf-8")
            )
            owned_files = owned.get("files") or {}
            self.assertNotIn("app/README.md", owned_files)
            self.assertEqual(manifest["scaffold"]["fileCount"], len(owned_files))
            self.assertEqual(
                manifest["scaffold"]["scaffoldDigest"],
                json.loads((state_dir / "reviewed.plan.json").read_text(encoding="utf-8"))[
                    "scaffoldDigest"
                ],
            )

            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(second.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertTrue(dry.ok)
            self.assertEqual(dry.code, "apply.dry_run_ok")

            applied = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(second.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertTrue(applied.ok)
            self.assertEqual(applied.status, "completed")

            resumed = resume_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(second.plan_digest),
                adapter=RecordingApplyAdapter(),
            )
            self.assertTrue(resumed.ok)
            self.assertEqual(resumed.code, "resume.noop")

    def test_user_modified_removed_source_file_preserves_prior_state(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            state_dir = root / "state" / "agent-arena"
            prior_owned = (state_dir / "scaffold.owned.json").read_text(encoding="utf-8")
            prior_manifest = (state_dir / "bootstrap.manifest.json").read_text(
                encoding="utf-8"
            )
            prior_plan = (state_dir / "reviewed.plan.json").read_text(encoding="utf-8")
            readme_out = root / "out" / "app" / "README.md"
            prior_readme = readme_out.read_bytes()
            readme_out.write_bytes(prior_readme + b"\nuser-kept-note\n")
            (staged / "app" / "README.md").unlink()

            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=staged,
                    output_root=root / "out",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_GENERATED_EDIT)
            self.assertEqual(
                (state_dir / "scaffold.owned.json").read_text(encoding="utf-8"),
                prior_owned,
            )
            self.assertEqual(
                (state_dir / "bootstrap.manifest.json").read_text(encoding="utf-8"),
                prior_manifest,
            )
            self.assertEqual(
                (state_dir / "reviewed.plan.json").read_text(encoding="utf-8"),
                prior_plan,
            )
            self.assertEqual(readme_out.read_bytes(), prior_readme + b"\nuser-kept-note\n")

    def test_conflict_after_earlier_source_change_does_not_mutate_outputs(self):
        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            state_dir = root / "state" / "agent-arena"
            prior_owned = (state_dir / "scaffold.owned.json").read_text(encoding="utf-8")
            prior_manifest = (state_dir / "bootstrap.manifest.json").read_text(
                encoding="utf-8"
            )
            prior_plan = (state_dir / "reviewed.plan.json").read_text(encoding="utf-8")
            readme_out = root / "out" / "app" / "README.md"
            marker_out = root / "out" / ".github" / "WORKFLOWS_DISABLED_UNTIL_REVIEW"
            prior_readme = readme_out.read_bytes()
            prior_marker = marker_out.read_bytes()

            readme_src = staged / "app" / "README.md"
            readme_src.write_text(
                readme_src.read_text(encoding="utf-8") + "source-bump\n",
                encoding="utf-8",
            )
            marker_out.write_bytes(prior_marker + b"operator-edit\n")

            with self.assertRaises(InitializerError) as ctx:
                plan_app(
                    config=config,
                    template_revision=PINNED_TEMPLATE_REVISION,
                    template_root=staged,
                    output_root=root / "out",
                    private_state_root=root / "state",
                )
            self.assertEqual(ctx.exception.code, ERR_GENERATED_EDIT)
            self.assertEqual(readme_out.read_bytes(), prior_readme)
            self.assertEqual(marker_out.read_bytes(), prior_marker + b"operator-edit\n")
            self.assertEqual(
                (state_dir / "scaffold.owned.json").read_text(encoding="utf-8"),
                prior_owned,
            )
            self.assertEqual(
                (state_dir / "bootstrap.manifest.json").read_text(encoding="utf-8"),
                prior_manifest,
            )
            self.assertEqual(
                (state_dir / "reviewed.plan.json").read_text(encoding="utf-8"),
                prior_plan,
            )

            # Prior plan remains recoverable after the operator restores the marker.
            marker_out.write_bytes(prior_marker)
            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(first.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertTrue(dry.ok)
            self.assertEqual(dry.code, "apply.dry_run_ok")

    def test_metadata_write_failure_rolls_back_output_to_prior_owned(self):
        import bootstrap.engine as engine_mod

        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            state_dir = root / "state" / "agent-arena"
            prior_owned = (state_dir / "scaffold.owned.json").read_text(encoding="utf-8")
            prior_manifest = (state_dir / "bootstrap.manifest.json").read_text(
                encoding="utf-8"
            )
            prior_plan = (state_dir / "reviewed.plan.json").read_text(encoding="utf-8")
            readme_out = root / "out" / "app" / "README.md"
            prior_readme = readme_out.read_bytes()

            readme_src = staged / "app" / "README.md"
            readme_src.write_text(
                readme_src.read_text(encoding="utf-8") + "source-bump-meta\n",
                encoding="utf-8",
            )

            real_atomic = engine_mod.atomic_write_text
            plan_target = state_dir / "reviewed.plan.json"

            def _fail_plan_write(path, payload):
                if Path(path) == plan_target:
                    raise OSError("injected-plan-persist-failure")
                return real_atomic(path, payload)

            engine_mod.atomic_write_text = _fail_plan_write  # type: ignore[assignment]
            try:
                with self.assertRaises(Exception):
                    plan_app(
                        config=config,
                        template_revision=PINNED_TEMPLATE_REVISION,
                        template_root=staged,
                        output_root=root / "out",
                        private_state_root=root / "state",
                    )
            finally:
                engine_mod.atomic_write_text = real_atomic  # type: ignore[assignment]

            self.assertEqual(readme_out.read_bytes(), prior_readme)
            self.assertEqual(
                (state_dir / "scaffold.owned.json").read_text(encoding="utf-8"),
                prior_owned,
            )
            self.assertEqual(
                (state_dir / "bootstrap.manifest.json").read_text(encoding="utf-8"),
                prior_manifest,
            )
            self.assertEqual(
                (state_dir / "reviewed.plan.json").read_text(encoding="utf-8"),
                prior_plan,
            )
            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(first.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertTrue(dry.ok)
            self.assertEqual(dry.code, "apply.dry_run_ok")

    def test_new_file_post_replace_chmod_failure_leaves_no_unowned_output(self):
        import bootstrap.scaffold as scaffold_mod

        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            state_dir = root / "state" / "agent-arena"
            prior_owned = (state_dir / "scaffold.owned.json").read_text(encoding="utf-8")
            prior_manifest = (state_dir / "bootstrap.manifest.json").read_text(
                encoding="utf-8"
            )
            prior_plan = (state_dir / "reviewed.plan.json").read_text(encoding="utf-8")
            prior_content = (state_dir / "content.manifest.json").read_text(
                encoding="utf-8"
            )

            zzz_src = staged / "app" / "zzz-new.txt"
            zzz_src.write_text("brand-new-owned-file\n", encoding="utf-8")
            zzz_out = root / "out" / "app" / "zzz-new.txt"
            self.assertFalse(zzz_out.exists())

            real_chmod = scaffold_mod.os.chmod

            def _chmod_fail_zzz(path, mode, *args, **kwargs):
                if Path(path) == zzz_out:
                    raise OSError("injected-post-replace-chmod-failure")
                return real_chmod(path, mode, *args, **kwargs)

            scaffold_mod.os.chmod = _chmod_fail_zzz  # type: ignore[assignment]
            try:
                with self.assertRaises(InitializerError) as ctx:
                    plan_app(
                        config=config,
                        template_revision=PINNED_TEMPLATE_REVISION,
                        template_root=staged,
                        output_root=root / "out",
                        private_state_root=root / "state",
                    )
                self.assertEqual(ctx.exception.code, ERR_OUTPUT_ROOT)
            finally:
                scaffold_mod.os.chmod = real_chmod  # type: ignore[assignment]

            self.assertFalse(zzz_out.exists())
            self.assertEqual(
                (state_dir / "scaffold.owned.json").read_text(encoding="utf-8"),
                prior_owned,
            )
            self.assertEqual(
                (state_dir / "bootstrap.manifest.json").read_text(encoding="utf-8"),
                prior_manifest,
            )
            self.assertEqual(
                (state_dir / "reviewed.plan.json").read_text(encoding="utf-8"),
                prior_plan,
            )
            self.assertEqual(
                (state_dir / "content.manifest.json").read_text(encoding="utf-8"),
                prior_content,
            )

            third = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=root / "out",
                private_state_root=root / "state",
            )
            self.assertTrue(third.ok)
            self.assertTrue(zzz_out.is_file())
            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(third.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertTrue(dry.ok)
            self.assertEqual(dry.code, "apply.dry_run_ok")

    def test_existing_file_post_replace_fsync_failure_restores_prior_bytes(self):
        import bootstrap.scaffold as scaffold_mod

        config = _load(CONFIG_ARENA)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "template"
            out = root / "out"
            _copy_fixture_template(TEMPLATE_ROOT, staged)
            first = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=out,
                private_state_root=root / "state",
            )
            self.assertTrue(first.ok)
            state_dir = root / "state" / "agent-arena"
            prior_owned = (state_dir / "scaffold.owned.json").read_text(encoding="utf-8")
            prior_manifest = (state_dir / "bootstrap.manifest.json").read_text(
                encoding="utf-8"
            )
            prior_plan = (state_dir / "reviewed.plan.json").read_text(encoding="utf-8")
            prior_content = (state_dir / "content.manifest.json").read_text(
                encoding="utf-8"
            )
            owned_doc = json.loads(prior_owned)
            raw_files = owned_doc.get("files") or {}
            hashes = {
                str(rel): str(digest)
                for rel, digest in raw_files.items()
                if isinstance(rel, str) and isinstance(digest, str)
            }
            owned = set(hashes)

            readme_out = out / "app" / "README.md"
            prior_readme = readme_out.read_bytes()
            readme_src = staged / "app" / "README.md"
            readme_src.write_text(
                readme_src.read_text(encoding="utf-8") + "post-replace-fsync-bump\n",
                encoding="utf-8",
            )

            real_fsync_dir = scaffold_mod.fsync_dir
            baseline = {"bytes": prior_readme}

            def _fsync_fail_when_readme_mutated(path):
                if Path(path) == readme_out.parent:
                    try:
                        current = readme_out.read_bytes()
                    except OSError:
                        current = None
                    if current is not None and current != baseline["bytes"]:
                        raise OSError("injected-post-replace-fsync-failure")
                return real_fsync_dir(path)

            # Call materialize directly so engine metadata restore cannot mask the
            # post-replace journal hole for an already-owned path.
            scaffold_mod.fsync_dir = _fsync_fail_when_readme_mutated  # type: ignore[assignment]
            try:
                with self.assertRaises(InitializerError) as ctx:
                    materialize_scaffold(
                        template_root=staged,
                        output_root=out,
                        config=config,
                        template_revision=PINNED_TEMPLATE_REVISION,
                        owned_relative=owned,
                        owned_hashes=hashes,
                    )
                self.assertEqual(ctx.exception.code, ERR_OUTPUT_ROOT)
            finally:
                scaffold_mod.fsync_dir = real_fsync_dir  # type: ignore[assignment]

            self.assertEqual(readme_out.read_bytes(), prior_readme)
            self.assertEqual(
                (state_dir / "scaffold.owned.json").read_text(encoding="utf-8"),
                prior_owned,
            )
            self.assertEqual(
                (state_dir / "bootstrap.manifest.json").read_text(encoding="utf-8"),
                prior_manifest,
            )
            self.assertEqual(
                (state_dir / "reviewed.plan.json").read_text(encoding="utf-8"),
                prior_plan,
            )
            self.assertEqual(
                (state_dir / "content.manifest.json").read_text(encoding="utf-8"),
                prior_content,
            )

            third = plan_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                template_root=staged,
                output_root=out,
                private_state_root=root / "state",
            )
            self.assertTrue(third.ok)
            self.assertNotEqual(third.plan_digest, first.plan_digest)
            self.assertIn(b"post-replace-fsync-bump\n", readme_out.read_bytes())
            dry = apply_app(
                config=config,
                template_revision=PINNED_TEMPLATE_REVISION,
                private_state_root=root / "state",
                plan_digest=str(third.plan_digest),
                adapter=BlockedLiveApplyAdapter(),
                dry_run=True,
            )
            self.assertTrue(dry.ok)
            self.assertEqual(dry.code, "apply.dry_run_ok")


def _copy_fixture_template(src: Path, dst: Path) -> None:
    import shutil

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""APP-005 initializer regression tests (offline generation + fixture adapters).

No live provider, DNS, Terraform apply, or remote database mutations.
Live apply remains gated by BlockedLiveApplyAdapter.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(REPO_ROOT))

from bootstrap import PINNED_TEMPLATE_REVISION  # noqa: E402
from bootstrap.adapters import (  # noqa: E402
    BlockedLiveApplyAdapter,
    ClaimingLiveAdapter,
    RecordingApplyAdapter,
)
from bootstrap.engine import apply_app, plan_app, resume_app  # noqa: E402
from bootstrap.errors import (  # noqa: E402
    ERR_ADOPT_BY_NAME,
    ERR_CONCURRENT,
    ERR_DRIFT_CONFIG,
    ERR_DRIFT_PLAN,
    ERR_LIVE_CLAIM,
    ERR_LIVE_MISSING,
    ERR_OVERWRITE,
    ERR_PATH_ESCAPE,
    ERR_PLAN_DIGEST,
    ERR_SYMLINK,
    ERR_TEMPLATE_REV,
    ERR_TEMPLATE_SNAPSHOT,
    InitializerError,
)
from bootstrap.lock import AppLock, exclusive_app_lock  # noqa: E402
from bootstrap.paths import iter_template_files  # noqa: E402
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


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
        canary = (TEMPLATE_ROOT / ".env").read_text(encoding="utf-8").strip()
        self.assertTrue(canary)
        # Ensure walker never yields excluded secrets.
        rels = {
            p.relative_to(TEMPLATE_ROOT).as_posix()
            for p in iter_template_files(TEMPLATE_ROOT)
        }
        self.assertNotIn(".env", rels)
        self.assertNotIn("platforms.json", rels)
        self.assertNotIn("supabase/.temp/project-ref", rels)


def _copy_fixture_template(src: Path, dst: Path) -> None:
    import shutil

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False)


if __name__ == "__main__":
    unittest.main()

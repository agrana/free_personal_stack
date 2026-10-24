"""Plan / apply / resume orchestration for APP-005."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set

from bootstrap import ALL_STAGES, EXTERNAL_STAGES, LOCAL_STAGES, PINNED_TEMPLATE_REVISION
from bootstrap.adapters import (
    ApplyAdapter,
    ApplyContext,
    BlockedLiveApplyAdapter,
    CapabilityReport,
    ClaimingLiveAdapter,
)
from bootstrap.errors import (
    ERR_CAPABILITY_BINDING,
    ERR_CAPABILITY_METADATA,
    ERR_CAPABILITY_UNVERIFIED,
    ERR_CHECKPOINT_RESET,
    ERR_CONFIG,
    ERR_DRIFT_CONFIG,
    ERR_DRIFT_PLAN,
    ERR_DRIFT_TEMPLATE,
    ERR_GENERATED_EDIT,
    ERR_LIVE_CLAIM,
    ERR_LIVE_MISSING,
    ERR_NO_AUTO_DELETE,
    ERR_PLAN_DIGEST,
    ERR_PLAN_MISSING,
    ERR_PLAN_REQUIRED,
    ERR_RESUME,
    ERR_ROLLBACK_INCOMPLETE,
    ERR_STATUS,
    InterruptStage,
    InitializerError,
)
from bootstrap.hashing import config_identity_hash
from bootstrap.lock import exclusive_app_lock
from bootstrap.manifest import (
    atomic_write_manifest,
    atomic_write_text,
    empty_manifest,
    load_manifest,
    mark_failure,
    mark_stage_complete,
)
from bootstrap.plan import (
    assert_apply_dns_gates,
    build_plan_document,
    digest_for_review,
    dns_choice_of,
    refuse_raw_terraform_in_plan,
    require_pinned_revision,
)
from bootstrap.paths import safe_join
from bootstrap.safe_fs import ensure_owned_directory, path_is_symlink
from bootstrap.scaffold import materialize_scaffold, verify_owned_output_tree
from bootstrap.snapshot import build_content_manifest
from bootstrap.validators_bridge import (
    assert_metadata_not_live_proof,
    run_secrets_preflight,
    validate_app_config,
)

REQUIRED_CAPABILITIES = (
    "vercel.project.write",
    "dns.record.write",
    "supabase.shared.reference",
)

# Account/capability evidence is invocation- and digest-bound; never reuse across replan.
PERMISSION_STAGES = frozenset(
    {
        "verify_account_identity",
        "verify_capabilities",
        "inventory_conflicts",
    }
)


@dataclass
class CommandResult:
    ok: bool
    code: str
    plan_digest: Optional[str] = None
    status: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


def _next_after(stage: str) -> Optional[str]:
    stages = list(ALL_STAGES)
    try:
        idx = stages.index(stage)
    except ValueError:
        return None
    if idx + 1 >= len(stages):
        return None
    return stages[idx + 1]


def _state_dir(private_state_root: Path, app_slug: str) -> Path:
    root = ensure_owned_directory(private_state_root)
    path = root / app_slug
    if path_is_symlink(path):
        from bootstrap.errors import ERR_SYMLINK

        raise InitializerError(ERR_SYMLINK)
    return ensure_owned_directory(path, root=root)


def _manifest_path(state_dir: Path) -> Path:
    return state_dir / "bootstrap.manifest.json"


def _plan_path(state_dir: Path) -> Path:
    return state_dir / "reviewed.plan.json"


def _lock_path(state_dir: Path) -> Path:
    return state_dir / "bootstrap.lock"


def _owned_path(state_dir: Path) -> Path:
    return state_dir / "scaffold.owned.json"


def _content_manifest_path(state_dir: Path) -> Path:
    return state_dir / "content.manifest.json"


def _repo_fields(config: Mapping[str, Any]) -> tuple[str, str]:
    repo = config.get("repository") if isinstance(config.get("repository"), Mapping) else {}
    owner = repo.get("owner")
    name = repo.get("name")
    if not isinstance(owner, str) or not isinstance(name, str):
        raise InitializerError(ERR_CONFIG)
    return owner, name


def _slug(config: Mapping[str, Any]) -> str:
    app = config.get("app") if isinstance(config.get("app"), Mapping) else {}
    slug = app.get("slug")
    if not isinstance(slug, str):
        raise InitializerError(ERR_CONFIG)
    return slug


def _load_owned_record(path: Path) -> tuple[Set[str], Dict[str, str]]:
    owned: Set[str] = set()
    hashes: Dict[str, str] = {}
    if not path.is_file() or path_is_symlink(path):
        return owned, hashes
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return owned, hashes
    if isinstance(prev, list):
        owned = {str(item) for item in prev}
    elif isinstance(prev, dict):
        files = prev.get("files")
        if isinstance(files, dict):
            for key, value in files.items():
                if isinstance(key, str) and isinstance(value, str):
                    hashes[key] = value
                    owned.add(key)
        elif isinstance(files, list):
            owned = {str(item) for item in files}
    return owned, hashes


def _write_owned_record(path: Path, owned: Set[str], hashes: Mapping[str, str]) -> None:
    payload = {
        "files": {rel: hashes[rel] for rel in sorted(owned) if rel in hashes},
    }
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
    )


def _read_bytes_if_regular(path: Path) -> Optional[bytes]:
    if path_is_symlink(path) or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _snapshot_owned_outputs(
    output_root: Path, owned_hashes: Mapping[str, str]
) -> Dict[str, bytes]:
    snap: Dict[str, bytes] = {}
    if not owned_hashes:
        return snap
    try:
        root = output_root.resolve(strict=False)
    except OSError:
        return snap
    if not root.is_dir():
        return snap

    for rel in owned_hashes:
        if not isinstance(rel, str):
            continue
        try:
            dest = safe_join(root, rel)
        except InitializerError:
            continue
        data = _read_bytes_if_regular(dest)
        if data is not None:
            snap[rel] = data
    return snap


def _restore_bytes_file(path: Path, payload: Optional[bytes]) -> bool:
    """Restore or remove a state file without going through atomic_write_text.

    Rollback must not depend on the same persistence helper that may have failed
    (or been fault-injected) during the forward path. Returns True on verified restore.
    """
    try:
        if payload is None:
            if path.exists() and not path_is_symlink(path):
                path.unlink()
            return not (path.exists() and path.is_file() and not path_is_symlink(path))
        if path_is_symlink(path):
            return False
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path.is_file() and not path_is_symlink(path) and path.read_bytes() == payload
    except OSError:
        return False


def _restore_plan_outputs(
    *,
    output_root: Path,
    prior_files: Mapping[str, bytes],
    prior_rels: Set[str],
    new_rels: Set[str],
    state_snapshots: Mapping[str, Optional[bytes]],
) -> None:
    """Restore scaffold outputs and plan metadata after a failed persist.

    Verifies each restore step. Raises output.rollback_incomplete when a required
    restore cannot be confirmed — this is not a crash-atomic guarantee.
    """
    incomplete = False
    try:
        root = output_root.resolve(strict=False)
    except OSError:
        root = output_root
    for rel in sorted(new_rels - prior_rels):
        try:
            dest = safe_join(root, rel)
        except InitializerError:
            incomplete = True
            continue
        if dest.exists() and not path_is_symlink(dest):
            try:
                dest.unlink()
            except OSError:
                incomplete = True
        if dest.exists() and dest.is_file() and not path_is_symlink(dest):
            incomplete = True
    for rel, data in prior_files.items():
        try:
            dest = safe_join(root, rel)
        except InitializerError:
            incomplete = True
            continue
        try:
            if not dest.parent.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if not (
                dest.is_file()
                and not path_is_symlink(dest)
                and dest.read_bytes() == data
            ):
                incomplete = True
        except OSError:
            incomplete = True
    for path_key, payload in state_snapshots.items():
        if not _restore_bytes_file(Path(path_key), payload):
            incomplete = True
    if incomplete:
        raise InitializerError(ERR_ROLLBACK_INCOMPLETE)


def _has_external_checkpoint(manifest: Mapping[str, Any]) -> bool:
    status = manifest.get("status")
    if status in {"interrupted", "applying", "failed"}:
        return True
    resource_ids = manifest.get("resourceIds") if isinstance(manifest.get("resourceIds"), Mapping) else {}
    if resource_ids.get("vercelProjectId"):
        return True
    if resource_ids.get("dnsRecordIds"):
        return True
    if resource_ids.get("infraStateKey"):
        return True
    completed = set(manifest.get("completedStages") or [])
    return bool(completed.intersection(EXTERNAL_STAGES))


def _strip_permission_stages(manifest: Dict[str, Any]) -> None:
    """Keep remote resource checkpoints; drop digest-bound permission evidence."""
    completed = [s for s in (manifest.get("completedStages") or []) if s not in PERMISSION_STAGES]
    manifest["completedStages"] = completed
    resume = manifest.get("resumeState") if isinstance(manifest.get("resumeState"), Mapping) else {}
    next_stage = resume.get("nextStage")
    if next_stage in PERMISSION_STAGES or next_stage in EXTERNAL_STAGES or next_stage == "finalize":
        manifest["resumeState"] = {
            "nextStage": "verify_account_identity",
            "failureCode": None,
        }


def _assert_capability_report(
    report: CapabilityReport,
    *,
    ctx: ApplyContext,
    plan_digest: str,
) -> None:
    if report.operation_kind != "provider_operation":
        raise InitializerError(ERR_LIVE_CLAIM)
    verified = report.verified if isinstance(report.verified, Mapping) else {}
    for key in REQUIRED_CAPABILITIES:
        if verified.get(key) is not True:
            raise InitializerError(ERR_CAPABILITY_UNVERIFIED)
    if (
        report.app_slug != ctx.app_slug
        or report.config_hash != ctx.config_hash
        or report.plan_digest != plan_digest
        or report.provider != "vercel"
        or report.operation != "apply"
    ):
        raise InitializerError(ERR_CAPABILITY_BINDING)


def plan_app(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    template_root: Path,
    output_root: Path,
    private_state_root: Path,
    config_mode: str = "draft",
) -> CommandResult:
    """Read-only regarding remotes. May materialize local scaffold + plan artifact."""
    require_pinned_revision(template_revision)
    validate_app_config(config, mode=config_mode)
    assert_metadata_not_live_proof(config)

    slug = _slug(config)
    owner, name = _repo_fields(config)
    config_hash = config_identity_hash(config)
    dns_choice = dns_choice_of(config)

    state_dir = _state_dir(private_state_root, slug)
    with exclusive_app_lock(_lock_path(state_dir)):
        owned_path = _owned_path(state_dir)
        owned, owned_hashes = _load_owned_record(owned_path)

        existing_manifest_path = _manifest_path(state_dir)
        prev_manifest: Optional[dict] = None
        if path_is_symlink(existing_manifest_path):
            from bootstrap.errors import ERR_SYMLINK

            raise InitializerError(ERR_SYMLINK)
        if existing_manifest_path.is_file():
            # Fail closed: never drop a corrupt manifest and silently reset ownership.
            prev_manifest = load_manifest(existing_manifest_path)

        if prev_manifest is not None:
            if (
                prev_manifest.get("configHash") == config_hash
                and prev_manifest.get("templateRevision") == template_revision
                and prev_manifest.get("status") == "completed"
            ):
                plan_file = _plan_path(state_dir)
                if not plan_file.is_file() or path_is_symlink(plan_file):
                    raise InitializerError(ERR_PLAN_MISSING)
                plan_doc = json.loads(plan_file.read_text(encoding="utf-8"))
                if not isinstance(plan_doc, dict):
                    raise InitializerError(ERR_PLAN_MISSING)
                expected = str(prev_manifest.get("planDigest") or "")
                _revalidate_reviewed_bindings(
                    config=config,
                    template_revision=template_revision,
                    state_dir=state_dir,
                    manifest=prev_manifest,
                    plan_doc=plan_doc,
                    expected_plan_digest=expected,
                    output_root=output_root,
                )
                return CommandResult(
                    ok=True,
                    code="plan.noop",
                    plan_digest=expected,
                    status="completed",
                    details={"operationKind": "planning_simulation"},
                )
            if _has_external_checkpoint(prev_manifest):
                if (
                    prev_manifest.get("configHash") != config_hash
                    or prev_manifest.get("templateRevision") != template_revision
                ):
                    raise InitializerError(ERR_CHECKPOINT_RESET)

        prior_rels = set(owned_hashes)
        prior_files = _snapshot_owned_outputs(output_root, owned_hashes)
        content_path = _content_manifest_path(state_dir)
        plan_file = _plan_path(state_dir)
        manifest_path = _manifest_path(state_dir)
        state_snapshots = {
            str(content_path): _read_bytes_if_regular(content_path),
            str(plan_file): _read_bytes_if_regular(plan_file),
            str(owned_path): _read_bytes_if_regular(owned_path),
            str(manifest_path): _read_bytes_if_regular(manifest_path),
        }

        try:
            (
                file_count,
                scaffold_digest,
                owned,
                owned_hashes,
                content_digest,
                content_provenance,
            ) = materialize_scaffold(
                template_root=template_root,
                output_root=output_root,
                config=config,
                template_revision=template_revision,
                owned_relative=owned,
                owned_hashes=owned_hashes,
            )

            content_manifest = build_content_manifest(template_root)
            content_manifest["contentSnapshotDigest"] = content_digest
            content_manifest["contentProvenance"] = content_provenance
            atomic_write_text(
                content_path,
                json.dumps(content_manifest, indent=2, sort_keys=True, ensure_ascii=True)
                + "\n",
            )

            plan_doc = build_plan_document(
                config=config,
                template_revision=template_revision,
                output_root=str(output_root),
                repository_name=name,
                repository_owner=owner,
                intended_stages=list(ALL_STAGES),
            )
            # Bind actual template/scaffold contents into the reviewed digest.
            plan_doc["contentSnapshotDigest"] = content_digest
            plan_doc["scaffoldDigest"] = scaffold_digest
            plan_doc["contentProvenance"] = content_provenance
            plan_doc["contentManifest"] = {
                "fileCount": len(content_manifest.get("files") or []),
                "contentSnapshotDigest": content_digest,
                "contentProvenance": content_provenance,
            }
            refuse_raw_terraform_in_plan(plan_doc)
            digest = digest_for_review(plan_doc)
            plan_doc["planDigest"] = digest

            atomic_write_text(
                plan_file,
                json.dumps(plan_doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            )

            if prev_manifest is not None and _has_external_checkpoint(prev_manifest):
                # Preserve remote ownership checkpoints; refresh local plan bindings only.
                # Drop account/capability evidence bound to the prior digest/invocation.
                manifest = dict(prev_manifest)
                manifest["configHash"] = config_hash
                manifest["templateRevision"] = template_revision
                manifest["planDigest"] = digest
                manifest["scaffold"] = {
                    "outputRoot": str(output_root.resolve()),
                    "fileCount": file_count,
                    "scaffoldDigest": scaffold_digest,
                }
                _strip_permission_stages(manifest)
                if manifest.get("status") not in {"interrupted", "applying", "failed"}:
                    manifest["status"] = "interrupted"
            else:
                manifest = empty_manifest(
                    app_slug=slug,
                    config_hash=config_hash,
                    template_revision=template_revision,
                    plan_digest=digest,
                    output_root=str(output_root.resolve()),
                    dns_resolved=dns_choice != "unresolved",
                )
                for stage in LOCAL_STAGES:
                    mark_stage_complete(manifest, stage, _next_after(stage))
                manifest["scaffold"] = {
                    "outputRoot": str(output_root.resolve()),
                    "fileCount": file_count,
                    "scaffoldDigest": scaffold_digest,
                }
                manifest["status"] = "planned"
                manifest["resumeState"] = {
                    "nextStage": EXTERNAL_STAGES[0],
                    "failureCode": None,
                }

            _write_owned_record(owned_path, owned, owned_hashes)
            atomic_write_manifest(manifest_path, manifest)
        except Exception as exc:
            try:
                _restore_plan_outputs(
                    output_root=output_root,
                    prior_files=prior_files,
                    prior_rels=prior_rels,
                    new_rels=set(owned_hashes),
                    state_snapshots=state_snapshots,
                )
            except InitializerError as rollback_exc:
                if rollback_exc.code == ERR_ROLLBACK_INCOMPLETE:
                    raise rollback_exc from exc
                raise
            raise

    return CommandResult(
        ok=True,
        code="plan.ok",
        plan_digest=digest,
        status=str(manifest.get("status") or "planned"),
        details={
            "operationKind": "planning_simulation",
            "scaffoldFiles": file_count,
            "nextStage": (manifest.get("resumeState") or {}).get("nextStage"),
            "contentSnapshotDigest": content_digest,
            "scaffoldDigest": scaffold_digest,
        },
    )


def _load_bindings_under_lock(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    state_dir: Path,
    expected_plan_digest: str,
    output_root: Optional[Path] = None,
) -> tuple[str, dict, dict]:
    """Validate plan/manifest/output bindings. Caller must hold the app lock."""
    require_pinned_revision(template_revision)
    slug = _slug(config)
    manifest_file = _manifest_path(state_dir)
    plan_file = _plan_path(state_dir)
    if not manifest_file.is_file() or not plan_file.is_file():
        raise InitializerError(ERR_PLAN_MISSING)
    if path_is_symlink(manifest_file) or path_is_symlink(plan_file):
        from bootstrap.errors import ERR_SYMLINK

        raise InitializerError(ERR_SYMLINK)
    manifest = load_manifest(manifest_file)
    try:
        plan_doc = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise InitializerError(ERR_PLAN_MISSING) from None
    if not isinstance(plan_doc, dict):
        raise InitializerError(ERR_PLAN_MISSING)
    _revalidate_reviewed_bindings(
        config=config,
        template_revision=template_revision,
        state_dir=state_dir,
        manifest=manifest,
        plan_doc=plan_doc,
        expected_plan_digest=expected_plan_digest,
        output_root=output_root,
    )
    return slug, manifest, plan_doc


def _revalidate_reviewed_bindings(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    state_dir: Path,
    manifest: Mapping[str, Any],
    plan_doc: Mapping[str, Any],
    expected_plan_digest: str,
    output_root: Optional[Path] = None,
) -> None:
    """Re-check metadata and on-disk generated artifacts against the reviewed digest."""
    refuse_raw_terraform_in_plan(plan_doc)

    config_hash = config_identity_hash(config)
    if manifest["configHash"] != config_hash:
        raise InitializerError(ERR_DRIFT_CONFIG)
    if manifest["templateRevision"] != template_revision:
        raise InitializerError(ERR_DRIFT_TEMPLATE)
    if manifest["planDigest"] != expected_plan_digest:
        raise InitializerError(ERR_PLAN_DIGEST)
    if digest_for_review(plan_doc) != expected_plan_digest:
        raise InitializerError(ERR_DRIFT_PLAN)
    if plan_doc.get("planDigest") != expected_plan_digest:
        raise InitializerError(ERR_DRIFT_PLAN)

    scaffold = manifest.get("scaffold") if isinstance(manifest.get("scaffold"), Mapping) else {}
    plan_scaffold = plan_doc.get("scaffoldDigest")
    manifest_scaffold = scaffold.get("scaffoldDigest")
    if not isinstance(plan_scaffold, str) or not plan_scaffold:
        raise InitializerError(ERR_DRIFT_PLAN)
    if plan_scaffold != manifest_scaffold:
        raise InitializerError(ERR_DRIFT_PLAN)
    content_digest = plan_doc.get("contentSnapshotDigest")
    if not isinstance(content_digest, str) or not content_digest:
        raise InitializerError(ERR_DRIFT_PLAN)

    declared_output = scaffold.get("outputRoot")
    if not isinstance(declared_output, str) or not declared_output:
        raise InitializerError(ERR_DRIFT_PLAN)
    declared_path = Path(declared_output)
    if output_root is not None:
        try:
            if declared_path.resolve(strict=False) != output_root.resolve(strict=False):
                raise InitializerError(ERR_DRIFT_PLAN)
        except OSError:
            raise InitializerError(ERR_DRIFT_PLAN) from None

    owned_path = _owned_path(state_dir)
    owned, owned_hashes = _load_owned_record(owned_path)
    if not owned_hashes or set(owned_hashes) != set(owned):
        raise InitializerError(ERR_GENERATED_EDIT)
    file_count = scaffold.get("fileCount")
    if isinstance(file_count, int) and file_count != len(owned_hashes):
        raise InitializerError(ERR_GENERATED_EDIT)

    verify_owned_output_tree(
        output_root=declared_path,
        owned_hashes=owned_hashes,
        template_revision=template_revision,
        content_digest=content_digest,
        expected_scaffold_digest=plan_scaffold,
    )


def _build_ctx(
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    plan_digest: str,
) -> ApplyContext:
    deployment = config["deployment"]
    database = config["database"]
    return ApplyContext(
        app_slug=_slug(config),
        deployment_identity=str(deployment["identity"]),
        state_key=str(deployment["stateKey"]),
        hostname=str(config["hostname"]),
        dns_choice=dns_choice_of(config),
        namespace=str(database["namespace"]),
        known_resource_ids=dict(manifest.get("resourceIds") or {}),
        secret_aliases_present={},
        config_hash=str(manifest.get("configHash") or ""),
        plan_digest=plan_digest,
    )


def _run_external_stages(
    *,
    adapter: ApplyAdapter,
    config: Mapping[str, Any],
    manifest: Dict[str, Any],
    state_dir: Path,
    start_stage: str,
    dry_run: bool,
    plan_digest: str,
) -> None:
    stages = list(EXTERNAL_STAGES) + ["finalize"]
    if start_stage not in stages:
        raise InitializerError(ERR_RESUME)
    start_idx = stages.index(start_stage)
    ctx = _build_ctx(config, manifest, plan_digest=plan_digest)

    if isinstance(adapter, ClaimingLiveAdapter):
        raise InitializerError(ERR_LIVE_CLAIM)

    completed = set(manifest.get("completedStages") or [])
    # Permission evidence must be re-established for this invocation + digest.
    # Remote resource stage completions (ensure_*) remain skippable checkpoints.
    completed_for_run = set(completed) - PERMISSION_STAGES
    capabilities_verified = False
    if start_stage in PERMISSION_STAGES or start_stage in {
        "ensure_vercel_project",
        "ensure_dns_records",
        "bind_infra_state",
        "finalize",
    }:
        start_stage = "verify_account_identity"
        start_idx = stages.index(start_stage)

    for stage in stages[start_idx:]:
        if stage in completed_for_run:
            continue
        manifest["status"] = "applying"
        atomic_write_manifest(_manifest_path(state_dir), manifest)
        try:
            if dry_run:
                mark_stage_complete(manifest, stage, _next_after(stage))
                atomic_write_manifest(_manifest_path(state_dir), manifest)
                continue

            if stage == "verify_account_identity":
                identity = adapter.verify_account_identity(ctx)
                if identity.operation_kind != "provider_operation":
                    raise InitializerError(ERR_LIVE_CLAIM)
            elif stage == "verify_capabilities":
                caps = adapter.verify_capabilities(ctx)
                _assert_capability_report(caps, ctx=ctx, plan_digest=plan_digest)
                capabilities_verified = True
            elif stage == "inventory_conflicts":
                if not capabilities_verified:
                    raise InitializerError(ERR_CAPABILITY_UNVERIFIED)
                inventory = adapter.inventory_conflicts(ctx)
                if inventory.operation_kind != "provider_operation":
                    raise InitializerError(ERR_LIVE_CLAIM)
            elif stage == "ensure_vercel_project":
                if not capabilities_verified:
                    raise InitializerError(ERR_CAPABILITY_UNVERIFIED)
                project_id = adapter.ensure_vercel_project(ctx)
                manifest["resourceIds"]["vercelProjectId"] = project_id
                ctx.known_resource_ids["vercelProjectId"] = project_id
            elif stage == "ensure_dns_records":
                if not capabilities_verified:
                    raise InitializerError(ERR_CAPABILITY_UNVERIFIED)
                dns_ids = adapter.ensure_dns_records(ctx)
                manifest["resourceIds"]["dnsRecordIds"] = list(dns_ids)
                ctx.known_resource_ids["dnsRecordIds"] = list(dns_ids)
            elif stage == "bind_infra_state":
                if not capabilities_verified:
                    raise InitializerError(ERR_CAPABILITY_UNVERIFIED)
                key = adapter.bind_infra_state(ctx)
                manifest["resourceIds"]["infraStateKey"] = key
                ctx.known_resource_ids["infraStateKey"] = key
            elif stage == "finalize":
                if not capabilities_verified:
                    raise InitializerError(ERR_CAPABILITY_UNVERIFIED)
                manifest["status"] = "completed"
                mark_stage_complete(manifest, stage, None)
                atomic_write_manifest(_manifest_path(state_dir), manifest)
                return
            else:
                raise InitializerError(ERR_RESUME)

            mark_stage_complete(manifest, stage, _next_after(stage))
            completed_for_run.add(stage)
            atomic_write_manifest(_manifest_path(state_dir), manifest)
        except InterruptStage as exc:
            mark_failure(manifest, stage=stage, code=str(exc.code))
            atomic_write_manifest(_manifest_path(state_dir), manifest)
            if hasattr(adapter, "attempt_delete_on_failure"):
                try:
                    adapter.attempt_delete_on_failure()  # type: ignore[attr-defined]
                except InitializerError as delete_exc:
                    if delete_exc.code != ERR_NO_AUTO_DELETE:
                        raise
            raise
        except InitializerError as exc:
            mark_failure(manifest, stage=stage, code=exc.code)
            atomic_write_manifest(_manifest_path(state_dir), manifest)
            if hasattr(adapter, "attempt_delete_on_failure"):
                try:
                    adapter.attempt_delete_on_failure()  # type: ignore[attr-defined]
                except InitializerError as delete_exc:
                    if delete_exc.code != ERR_NO_AUTO_DELETE:
                        raise
            raise


def apply_app(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    private_state_root: Path,
    plan_digest: str,
    adapter: Optional[ApplyAdapter] = None,
    secret_values: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
    allow_unresolved_dns_for_tests: bool = False,
) -> CommandResult:
    """Explicit approved apply. Default adapter blocks live cloud mutations."""
    try:
        return _apply_app_impl(
            config=config,
            template_revision=template_revision,
            private_state_root=private_state_root,
            plan_digest=plan_digest,
            adapter=adapter,
            secret_values=secret_values,
            dry_run=dry_run,
            allow_unresolved_dns_for_tests=allow_unresolved_dns_for_tests,
        )
    except InterruptStage:
        return CommandResult(
            ok=False,
            code="stage.interrupted",
            plan_digest=plan_digest or None,
            status="interrupted",
        )
    except InitializerError as exc:
        return CommandResult(
            ok=False,
            code=exc.code,
            plan_digest=plan_digest or None,
            status="failed",
        )


def _apply_app_impl(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    private_state_root: Path,
    plan_digest: str,
    adapter: Optional[ApplyAdapter] = None,
    secret_values: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
    allow_unresolved_dns_for_tests: bool = False,
) -> CommandResult:
    if not plan_digest:
        raise InitializerError(ERR_PLAN_REQUIRED)

    validate_app_config(config, mode="draft")
    assert_metadata_not_live_proof(config)
    if not allow_unresolved_dns_for_tests:
        assert_apply_dns_gates(config)
    else:
        if dns_choice_of(config) == "cloudfront":
            from bootstrap.errors import ERR_DNS_CLOUDFRONT

            raise InitializerError(ERR_DNS_CLOUDFRONT)

    # Caller metadata never proves live permission.
    if config.get("externalVerification", {}).get("accountPermissions") == "verified":
        pass

    active_adapter: ApplyAdapter = adapter or BlockedLiveApplyAdapter()
    if isinstance(active_adapter, ClaimingLiveAdapter):
        raise InitializerError(ERR_LIVE_CLAIM)

    slug = _slug(config)
    state_dir = _state_dir(private_state_root, slug)

    if secret_values is not None:
        aliases = config.get("secretAliases") or {}
        if not isinstance(aliases, Mapping):
            raise InitializerError(ERR_CONFIG)
        report = run_secrets_preflight(
            alias_map={str(k): str(v) for k, v in aliases.items()},
            values=secret_values,
            app_id=slug,
        )
        if report.get("deploymentAuthorized") is True:
            raise InitializerError(ERR_CAPABILITY_METADATA)

    with exclusive_app_lock(_lock_path(state_dir)):
        _slug_b, manifest, _plan_doc = _load_bindings_under_lock(
            config=config,
            template_revision=template_revision,
            state_dir=state_dir,
            expected_plan_digest=plan_digest,
        )
        if manifest["status"] == "completed":
            return CommandResult(
                ok=True,
                code="apply.noop",
                plan_digest=plan_digest,
                status="completed",
                details={
                    "operationKind": (
                        "provider_operation" if not dry_run else "planning_simulation"
                    )
                },
            )
        if manifest["status"] not in {"planned", "applying", "interrupted", "failed"}:
            raise InitializerError(ERR_STATUS)

        next_stage = manifest.get("resumeState", {}).get("nextStage") or EXTERNAL_STAGES[0]
        if next_stage in LOCAL_STAGES:
            next_stage = EXTERNAL_STAGES[0]

        if dry_run:
            return CommandResult(
                ok=True,
                code="apply.dry_run_ok",
                plan_digest=plan_digest,
                status=manifest["status"],
                details={
                    "operationKind": "planning_simulation",
                    "durableMutation": False,
                    "nextStage": next_stage,
                    "resourceIds": manifest.get("resourceIds"),
                },
            )

        if isinstance(active_adapter, BlockedLiveApplyAdapter):
            mark_failure(
                manifest,
                stage=str(next_stage),
                code=ERR_LIVE_MISSING,
            )
            atomic_write_manifest(_manifest_path(state_dir), manifest)
            return CommandResult(
                ok=False,
                code=ERR_LIVE_MISSING,
                plan_digest=plan_digest,
                status=manifest["status"],
                details={
                    "operationKind": "blocked",
                    "gate": "live_apply_integration_missing",
                    "note": "Default adapter refuses remote mutations; injectable adapter required for provider_operation tests only.",
                },
            )

        try:
            _run_external_stages(
                adapter=active_adapter,
                config=config,
                manifest=manifest,
                state_dir=state_dir,
                start_stage=str(next_stage),
                dry_run=False,
                plan_digest=plan_digest,
            )
            manifest = load_manifest(_manifest_path(state_dir))
            return CommandResult(
                ok=True,
                code="apply.ok",
                plan_digest=plan_digest,
                status=manifest["status"],
                details={
                    "operationKind": "provider_operation",
                    "resourceIds": manifest.get("resourceIds"),
                },
            )
        except InterruptStage:
            return CommandResult(
                ok=False,
                code="stage.interrupted",
                plan_digest=plan_digest,
                status="interrupted",
            )
        except InitializerError as exc:
            return CommandResult(
                ok=False,
                code=exc.code,
                plan_digest=plan_digest,
                status="failed",
            )


def resume_app(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    private_state_root: Path,
    plan_digest: str,
    adapter: Optional[ApplyAdapter] = None,
    secret_values: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
) -> CommandResult:
    """Resume from durable manifest checkpoint after interruption.

    Uses the same locked binding + generated-file validation path as apply;
    there is no unlocked completed-noop fast path.
    """
    result = apply_app(
        config=config,
        template_revision=template_revision,
        private_state_root=private_state_root,
        plan_digest=plan_digest,
        adapter=adapter,
        secret_values=secret_values,
        dry_run=dry_run,
    )
    if result.code == "apply.noop":
        return CommandResult(
            ok=True,
            code="resume.noop",
            plan_digest=result.plan_digest,
            status=result.status,
            details=result.details,
        )
    if result.code == ERR_PLAN_MISSING:
        return CommandResult(
            ok=False,
            code=ERR_RESUME,
            plan_digest=plan_digest or None,
            status="failed",
        )
    return result


def default_pinned_revision() -> str:
    return PINNED_TEMPLATE_REVISION

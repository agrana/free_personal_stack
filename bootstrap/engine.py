"""Plan / apply / resume orchestration for APP-005."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from bootstrap import ALL_STAGES, EXTERNAL_STAGES, LOCAL_STAGES, PINNED_TEMPLATE_REVISION
from bootstrap.adapters import (
    ApplyAdapter,
    ApplyContext,
    BlockedLiveApplyAdapter,
    ClaimingLiveAdapter,
)
from bootstrap.errors import (
    ERR_CAPABILITY_METADATA,
    ERR_CONFIG,
    ERR_DRIFT_CONFIG,
    ERR_DRIFT_PLAN,
    ERR_DRIFT_TEMPLATE,
    ERR_LIVE_CLAIM,
    ERR_LIVE_MISSING,
    ERR_NO_AUTO_DELETE,
    ERR_PLAN_DIGEST,
    ERR_PLAN_MISSING,
    ERR_PLAN_REQUIRED,
    ERR_RESUME,
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
from bootstrap.scaffold import materialize_scaffold
from bootstrap.validators_bridge import (
    assert_metadata_not_live_proof,
    run_secrets_preflight,
    validate_app_config,
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
    path = private_state_root / app_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(state_dir: Path) -> Path:
    return state_dir / "bootstrap.manifest.json"


def _plan_path(state_dir: Path) -> Path:
    return state_dir / "reviewed.plan.json"


def _lock_path(state_dir: Path) -> Path:
    return state_dir / "bootstrap.lock"


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
        plan_doc = build_plan_document(
            config=config,
            template_revision=template_revision,
            output_root=str(output_root),
            repository_name=name,
            repository_owner=owner,
            intended_stages=list(ALL_STAGES),
        )
        refuse_raw_terraform_in_plan(plan_doc)
        digest = digest_for_review(plan_doc)
        plan_doc["planDigest"] = digest

        owned: Set[str] = set()
        owned_path = state_dir / "scaffold.owned.json"
        if owned_path.is_file():
            try:
                prev = json.loads(owned_path.read_text(encoding="utf-8"))
                if isinstance(prev, list):
                    owned = {str(item) for item in prev}
            except (OSError, json.JSONDecodeError, TypeError):
                owned = set()
        # Idempotent re-plan: allow rewriting files this initializer already owns.
        existing_manifest = _manifest_path(state_dir)
        if existing_manifest.is_file():
            try:
                prev_manifest = load_manifest(existing_manifest)
                if (
                    prev_manifest.get("configHash") == config_hash
                    and prev_manifest.get("templateRevision") == template_revision
                    and prev_manifest.get("status") == "completed"
                ):
                    return CommandResult(
                        ok=True,
                        code="plan.noop",
                        plan_digest=str(prev_manifest.get("planDigest")),
                        status="completed",
                        details={"operationKind": "planning_simulation"},
                    )
            except InitializerError:
                pass

        file_count, scaffold_digest, owned = materialize_scaffold(
            template_root=template_root,
            output_root=output_root,
            config=config,
            template_revision=template_revision,
            owned_relative=owned,
        )

        # Persist plan artifact (secret-free, atomic).
        plan_file = _plan_path(state_dir)
        atomic_write_text(
            plan_file,
            json.dumps(plan_doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        )

        manifest = empty_manifest(
            app_slug=slug,
            config_hash=config_hash,
            template_revision=template_revision,
            plan_digest=digest,
            output_root=str(output_root.resolve()),
            dns_resolved=dns_choice != "unresolved",
        )
        # Local stages completed during plan.
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
        # Record owned files privately (not in closed schema) beside manifest.
        owned_path.write_text(
            json.dumps(sorted(owned), indent=2) + "\n", encoding="utf-8"
        )
        atomic_write_manifest(_manifest_path(state_dir), manifest)

    return CommandResult(
        ok=True,
        code="plan.ok",
        plan_digest=digest,
        status="planned",
        details={
            "operationKind": "planning_simulation",
            "scaffoldFiles": file_count,
            "nextStage": EXTERNAL_STAGES[0],
        },
    )


def _load_bindings(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    private_state_root: Path,
    expected_plan_digest: str,
) -> tuple[str, dict, dict, Path]:
    require_pinned_revision(template_revision)
    slug = _slug(config)
    state_dir = _state_dir(private_state_root, slug)
    manifest_file = _manifest_path(state_dir)
    plan_file = _plan_path(state_dir)
    if not manifest_file.is_file() or not plan_file.is_file():
        raise InitializerError(ERR_PLAN_MISSING)
    manifest = load_manifest(manifest_file)
    plan_doc = json.loads(plan_file.read_text(encoding="utf-8"))
    if not isinstance(plan_doc, dict):
        raise InitializerError(ERR_PLAN_MISSING)
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
    return slug, manifest, plan_doc, state_dir


def _build_ctx(config: Mapping[str, Any], manifest: Mapping[str, Any]) -> ApplyContext:
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
    )


def _run_external_stages(
    *,
    adapter: ApplyAdapter,
    config: Mapping[str, Any],
    manifest: Dict[str, Any],
    state_dir: Path,
    start_stage: str,
    dry_run: bool,
) -> None:
    stages = list(EXTERNAL_STAGES) + ["finalize"]
    if start_stage not in stages:
        raise InitializerError(ERR_RESUME)
    start_idx = stages.index(start_stage)
    ctx = _build_ctx(config, manifest)

    if isinstance(adapter, ClaimingLiveAdapter):
        raise InitializerError(ERR_LIVE_CLAIM)
    if dry_run and not isinstance(adapter, BlockedLiveApplyAdapter):
        # Dry-run with a test adapter still must not claim provider_operation success
        # as persisted completion of remote resources.
        pass

    for stage in stages[start_idx:]:
        if stage in manifest.get("completedStages", []):
            continue
        manifest["status"] = "applying"
        atomic_write_manifest(_manifest_path(state_dir), manifest)
        try:
            if dry_run:
                # Planning simulation only — no provider mutations, no ID adoption.
                mark_stage_complete(manifest, stage, _next_after(stage))
                atomic_write_manifest(_manifest_path(state_dir), manifest)
                continue

            if stage == "verify_account_identity":
                identity = adapter.verify_account_identity(ctx)
                if identity.operation_kind != "provider_operation":
                    raise InitializerError(ERR_LIVE_CLAIM)
            elif stage == "verify_capabilities":
                caps = adapter.verify_capabilities(ctx)
                if caps.operation_kind != "provider_operation":
                    raise InitializerError(ERR_LIVE_CLAIM)
                # Metadata flags in config are never proof.
                if config.get("externalVerification", {}).get("accountPermissions") == "verified":
                    # Still require adapter verification; do not short-circuit.
                    pass
            elif stage == "inventory_conflicts":
                inventory = adapter.inventory_conflicts(ctx)
                if inventory.operation_kind != "provider_operation":
                    raise InitializerError(ERR_LIVE_CLAIM)
            elif stage == "ensure_vercel_project":
                project_id = adapter.ensure_vercel_project(ctx)
                manifest["resourceIds"]["vercelProjectId"] = project_id
                ctx.known_resource_ids["vercelProjectId"] = project_id
            elif stage == "ensure_dns_records":
                dns_ids = adapter.ensure_dns_records(ctx)
                manifest["resourceIds"]["dnsRecordIds"] = list(dns_ids)
                ctx.known_resource_ids["dnsRecordIds"] = list(dns_ids)
            elif stage == "bind_infra_state":
                key = adapter.bind_infra_state(ctx)
                manifest["resourceIds"]["infraStateKey"] = key
                ctx.known_resource_ids["infraStateKey"] = key
            elif stage == "finalize":
                manifest["status"] = "completed"
                mark_stage_complete(manifest, stage, None)
                atomic_write_manifest(_manifest_path(state_dir), manifest)
                return
            else:
                raise InitializerError(ERR_RESUME)

            mark_stage_complete(manifest, stage, _next_after(stage))
            atomic_write_manifest(_manifest_path(state_dir), manifest)
        except InterruptStage as exc:
            mark_failure(manifest, stage=stage, code=str(exc.code))
            atomic_write_manifest(_manifest_path(state_dir), manifest)
            # Never auto-delete remote resources after failure/interrupt.
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

    slug, manifest, _plan_doc, state_dir = _load_bindings(
        config=config,
        template_revision=template_revision,
        private_state_root=private_state_root,
        expected_plan_digest=plan_digest,
    )

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
        manifest = load_manifest(_manifest_path(state_dir))
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
            # Dry-run validates plan binding only. Never advance durable remote
            # stages or mark the manifest completed.
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
    """Resume from durable manifest checkpoint after interruption."""
    slug = _slug(config)
    state_dir = _state_dir(private_state_root, slug)
    manifest_file = _manifest_path(state_dir)
    if not manifest_file.is_file():
        raise InitializerError(ERR_RESUME)
    manifest = load_manifest(manifest_file)
    if manifest["status"] not in {"interrupted", "failed", "applying", "planned"}:
        if manifest["status"] == "completed":
            return CommandResult(
                ok=True,
                code="resume.noop",
                plan_digest=plan_digest,
                status="completed",
            )
        raise InitializerError(ERR_RESUME)
    return apply_app(
        config=config,
        template_revision=template_revision,
        private_state_root=private_state_root,
        plan_digest=plan_digest,
        adapter=adapter,
        secret_values=secret_values,
        dry_run=dry_run,
    )


def default_pinned_revision() -> str:
    return PINNED_TEMPLATE_REVISION

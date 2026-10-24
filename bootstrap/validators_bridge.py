"""Load and reuse APP-001/002/004 validators without treating metadata as live proof."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, MutableMapping, Optional

from bootstrap.errors import (
    ERR_CAPABILITY_METADATA,
    ERR_CONFIG,
    ERR_SECRETS,
    ERR_TF_GUARD,
    InitializerError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / "scripts"


def _load_script(name: str, filename: str) -> ModuleType:
    path = _SCRIPTS / filename
    mod_name = f"bootstrap_bridge_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise InitializerError(ERR_CONFIG)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def load_app_config_validator() -> ModuleType:
    return _load_script("validate_app_config", "validate-app-config.py")


def load_secrets_preflight() -> ModuleType:
    return _load_script("secrets_preflight", "secrets-preflight.py")


def load_plan_guard() -> ModuleType:
    return _load_script("validate_terraform_plan", "validate-terraform-plan.py")


def validate_app_config(config: Mapping[str, Any], *, mode: str) -> None:
    vac = load_app_config_validator()
    result = vac.validate_config(dict(config), mode=mode)
    if not result.ok:
        raise InitializerError(ERR_CONFIG)


def assert_metadata_not_live_proof(config: Mapping[str, Any]) -> None:
    """Refuse to treat externalVerification booleans/enums as live permission proof."""
    external = config.get("externalVerification")
    if not isinstance(external, Mapping):
        raise InitializerError(ERR_CONFIG)
    # Any caller claiming deploymentAuthorized via metadata is ignored; we never
    # promote metadata to proof. This function documents the hard rule.
    for key in (
        "accountPermissions",
        "sharedDatabaseGuard",
        "secretSource",
        "liveConflictDiscovery",
    ):
        if key not in external:
            raise InitializerError(ERR_CAPABILITY_METADATA)


def run_secrets_preflight(
    *,
    alias_map: Mapping[str, str],
    values: Mapping[str, str],
    app_id: str,
) -> Mapping[str, Any]:
    mod = load_secrets_preflight()
    source = mod.CallerProvidedEnvSource(values)
    report = mod.run_preflight(
        alias_map=dict(alias_map),
        source=source,
        context={"appId": app_id},
        enable_network_probe=False,
        probe_transport=None,
        browser_artifact_paths=[],
        browser_evidence_kind="skipped",
    )
    if not isinstance(report, dict):
        raise InitializerError(ERR_SECRETS)
    # Presence report must never authorize deployment.
    if report.get("deploymentAuthorized") is True:
        raise InitializerError(ERR_CAPABILITY_METADATA)
    if report.get("productionReady") is True:
        raise InitializerError(ERR_CAPABILITY_METADATA)
    return report


def run_terraform_plan_guard(
    *,
    plan_document: MutableMapping[str, Any],
    ownership: Mapping[str, Any],
) -> None:
    """Validate a reviewed Terraform JSON plan; never persist the raw plan here."""
    guard = load_plan_guard()
    result = guard.validate_plan(dict(plan_document), dict(ownership))
    if not getattr(result, "ok", False):
        raise InitializerError(ERR_TF_GUARD)


def load_json_object(path: Path) -> dict:
    import json

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        raise InitializerError(ERR_CONFIG) from None
    if not isinstance(data, dict):
        raise InitializerError(ERR_CONFIG)
    return data

"""Pure local planning — secret-free reviewed plan artifacts only."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from bootstrap import PINNED_TEMPLATE_REVISION
from bootstrap.errors import (
    ERR_DNS_CLOUDFRONT,
    ERR_DNS_UNRESOLVED,
    ERR_TEMPLATE_REV,
    ERR_TF_RAW_PERSIST,
    InitializerError,
)
from bootstrap.hashing import config_identity_hash, plan_digest


def require_pinned_revision(template_revision: str) -> str:
    rev = (template_revision or "").strip().lower()
    if rev != PINNED_TEMPLATE_REVISION:
        raise InitializerError(ERR_TEMPLATE_REV)
    return rev


def dns_choice_of(config: Mapping[str, Any]) -> str:
    dns = config.get("dns") if isinstance(config.get("dns"), Mapping) else {}
    choice = dns.get("choice") if isinstance(dns, Mapping) else None
    if not isinstance(choice, str):
        raise InitializerError(ERR_DNS_UNRESOLVED)
    return choice


def assert_apply_dns_gates(config: Mapping[str, Any]) -> str:
    choice = dns_choice_of(config)
    if choice == "unresolved":
        raise InitializerError(ERR_DNS_UNRESOLVED)
    if choice == "cloudfront":
        raise InitializerError(ERR_DNS_CLOUDFRONT)
    if choice != "cloudflare":
        raise InitializerError(ERR_DNS_UNRESOLVED)
    return choice


def refuse_raw_terraform_in_plan(plan_document: Mapping[str, Any]) -> None:
    forbidden_keys = {
        "resource_changes",
        "planned_values",
        "prior_state",
        "configuration",
        "tfplan",
        "rawTerraformPlan",
        "terraformPlan",
    }
    if any(key in plan_document for key in forbidden_keys):
        raise InitializerError(ERR_TF_RAW_PERSIST)
    nested = plan_document.get("artifacts")
    if isinstance(nested, Mapping) and any(k in nested for k in forbidden_keys):
        raise InitializerError(ERR_TF_RAW_PERSIST)


def build_plan_document(
    *,
    config: Mapping[str, Any],
    template_revision: str,
    output_root: str,
    repository_name: str,
    repository_owner: str,
    intended_stages: Sequence[str],
) -> dict:
    """Build a secret-free plan for human review. operationKind marks simulation."""
    revision = require_pinned_revision(template_revision)
    config_hash = config_identity_hash(config)
    app = config.get("app") if isinstance(config.get("app"), Mapping) else {}
    deployment = (
        config.get("deployment") if isinstance(config.get("deployment"), Mapping) else {}
    )
    database = config.get("database") if isinstance(config.get("database"), Mapping) else {}
    dns_choice = dns_choice_of(config)

    aliases = config.get("secretAliases") if isinstance(config.get("secretAliases"), Mapping) else {}
    alias_names = sorted(str(k) for k in aliases.keys())

    plan = {
        "schemaVersion": 1,
        "operationKind": "planning_simulation",
        "appSlug": app.get("slug"),
        "configHash": config_hash,
        "templateRevision": revision,
        "repository": {
            "owner": repository_owner,
            "name": repository_name,
        },
        "naming": {
            "hostname": config.get("hostname"),
            "deploymentIdentity": deployment.get("identity"),
            "stateKey": deployment.get("stateKey"),
            "databaseNamespace": database.get("namespace"),
            "databaseMode": database.get("mode"),
        },
        "dns": {
            "choice": dns_choice,
            "cloudfrontSupported": False,
        },
        "secretAliasNames": alias_names,
        "outputRoot": output_root,
        "intendedStages": list(intended_stages),
        "resourceIntents": [
            {
                "kind": "vercel_project",
                "identity": deployment.get("identity"),
                "action": "ensure",
                "adoptByNameForbidden": True,
            },
            {
                "kind": "infra_state",
                "stateKey": deployment.get("stateKey"),
                "action": "bind",
            },
            {
                "kind": "dns_records",
                "hostname": config.get("hostname"),
                "action": "ensure" if dns_choice == "cloudflare" else "skip",
            },
            {
                "kind": "shared_supabase_database",
                "mode": "existing-project-shared",
                "action": "reference_only",
                "createForbidden": True,
                "deleteForbidden": True,
            },
        ],
        "artifacts": {
            "persistRawTerraformPlan": False,
            "manifestSchema": "bootstrap-manifest.schema.json",
        },
        "gates": {
            "liveApplyIntegrated": False,
            "requiresExactPlanDigest": True,
            "requiresPinnedTemplateRevision": True,
            "requiresConfigHash": True,
        },
    }
    refuse_raw_terraform_in_plan(plan)
    plan["planDigest"] = plan_digest({k: v for k, v in plan.items() if k != "planDigest"})
    return plan


def digest_for_review(plan_document: Mapping[str, Any]) -> str:
    body = {k: v for k, v in plan_document.items() if k != "planDigest"}
    refuse_raw_terraform_in_plan(body)
    return plan_digest(body)

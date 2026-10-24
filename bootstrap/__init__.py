"""APP-005 app initializer core (plan / apply / resume).

Local planning and scaffold are separated from trusted remote execution.
The shipped default apply adapter does not perform live cloud mutations.
"""

from __future__ import annotations

__all__ = [
    "PINNED_TEMPLATE_REVISION",
    "EXTERNAL_STAGES",
    "LOCAL_STAGES",
    "ALL_STAGES",
]

# Documented pin from docs/bootstrap.md (APP-001 baseline commit).
PINNED_TEMPLATE_REVISION = "7f21f51209659a77fd56b19e6116f388e783d93d"

LOCAL_STAGES = (
    "validate_inputs",
    "materialize_scaffold",
    "write_plan_artifact",
)

EXTERNAL_STAGES = (
    "verify_account_identity",
    "verify_capabilities",
    "inventory_conflicts",
    "ensure_vercel_project",
    "ensure_dns_records",
    "bind_infra_state",
)

ALL_STAGES = LOCAL_STAGES + EXTERNAL_STAGES + ("finalize",)

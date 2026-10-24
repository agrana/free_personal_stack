"""Remote apply adapters.

Shipped default blocks live mutations honestly.
Unit tests inject RecordingApplyAdapter / InterruptibleApplyAdapter.
Network transport is injectable; defaults never pretend fake cloud ops are real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Set

from bootstrap.errors import (
    ERR_ADOPT_BY_NAME,
    ERR_AMBIGUOUS,
    ERR_COLLISION,
    ERR_LIVE_CLAIM,
    ERR_LIVE_MISSING,
    ERR_NO_AUTO_DELETE,
    InterruptStage,
    InitializerError,
)


@dataclass(frozen=True)
class AccountIdentity:
    provider: str
    account_id: str
    operation_kind: str  # provider_operation | planning_simulation


@dataclass(frozen=True)
class CapabilityReport:
    """Capabilities verified by the adapter itself — never caller metadata booleans."""

    verified: Mapping[str, bool]
    operation_kind: str


@dataclass(frozen=True)
class ConflictInventory:
    vercel_project_ids: Mapping[str, str]  # identity -> project id
    hostnames: Mapping[str, str]  # hostname -> record id or owner mark
    state_keys: Set[str]
    operation_kind: str


@dataclass
class ApplyContext:
    app_slug: str
    deployment_identity: str
    state_key: str
    hostname: str
    dns_choice: str
    namespace: str
    known_resource_ids: Dict[str, Any]
    secret_aliases_present: Mapping[str, bool]


class ApplyAdapter(Protocol):
    def verify_account_identity(self, ctx: ApplyContext) -> AccountIdentity:
        ...

    def verify_capabilities(self, ctx: ApplyContext) -> CapabilityReport:
        ...

    def inventory_conflicts(self, ctx: ApplyContext) -> ConflictInventory:
        ...

    def ensure_vercel_project(self, ctx: ApplyContext) -> str:
        """Return project id. Must look up by ownership id — never adopt by name alone."""
        ...

    def ensure_dns_records(self, ctx: ApplyContext) -> List[str]:
        ...

    def bind_infra_state(self, ctx: ApplyContext) -> str:
        ...


class BlockedLiveApplyAdapter:
    """Shipped default: refuse live provider mutations with an explicit gate."""

    def verify_account_identity(self, ctx: ApplyContext) -> AccountIdentity:
        raise InitializerError(ERR_LIVE_MISSING)

    def verify_capabilities(self, ctx: ApplyContext) -> CapabilityReport:
        raise InitializerError(ERR_LIVE_MISSING)

    def inventory_conflicts(self, ctx: ApplyContext) -> ConflictInventory:
        raise InitializerError(ERR_LIVE_MISSING)

    def ensure_vercel_project(self, ctx: ApplyContext) -> str:
        raise InitializerError(ERR_LIVE_MISSING)

    def ensure_dns_records(self, ctx: ApplyContext) -> List[str]:
        raise InitializerError(ERR_LIVE_MISSING)

    def bind_infra_state(self, ctx: ApplyContext) -> str:
        raise InitializerError(ERR_LIVE_MISSING)


@dataclass
class RecordingApplyAdapter:
    """Test double that performs in-memory provider_operation semantics."""

    account_id: str = "acct_fixture_example"
    capabilities: Mapping[str, bool] = field(
        default_factory=lambda: {
            "vercel.project.write": True,
            "dns.record.write": True,
            "supabase.shared.reference": True,
        }
    )
    existing_projects_by_id: Dict[str, str] = field(default_factory=dict)
    existing_projects_by_name: Dict[str, str] = field(default_factory=dict)
    existing_dns: Dict[str, str] = field(default_factory=dict)
    existing_state_keys: Set[str] = field(default_factory=set)
    created_project_id: str = "prj_fixture_new_001"
    created_dns_ids: List[str] = field(default_factory=lambda: ["dns_fixture_001"])
    interrupt_before: Optional[str] = None
    calls: List[str] = field(default_factory=list)
    delete_attempts: int = 0
    allow_delete: bool = False

    def _maybe_interrupt(self, stage: str) -> None:
        self.calls.append(stage)
        if self.interrupt_before == stage:
            raise InterruptStage("stage.interrupted")

    def verify_account_identity(self, ctx: ApplyContext) -> AccountIdentity:
        self._maybe_interrupt("verify_account_identity")
        return AccountIdentity(
            provider="vercel",
            account_id=self.account_id,
            operation_kind="provider_operation",
        )

    def verify_capabilities(self, ctx: ApplyContext) -> CapabilityReport:
        self._maybe_interrupt("verify_capabilities")
        # Ignore any caller-supplied metadata; only adapter-held facts count.
        return CapabilityReport(
            verified=dict(self.capabilities),
            operation_kind="provider_operation",
        )

    def inventory_conflicts(self, ctx: ApplyContext) -> ConflictInventory:
        self._maybe_interrupt("inventory_conflicts")
        return ConflictInventory(
            vercel_project_ids=dict(self.existing_projects_by_id),
            hostnames=dict(self.existing_dns),
            state_keys=set(self.existing_state_keys),
            operation_kind="provider_operation",
        )

    def ensure_vercel_project(self, ctx: ApplyContext) -> str:
        self._maybe_interrupt("ensure_vercel_project")
        known = ctx.known_resource_ids.get("vercelProjectId")
        if known:
            owner = self.existing_projects_by_id.get(str(known))
            if owner is None:
                # Reconcile after crash: id claimed locally but missing remotely —
                # refuse ambiguous adoption.
                raise InitializerError(ERR_AMBIGUOUS)
            if owner != ctx.deployment_identity:
                raise InitializerError(ERR_COLLISION)
            return str(known)

        by_name = self.existing_projects_by_name.get(ctx.deployment_identity)
        if by_name:
            # Name collision without ownership id in manifest → refuse (no adopt-by-name).
            raise InitializerError(ERR_ADOPT_BY_NAME)

        # Create
        self.existing_projects_by_id[self.created_project_id] = ctx.deployment_identity
        self.existing_projects_by_name[ctx.deployment_identity] = self.created_project_id
        return self.created_project_id

    def ensure_dns_records(self, ctx: ApplyContext) -> List[str]:
        self._maybe_interrupt("ensure_dns_records")
        if ctx.dns_choice != "cloudflare":
            return []
        existing = self.existing_dns.get(ctx.hostname)
        known_ids = list(ctx.known_resource_ids.get("dnsRecordIds") or [])
        if existing and not known_ids:
            raise InitializerError(ERR_COLLISION)
        if known_ids:
            for kid in known_ids:
                if self.existing_dns.get(ctx.hostname) not in (None, kid) and kid not in self.existing_dns.values():
                    raise InitializerError(ERR_AMBIGUOUS)
            return list(known_ids)
        for dns_id in self.created_dns_ids:
            self.existing_dns[ctx.hostname] = dns_id
        return list(self.created_dns_ids)

    def bind_infra_state(self, ctx: ApplyContext) -> str:
        self._maybe_interrupt("bind_infra_state")
        if ctx.state_key in self.existing_state_keys and ctx.known_resource_ids.get("infraStateKey") != ctx.state_key:
            raise InitializerError(ERR_COLLISION)
        self.existing_state_keys.add(ctx.state_key)
        return ctx.state_key

    def attempt_delete_on_failure(self) -> None:
        self.delete_attempts += 1
        if not self.allow_delete:
            raise InitializerError(ERR_NO_AUTO_DELETE)


class ClaimingLiveAdapter(BlockedLiveApplyAdapter):
    """Hostile test double that falsely claims live success — engine must reject."""

    def verify_account_identity(self, ctx: ApplyContext) -> AccountIdentity:
        return AccountIdentity(
            provider="vercel",
            account_id="acct_fake",
            operation_kind="planning_simulation",
        )

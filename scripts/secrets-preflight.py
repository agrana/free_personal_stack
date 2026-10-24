#!/usr/bin/env python3
"""APP-004 secure credential preflight and binding (presence + capability only).

Resolves allowlisted alias NAMES from a caller-provided secure source mapping.
Values remain in process memory only. Reports and console output contain
presence and verified/unverified/blocked capabilities — never secret values,
raw request URLs, response bodies, headers, or environment dumps.

Network probes are opt-in, fixed-host allowlist only, TLS required, redirects
rejected, schema-validated responses. Without an authorized supervisor probe,
production capabilities remain explicitly unverified.

Does not create Vercel/Supabase projects, persist credentials, or overwrite
shared SavedTube secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = (REPO_ROOT / "tests" / "fixtures").resolve()

ALIAS_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
APP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SHELL_METACHAR_RE = re.compile(r"""[;|&$`<>(){}[\]\\"'\n\r\t]""")
URL_USERINFO_RE = re.compile(r"://[^/\s]+:[^/@\s]+@")
BEARER_RE = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+")
JWT_LIKE_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key)\s*=\s*\S+"
)
SK_LIKE_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}")
VCT_LIKE_RE = re.compile(r"\bvct_[A-Za-z0-9_\-]{8,}")

# Minimum allowlisted aliases resolved by this preflight (schema + APP-004).
ALLOWLISTED_ALIASES = frozenset(
    {
        "CLOUDFLARE_API_TOKEN",
        "VERCEL_API_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ACCESS_TOKEN",
        "TERRAFORM_STATE_PASSWORD",
        "SUPABASE_PROJECT_ID",
        "SUPABASE_DATABASE_PASSWORD",
        "DOMAIN_NAME",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SUPPORT_EMAIL_DESTINATION",
        "CONTACT_EMAIL_DESTINATION",
        "VERCEL_TEAM_ID",
        "CLOUDFLARE_ACCOUNT_ID",
        "VERCEL_PROJECT_ID",
        "SAVEDTUBE_VERCEL_PROJECT_ID",
        "OPENAI_API_KEY",
        "NEXTAUTH_SECRET",
        "NEXTAUTH_URL",
    }
)

REQUIRED_PRESENCE_ALIASES = frozenset(
    {
        "CLOUDFLARE_API_TOKEN",
        "VERCEL_API_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ACCESS_TOKEN",
        "TERRAFORM_STATE_PASSWORD",
        "SUPABASE_PROJECT_ID",
        "VERCEL_PROJECT_ID",
        "SAVEDTUBE_VERCEL_PROJECT_ID",
    }
)

# Optional: presence reported when declared; absence does not block local metadata.
OPTIONAL_PRESENCE_ALIASES = frozenset(
    {
        "OPENAI_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "NEXTAUTH_SECRET",
        "NEXTAUTH_URL",
        "DOMAIN_NAME",
        "VERCEL_TEAM_ID",
        "CLOUDFLARE_ACCOUNT_ID",
        "SUPPORT_EMAIL_DESTINATION",
        "CONTACT_EMAIL_DESTINATION",
        "SUPABASE_DATABASE_PASSWORD",
    }
)

PUBLIC_CONFIG_ALLOWLIST = frozenset(
    {
        "NEXT_PUBLIC_SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_SITE_URL",
    }
)

SERVER_ONLY_ALIASES = frozenset(
    {
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_DATABASE_PASSWORD",
        "SUPABASE_ACCESS_TOKEN",
        "VERCEL_API_TOKEN",
        "CLOUDFLARE_API_TOKEN",
        "TERRAFORM_STATE_PASSWORD",
        "GOOGLE_CLIENT_SECRET",
        "NEXTAUTH_SECRET",
        "OPENAI_API_KEY",
    }
)

KNOWN_PERMISSION_CLAIMS = frozenset(
    {
        "vercel.token.read",
        "vercel.token.write",
        "supabase.config.read",
        "openai.api",
    }
)

PROBE_HOST_ALLOWLIST = frozenset(
    {
        "api.vercel.com",
        "api.supabase.com",
        "api.openai.com",
    }
)

PROBE_TIMEOUT_SECONDS = 5
BROWSER_SCAN_CHUNK_SIZE = 65536
BROWSER_SCAN_OVERLAP = 8192
BROWSER_SCAN_MAX_BYTES = 32 * 1024 * 1024

VERCEL_USER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "string"},
        "teams": {"type": "array"},
    },
}

_REDACT_PATTERNS = (
    URL_USERINFO_RE,
    BEARER_RE,
    JWT_LIKE_RE,
    ASSIGNMENT_SECRET_RE,
    SK_LIKE_RE,
    VCT_LIKE_RE,
    re.compile(r"postgresql://[^\s]+", re.IGNORECASE),
    re.compile(r"postgres://[^\s]+", re.IGNORECASE),
    re.compile(r"https?://[^\s]+", re.IGNORECASE),
)

# Fixed public error codes only — never interpolate exception text or values.
ERR_INPUT_TYPE = "input.invalid_type"
ERR_INPUT_JSON = "input.invalid_json"
ERR_INPUT_SHAPE = "input.invalid_shape"
ERR_ALIAS_TOKEN = "alias.invalid_token"
ERR_ALIAS_UNKNOWN = "alias.unknown"
ERR_ALIAS_REMAP = "alias.remapping_prohibited"
ERR_ALIAS_MISSING = "alias.missing_required"
ERR_ALIAS_BLOCKED = "alias.resolution_blocked"
ERR_URL_USERINFO = "supabase_url.embedded_userinfo_rejected"
ERR_PUBLIC_ALLOWLIST = "public.config.allowlist_violation"
ERR_PUBLIC_EXFIL = "public.config.server_only_value_exfiltration"
ERR_PERMISSIONS = "permissions.unknown_claim"
ERR_PERMISSIONS_SHAPE = "permissions.invalid_claims_shape"
ERR_ACCOUNT_SHAPE = "account.alignment.invalid_shape"
ERR_ACCOUNT_MISMATCH = "account.alignment.mismatch"
ERR_IDENTITY_UNBOUND = "identity.vercel.arena_unbound"
ERR_IDENTITY_MISSING_SAVED = "identity.vercel.savedtube_missing"
ERR_IDENTITY_COLLISION = "identity.vercel.collision"
ERR_PROBE_NO_TRANSPORT = "probe.transport_missing"
ERR_PROBE_TOKEN_MISSING = "probe.token_missing"
ERR_PROBE_INVALID = "probe.response_invalid"
ERR_PROBE_TRANSPORT = "probe.transport_error"
ERR_PROBE_HOST = "probe.host_not_allowlisted"
ERR_PROBE_PATH = "probe.path_invalid"
ERR_PROBE_METHOD = "probe.method_not_allowed"
ERR_PROBE_REDIRECT = "probe.redirect_rejected"
ERR_BROWSER_MISSING = "browserScan.artifact_missing"
ERR_BROWSER_EMPTY = "browserScan.artifact_empty"
ERR_BROWSER_UNREADABLE = "browserScan.artifact_unreadable"
ERR_BROWSER_OVERSIZE = "browserScan.artifact_oversize"
ERR_BROWSER_LEAK = "browserScan.server_only_material_detected"
ERR_VALUES_SOURCE = "values.source_not_allowed"
ERR_APP_ID = "app_id.invalid"


class PreflightError(RuntimeError):
    pass


class UnknownAliasError(PreflightError):
    pass


class SourceValidationError(PreflightError):
    pass


class ProbeError(PreflightError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProbeTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        host: str,
        path: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        ...


def redact_text(message: str) -> str:
    """Strip credential-like fragments from text destined for reports/console."""
    redacted = str(message)
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    if len(redacted) > 240:
        redacted = redacted[:240] + "…"
    return redacted


def _safe_error(code: str) -> str:
    """Public errors are fixed codes only (no dynamic exception text)."""
    text = str(code).strip()
    if not text or any(ch in text for ch in "\n\r\t") or len(text) > 120:
        return "error.unspecified"
    return text


class CallerProvidedEnvSource:
    """Trusted-launcher source: resolve only allowlisted aliases from a mapping.

    Disallows arbitrary shell execution and arbitrary env-file probing. Values
    stay in process memory; this object never writes them to disk or stdout.
    """

    def __init__(self, mapping: Mapping[str, str]) -> None:
        if not isinstance(mapping, Mapping):
            raise SourceValidationError(ERR_INPUT_TYPE)
        cleaned: Dict[str, str] = {}
        for key, value in mapping.items():
            if not isinstance(key, str):
                raise SourceValidationError(ERR_INPUT_TYPE)
            if value is None:
                continue
            if not isinstance(value, str):
                raise SourceValidationError(ERR_INPUT_TYPE)
            cleaned[key] = value
        self._mapping = cleaned

    def resolve(self, alias: str) -> Optional[str]:
        if alias not in ALLOWLISTED_ALIASES and alias not in PUBLIC_CONFIG_ALLOWLIST:
            raise UnknownAliasError(ERR_ALIAS_UNKNOWN)
        if alias not in self._mapping:
            return None
        value = self._mapping[alias]
        text = value.strip()
        return text if text else None

    def public_keys_present(self) -> List[str]:
        keys = [k for k in self._mapping if isinstance(k, str) and k.startswith("NEXT_PUBLIC_")]
        return sorted(keys)

    def known_server_only_values(self) -> List[str]:
        values: List[str] = []
        for alias in sorted(SERVER_ONLY_ALIASES):
            resolved = self.resolve(alias)
            if resolved:
                values.append(resolved)
        return values


@dataclass
class AliasPresence:
    alias: str
    present: bool
    resolution: str  # ok | blocked | skipped
    serverOnly: bool
    browserAllowed: bool

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "alias": self.alias,
            "present": self.present,
            "resolution": self.resolution,
            "serverOnly": self.serverOnly,
            "browserAllowed": self.browserAllowed,
        }


@dataclass
class Capability:
    name: str
    status: str  # verified | unverified | blocked | present
    reason: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        out = {"name": self.name, "status": self.status}
        if self.reason:
            out["reason"] = _safe_error(self.reason)
        return out


@dataclass
class PreflightReport:
    ok: bool
    local_metadata_ok: bool = False
    production_ready: bool = False
    deployment_authorized: bool = False
    aliases: List[AliasPresence] = field(default_factory=list)
    capabilities: List[Capability] = field(default_factory=list)
    identity: Dict[str, Any] = field(default_factory=dict)
    binding_plan: Dict[str, Any] = field(default_factory=dict)
    preview: Dict[str, Any] = field(default_factory=dict)
    fork: Dict[str, Any] = field(default_factory=dict)
    browserScan: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "localMetadataOk": self.local_metadata_ok,
            "productionReady": False,
            "deploymentAuthorized": False,
            "aliases": [a.to_public_dict() for a in self.aliases],
            "capabilities": [c.to_public_dict() for c in self.capabilities],
            "identity": dict(self.identity),
            "bindingPlan": self.binding_plan,
            "preview": dict(self.preview),
            "fork": dict(self.fork),
            "browserScan": dict(self.browserScan),
            "errors": [_safe_error(e) for e in self.errors],
            "productionCredentials": "unverified",
            "note": (
                "Local metadata/presence checks only. productionReady and "
                "deploymentAuthorized remain false until authorized live "
                "supervisor verification exists. Unverified capabilities never "
                "authorize deployment. GitHub secret-list access is not evidence "
                "of secret-value access."
            ),
        }


def _validate_app_id(app_id: str) -> str:
    if not isinstance(app_id, str) or not APP_ID_RE.fullmatch(app_id):
        raise PreflightError(ERR_APP_ID)
    if SHELL_METACHAR_RE.search(app_id) or CONTROL_CHAR_RE.search(app_id):
        raise PreflightError(ERR_APP_ID)
    return app_id


def build_binding_plan(
    alias_map: Mapping[str, str],
    *,
    app_id: str = "app",
) -> Dict[str, Any]:
    """Reusable binding plan: alias names and targets only (no values).

    Invalid / malicious alias tokens are omitted from the plan artifact so
    reports never echo attacker-controlled strings. Remapping is prohibited:
    slot name must equal alias name.
    """
    validated_app = _validate_app_id(app_id)
    target = f"{validated_app}-process"
    bindings: List[Dict[str, Any]] = []
    # String keys only — avoid mixed-type sorted() crashes.
    slots = sorted(k for k in alias_map.keys() if isinstance(k, str))
    for slot in slots:
        alias = alias_map[slot]
        if not isinstance(slot, str) or not ALIAS_NAME_RE.fullmatch(slot):
            continue
        if slot not in ALLOWLISTED_ALIASES:
            continue
        if not isinstance(alias, str) or not ALIAS_NAME_RE.fullmatch(alias):
            continue
        if alias != slot:
            continue
        if SHELL_METACHAR_RE.search(alias) or CONTROL_CHAR_RE.search(alias):
            continue
        reuse = slot not in {"VERCEL_PROJECT_ID"}
        source = (
            f"{validated_app}-new-project"
            if slot == "VERCEL_PROJECT_ID"
            else "shared-savedtube-secure-store"
        )
        status = "unbound" if slot == "VERCEL_PROJECT_ID" else "bind-from-shared"
        if slot == "SAVEDTUBE_VERCEL_PROJECT_ID":
            source = "shared-savedtube-identity"
            status = "reference-only"
        bindings.append(
            {
                "slot": slot,
                "alias": alias,
                "source": source,
                "target": target,
                "reuse": reuse,
                "status": status,
                "persist": False,
            }
        )
    return {
        "schemaVersion": 1,
        "appId": validated_app,
        "description": (
            "Alias names and targets only. Remapping prohibited (slot==alias). "
            "Do not export plaintext env files from this plan. Do not overwrite "
            "shared SavedTube secrets."
        ),
        "publicConfigAllowlist": sorted(PUBLIC_CONFIG_ALLOWLIST),
        "serverOnly": sorted(SERVER_ONLY_ALIASES),
        "optionalAliases": sorted(OPTIONAL_PRESENCE_ALIASES),
        "previewDefaults": {
            "injectProductionSecrets": False,
            "credentials": "disabled",
        },
        "forkPolicy": {"injectProductionSecrets": False},
        "bindings": bindings,
    }


def _validate_alias_token(token: Any, errors: List[str]) -> bool:
    if not isinstance(token, str) or not ALIAS_NAME_RE.fullmatch(token):
        errors.append(_safe_error(ERR_ALIAS_TOKEN))
        return False
    if SHELL_METACHAR_RE.search(token) or CONTROL_CHAR_RE.search(token):
        errors.append(_safe_error(ERR_ALIAS_TOKEN))
        return False
    return True


def _value_has_embedded_userinfo(value: str) -> bool:
    return bool(URL_USERINFO_RE.search(value))


def probe_allowlisted(
    *,
    transport: ProbeTransport,
    host: str,
    path: str,
    method: str = "GET",
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    if host not in PROBE_HOST_ALLOWLIST:
        raise ProbeError(ERR_PROBE_HOST)
    if not path.startswith("/"):
        raise ProbeError(ERR_PROBE_PATH)
    if method.upper() not in {"GET", "HEAD"}:
        raise ProbeError(ERR_PROBE_METHOD)
    response = transport.request(
        method=method.upper(),
        host=host,
        path=path,
        headers=dict(headers or {}),
        timeout=timeout,
    )
    status = int(response.get("status", 0))
    if status in {301, 302, 303, 307, 308}:
        raise ProbeError(ERR_PROBE_REDIRECT)
    return response


def _validate_response_schema(payload: Any, schema: Mapping[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in schema.get("required", []):
        if key not in payload:
            return False
    props = schema.get("properties", {})
    for key, rules in props.items():
        if key not in payload:
            continue
        expected = rules.get("type")
        value = payload[key]
        if expected == "string" and not isinstance(value, str):
            return False
        if expected == "array" and not isinstance(value, list):
            return False
        if expected == "object" and not isinstance(value, dict):
            return False
    return True


def _check_vercel_identity(
    source: CallerProvidedEnvSource, errors: List[str]
) -> Dict[str, Any]:
    savedtube = source.resolve("SAVEDTUBE_VERCEL_PROJECT_ID")
    arena = source.resolve("VERCEL_PROJECT_ID")
    if not arena:
        errors.append(_safe_error(ERR_IDENTITY_UNBOUND))
        return {
            "vercelProjectDistinct": "blocked",
            "reason": ERR_IDENTITY_UNBOUND,
        }
    if not savedtube:
        errors.append(_safe_error(ERR_IDENTITY_MISSING_SAVED))
        return {
            "vercelProjectDistinct": "blocked",
            "reason": ERR_IDENTITY_MISSING_SAVED,
        }
    if arena == savedtube:
        errors.append(_safe_error(ERR_IDENTITY_COLLISION))
        return {
            "vercelProjectDistinct": "blocked",
            "reason": ERR_IDENTITY_COLLISION,
        }
    return {
        "vercelProjectDistinct": "present-distinct",
        "reason": "local_distinctness_only_live_unverified",
    }


def _iter_file_windows(
    path: Path, *, chunk_size: int, overlap: int, max_bytes: int
) -> Iterable[bytes]:
    total = 0
    carry = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                if carry:
                    yield carry
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError(ERR_BROWSER_OVERSIZE)
            window = carry + chunk
            yield window
            if len(window) > overlap:
                carry = window[-overlap:]
            else:
                carry = window


def _scan_browser_artifacts(
    paths: Sequence[str],
    *,
    source: CallerProvidedEnvSource,
    evidence_kind: str,
    errors: List[str],
) -> Dict[str, Any]:
    kind = (
        evidence_kind
        if evidence_kind in {"static-fixture", "built-bundle"}
        else "static-fixture"
    )
    findings: List[Dict[str, str]] = []
    scanned = 0
    if not paths:
        return {
            "evidenceKind": kind,
            "claimsFullProductionBuild": False,
            "scanScope": "supplied-artifacts-only",
            "scannedArtifacts": 0,
            "status": "unverified",
            "findings": [],
            "note": (
                "No artifact paths supplied; browser exclusion remains unverified. "
                "Caller evidenceKind is a label only and never proves a full "
                "production build."
            ),
        }

    secrets: List[Tuple[str, str]] = []
    for alias in sorted(SERVER_ONLY_ALIASES):
        value = source.resolve(alias)
        if value:
            secrets.append((alias, value))

    for _raw in paths:
        path = Path(_raw)
        if not path.is_file():
            errors.append(_safe_error(ERR_BROWSER_MISSING))
            continue
        try:
            size = path.stat().st_size
        except OSError:
            errors.append(_safe_error(ERR_BROWSER_UNREADABLE))
            continue
        if size == 0:
            errors.append(_safe_error(ERR_BROWSER_EMPTY))
            continue
        if size > BROWSER_SCAN_MAX_BYTES:
            errors.append(_safe_error(ERR_BROWSER_OVERSIZE))
            continue
        try:
            hit_aliases = set()
            for window in _iter_file_windows(
                path,
                chunk_size=BROWSER_SCAN_CHUNK_SIZE,
                overlap=BROWSER_SCAN_OVERLAP,
                max_bytes=BROWSER_SCAN_MAX_BYTES,
            ):
                # Decode per window; secrets compared as utf-8 bytes for boundary safety.
                for alias, value in secrets:
                    if alias in hit_aliases:
                        continue
                    needle = value.encode("utf-8", errors="ignore")
                    if needle and needle in window:
                        hit_aliases.add(alias)
                        findings.append(
                            {
                                "alias": alias,
                                "severity": "server-only-secret-excluded-from-browser",
                            }
                        )
            scanned += 1
        except OSError:
            errors.append(_safe_error(ERR_BROWSER_UNREADABLE))
            continue

    if findings:
        errors.append(_safe_error(ERR_BROWSER_LEAK))
        status = "blocked"
    elif scanned == len(paths) and scanned > 0:
        status = "ok"
    else:
        status = "blocked"

    return {
        "evidenceKind": kind,
        "claimsFullProductionBuild": False,
        "scanScope": "supplied-artifacts-only",
        "scannedArtifacts": scanned,
        "status": status,
        "findings": findings,
        "note": (
            "Scan covers supplied artifact bytes only (streaming with overlap). "
            "evidenceKind is a caller label; it does not claim a complete "
            "production build."
        ),
    }


def _validate_alias_map(alias_map: Mapping[Any, Any], errors: List[str]) -> Dict[str, str]:
    if not isinstance(alias_map, Mapping):
        errors.append(_safe_error(ERR_INPUT_SHAPE))
        return {}
    cleaned: Dict[str, str] = {}
    for slot, alias in alias_map.items():
        if not isinstance(slot, str):
            errors.append(_safe_error(ERR_ALIAS_TOKEN))
            continue
        slot_ok = _validate_alias_token(slot, errors)
        alias_ok = _validate_alias_token(alias, errors)
        if not slot_ok or not alias_ok:
            continue
        if slot not in ALLOWLISTED_ALIASES:
            errors.append(_safe_error(ERR_ALIAS_UNKNOWN))
            continue
        if alias != slot:
            # Explicit prohibition: remapping is not implemented.
            errors.append(_safe_error(ERR_ALIAS_REMAP))
            continue
        cleaned[slot] = alias
    return cleaned


def _validate_context(ctx: Mapping[str, Any], errors: List[str]) -> Dict[str, Any]:
    if not isinstance(ctx, Mapping):
        errors.append(_safe_error(ERR_INPUT_SHAPE))
        return {}
    out = dict(ctx)

    claimed = out.get("claimedPermissions")
    if claimed is not None and not isinstance(claimed, list):
        errors.append(_safe_error(ERR_PERMISSIONS_SHAPE))
        out["claimedPermissions"] = None
    elif isinstance(claimed, list):
        for claim in claimed:
            if not isinstance(claim, str):
                errors.append(_safe_error(ERR_PERMISSIONS_SHAPE))
                out["claimedPermissions"] = None
                break

    for key in ("expectedAccountFingerprints", "observedAccountFingerprints"):
        value = out.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            errors.append(_safe_error(ERR_ACCOUNT_SHAPE))
            out[key] = None
            continue
        for fkey, fval in value.items():
            if not isinstance(fkey, str) or not isinstance(fval, str):
                errors.append(_safe_error(ERR_ACCOUNT_SHAPE))
                out[key] = None
                break
    return out


def run_preflight(
    *,
    alias_map: Mapping[str, str],
    source: CallerProvidedEnvSource,
    context: Optional[Mapping[str, Any]] = None,
    enable_network_probe: bool = False,
    probe_transport: Optional[ProbeTransport] = None,
    browser_artifact_paths: Optional[Sequence[str]] = None,
    browser_evidence_kind: str = "static-fixture",
    app_id: str = "app",
) -> PreflightReport:
    errors: List[str] = []
    aliases_out: List[AliasPresence] = []
    capabilities: List[Capability] = []

    try:
        validated_app = _validate_app_id(app_id)
    except PreflightError:
        validated_app = "app"
        errors.append(_safe_error(ERR_APP_ID))

    cleaned_aliases = _validate_alias_map(alias_map, errors)
    ctx = _validate_context(dict(context or {}), errors)

    declared = set(cleaned_aliases.keys()) | set(REQUIRED_PRESENCE_ALIASES)
    # String-only sort
    for slot in sorted(s for s in declared if isinstance(s, str)):
        if slot not in ALLOWLISTED_ALIASES:
            continue
        try:
            value = source.resolve(slot)
        except UnknownAliasError:
            errors.append(_safe_error(ERR_ALIAS_BLOCKED))
            aliases_out.append(
                AliasPresence(
                    alias=slot,
                    present=False,
                    resolution="blocked",
                    serverOnly=slot in SERVER_ONLY_ALIASES,
                    browserAllowed=False,
                )
            )
            continue
        present = value is not None
        resolution = "ok"
        if slot in REQUIRED_PRESENCE_ALIASES and not present:
            resolution = "blocked"
            errors.append(_safe_error(ERR_ALIAS_MISSING))
        elif present and slot == "SUPABASE_URL" and _value_has_embedded_userinfo(value or ""):
            present = False
            resolution = "blocked"
            errors.append(_safe_error(ERR_URL_USERINFO))
        elif slot in OPTIONAL_PRESENCE_ALIASES and not present:
            resolution = "skipped"
        aliases_out.append(
            AliasPresence(
                alias=slot,
                present=present,
                resolution=resolution,
                serverOnly=slot in SERVER_ONLY_ALIASES,
                browserAllowed=slot not in SERVER_ONLY_ALIASES,
            )
        )

    # Public NEXT_PUBLIC: key allowlist + value must not equal any server-only secret.
    public_blocked = False
    for key in source.public_keys_present():
        if key not in PUBLIC_CONFIG_ALLOWLIST:
            errors.append(_safe_error(ERR_PUBLIC_ALLOWLIST))
            capabilities.append(
                Capability(
                    name="public.config.allowlist",
                    status="blocked",
                    reason=ERR_PUBLIC_ALLOWLIST,
                )
            )
            public_blocked = True
            break
    if not public_blocked:
        capabilities.append(
            Capability(
                name="public.config.allowlist",
                status="present",
                reason="strict_allowlist_enforced_locally",
            )
        )

    server_values = source.known_server_only_values()
    exfil = False
    for key in source.public_keys_present():
        if key not in PUBLIC_CONFIG_ALLOWLIST:
            continue
        pub_val = source.resolve(key)
        if not pub_val:
            continue
        for secret_val in server_values:
            if pub_val == secret_val:
                exfil = True
                break
        if exfil:
            break
    if exfil:
        errors.append(_safe_error(ERR_PUBLIC_EXFIL))
        capabilities.append(
            Capability(
                name="public.config.secret_exfiltration",
                status="blocked",
                reason=ERR_PUBLIC_EXFIL,
            )
        )

    identity = _check_vercel_identity(source, errors)

    capabilities.append(
        Capability(
            name="production.credentials",
            status="unverified",
            reason="supervisor_authorized_preflight_not_executed",
        )
    )

    openai_present = source.resolve("OPENAI_API_KEY") is not None
    capabilities.append(
        Capability(
            name="openai.api",
            status="present" if openai_present else "blocked",
            reason="api_key_presence_only_not_entitlement",
        )
    )
    capabilities.append(
        Capability(
            name="openai.codex",
            status="unverified",
            reason="openai_api_key_does_not_prove_codex",
        )
    )
    capabilities.append(
        Capability(
            name="openai.decisions",
            status="unverified",
            reason="openai_api_key_does_not_prove_decisions",
        )
    )

    claimed = ctx.get("claimedPermissions")
    if isinstance(claimed, list):
        for claim in claimed:
            if claim not in KNOWN_PERMISSION_CLAIMS:
                errors.append(_safe_error(ERR_PERMISSIONS))
                capabilities.append(
                    Capability(
                        name="permissions.claims",
                        status="blocked",
                        reason=ERR_PERMISSIONS,
                    )
                )
                break

    expected = ctx.get("expectedAccountFingerprints")
    observed = ctx.get("observedAccountFingerprints")
    if expected or observed:
        if not isinstance(expected, dict) or not isinstance(observed, dict):
            errors.append(_safe_error(ERR_ACCOUNT_SHAPE))
            capabilities.append(
                Capability(
                    name="account.alignment",
                    status="blocked",
                    reason=ERR_ACCOUNT_SHAPE,
                )
            )
        else:
            mismatched = False
            for key, exp in expected.items():
                if key not in observed or observed.get(key) != exp:
                    mismatched = True
                    break
            if mismatched:
                errors.append(_safe_error(ERR_ACCOUNT_MISMATCH))
                capabilities.append(
                    Capability(
                        name="account.alignment",
                        status="blocked",
                        reason=ERR_ACCOUNT_MISMATCH,
                    )
                )
            else:
                capabilities.append(
                    Capability(
                        name="account.alignment",
                        status="present",
                        reason="caller_fingerprints_match_not_live_proof",
                    )
                )

    is_preview = str(ctx.get("deploymentTarget", "")).lower() == "preview"
    is_fork = bool(ctx.get("isForkPullRequest"))
    preview = {"injectProductionSecrets": False, "credentials": "disabled"}
    fork = {"injectProductionSecrets": False}
    if is_preview:
        capabilities.append(
            Capability(
                name="preview.production_secrets",
                status="blocked",
                reason="preview_defaults_disable_production_secrets",
            )
        )
    if is_fork:
        capabilities.append(
            Capability(
                name="fork.production_secrets",
                status="blocked",
                reason="fork_workflows_never_inject_production_secrets",
            )
        )

    read_status = "unverified"
    write_status = "unverified"
    if enable_network_probe:
        if probe_transport is None:
            errors.append(_safe_error(ERR_PROBE_NO_TRANSPORT))
            read_status = "blocked"
        else:
            token = source.resolve("VERCEL_API_TOKEN")
            if not token:
                read_status = "blocked"
                errors.append(_safe_error(ERR_PROBE_TOKEN_MISSING))
            else:
                try:
                    response = probe_allowlisted(
                        transport=probe_transport,
                        host="api.vercel.com",
                        path="/v2/user",
                        method="GET",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    status = int(response.get("status", 0))
                    payload = response.get("json")
                    if status == 200 and _validate_response_schema(
                        payload, VERCEL_USER_RESPONSE_SCHEMA
                    ):
                        read_status = "verified"
                    else:
                        read_status = "blocked"
                        errors.append(_safe_error(ERR_PROBE_INVALID))
                    write_status = "unverified"
                except ProbeError as exc:
                    read_status = "blocked"
                    errors.append(_safe_error(exc.code))
                except Exception:  # noqa: BLE001 — fixed code only; never embed exc
                    read_status = "blocked"
                    errors.append(_safe_error(ERR_PROBE_TRANSPORT))
    capabilities.append(
        Capability(
            name="vercel.token.read",
            status=read_status,
            reason="read_capability_independent_of_write",
        )
    )
    capabilities.append(
        Capability(
            name="vercel.token.write",
            status=write_status,
            reason="write_never_inferred_from_get",
        )
    )

    browser_scan = _scan_browser_artifacts(
        browser_artifact_paths or [],
        source=source,
        evidence_kind=browser_evidence_kind,
        errors=errors,
    )

    binding_plan = build_binding_plan(cleaned_aliases or alias_map, app_id=validated_app)

    local_ok = (
        not errors
        and identity.get("vercelProjectDistinct") == "present-distinct"
        and browser_scan.get("status") != "blocked"
    )
    if browser_scan.get("status") == "blocked":
        local_ok = False
    if any(
        c.status == "blocked" and c.name.endswith("production_secrets")
        for c in capabilities
    ):
        if is_preview or is_fork:
            local_ok = False
    if any(
        c.status == "blocked"
        for c in capabilities
        if c.name
        in {
            "public.config.allowlist",
            "public.config.secret_exfiltration",
            "account.alignment",
            "permissions.claims",
            "vercel.token.read",
        }
    ):
        local_ok = False
    if errors:
        local_ok = False

    # Unverified capabilities never authorize deployment.
    production_ready = False
    deployment_authorized = False

    return PreflightReport(
        ok=local_ok,
        local_metadata_ok=local_ok,
        production_ready=production_ready,
        deployment_authorized=deployment_authorized,
        aliases=aliases_out,
        capabilities=capabilities,
        identity=identity,
        binding_plan=binding_plan,
        preview=preview,
        fork=fork,
        browserScan=browser_scan,
        errors=[_safe_error(e) for e in errors],
    )


def _path_is_under_fixtures(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return FIXTURES_ROOT in resolved.parents or resolved == FIXTURES_ROOT
    except OSError:
        return False


def _load_json_object(raw: str, *, label: str) -> Dict[str, Any]:
    del label  # label unused; keep signature stable for callers
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightError(ERR_INPUT_JSON) from exc
    if not isinstance(payload, dict):
        raise PreflightError(ERR_INPUT_SHAPE)
    return payload


def _load_values_from_env(alias_map: Mapping[str, str]) -> Dict[str, str]:
    """Pull only allowlisted alias names (plus public allowlist) from environ."""
    wanted = set(alias_map.keys()) | set(REQUIRED_PRESENCE_ALIASES) | set(
        PUBLIC_CONFIG_ALLOWLIST
    )
    out: Dict[str, str] = {}
    for key in wanted:
        if key not in ALLOWLISTED_ALIASES and key not in PUBLIC_CONFIG_ALLOWLIST:
            continue
        if key in os.environ:
            val = os.environ[key]
            if isinstance(val, str):
                out[key] = val
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "APP-004 credential preflight: presence + capability report only. "
            "Does not dump secrets or claim live production readiness. "
            "Values via --values-stdin, --values-env, or fixtures-only --values-json."
        )
    )
    parser.add_argument(
        "--aliases-json",
        required=True,
        help="JSON object of slot→alias NAME tokens (from app.config secretAliases); remapping prohibited",
    )
    values_group = parser.add_mutually_exclusive_group(required=True)
    values_group.add_argument(
        "--values-stdin",
        action="store_true",
        help="Read allowlisted values JSON object from stdin (preferred; memory/pipe only)",
    )
    values_group.add_argument(
        "--values-env",
        action="store_true",
        help="Read only allowlisted alias names from process environment (no env dump)",
    )
    values_group.add_argument(
        "--values-json",
        help="Fixtures-only path under tests/fixtures/ (never general secrets-on-disk)",
    )
    parser.add_argument(
        "--context-json",
        help="Optional JSON context (fork/preview/fingerprints/claims)",
    )
    parser.add_argument(
        "--app-id",
        default="app",
        help="Validated app slug used for binding plan targets (default: app)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "summary"),
        default="json",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Opt-in network probe (live transport disabled in this worker; capabilities stay unverified/blocked)",
    )
    parser.add_argument(
        "--browser-artifact",
        action="append",
        default=[],
        help="Path to static/fixture or built bundle artifact to scan (repeatable)",
    )
    parser.add_argument(
        "--browser-evidence",
        choices=("static-fixture", "built-bundle"),
        default="static-fixture",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        alias_raw = Path(args.aliases_json).read_text(encoding="utf-8")
        alias_map = _load_json_object(alias_raw, label="aliases")
        context: Dict[str, Any] = {}
        if args.context_json:
            context = _load_json_object(
                Path(args.context_json).read_text(encoding="utf-8"),
                label="context",
            )
        if args.values_stdin:
            values = _load_json_object(sys.stdin.read(), label="values")
        elif args.values_env:
            values = _load_values_from_env(alias_map)
        else:
            values_path = Path(args.values_json)
            if not _path_is_under_fixtures(values_path):
                raise PreflightError(ERR_VALUES_SOURCE)
            values = _load_json_object(
                values_path.read_text(encoding="utf-8"),
                label="values",
            )
    except PreflightError as exc:
        print(
            json.dumps({"ok": False, "errors": [_safe_error(str(exc))]}),
            file=sys.stdout,
        )
        return 2
    except OSError:
        print(
            json.dumps({"ok": False, "errors": [_safe_error(ERR_INPUT_JSON)]}),
            file=sys.stdout,
        )
        return 2

    try:
        source = CallerProvidedEnvSource(values)
    except SourceValidationError as exc:
        print(
            json.dumps({"ok": False, "errors": [_safe_error(str(exc))]}),
            file=sys.stdout,
        )
        return 2

    transport = None
    if args.probe:
        transport = None

    report = run_preflight(
        alias_map=alias_map,
        source=source,
        context=context if isinstance(context, dict) else {},
        enable_network_probe=bool(args.probe),
        probe_transport=transport,
        browser_artifact_paths=list(args.browser_artifact or []),
        browser_evidence_kind=args.browser_evidence,
        app_id=str(args.app_id or "app"),
    )
    public = report.to_public_dict()
    if args.format == "json":
        print(json.dumps(public, indent=2, sort_keys=True))
    else:
        print(
            f"ok={public['ok']} localMetadataOk={public['localMetadataOk']} "
            f"productionReady=false deploymentAuthorized=false"
        )
        print(f"identity={public['identity'].get('vercelProjectDistinct')}")
        print(f"errors={len(public['errors'])}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

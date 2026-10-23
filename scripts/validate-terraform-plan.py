#!/usr/bin/env python3
"""Fail-closed offline Terraform JSON plan validator (APP-002).

Validates a `terraform show -json` plan against an explicit app ownership
allowlist with closed address→identity bindings. Does not call remotes, does
not apply, and does not log secret plan values. Not a full deploy initializer
(see APP-005).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Closed supported types — caller allowlists cannot expand beyond this set.
SUPPORTED_RESOURCE_TYPES = frozenset(
    {
        "vercel_project",
        "vercel_project_domain",
        "vercel_project_environment_variable",
        "cloudflare_record",
    }
)

FORBIDDEN_TYPES = frozenset(
    {
        "cloudflare_zone",
        "cloudflare_email_routing_settings",
        "cloudflare_email_routing_rule",
        "null_resource",
        "supabase_project",
    }
)

# Exact action tuples only (order-sensitive as emitted by Terraform).
ALLOWED_ACTION_TUPLES = frozenset(
    {
        ("create",),
        ("update",),
        ("no-op",),
    }
)

DNS_RESOURCE_TYPES = frozenset({"cloudflare_record"})
PROJECT_LINKED_TYPES = frozenset(
    {"vercel_project_domain", "vercel_project_environment_variable"}
)

SAFE_OWNERSHIP_KEY = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SAFE_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)
SAFE_STATE_KEY = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE_DNS_LABEL = re.compile(r"^[A-Za-z0-9._*-]{1,253}$")
SAFE_RECORD_TYPE = re.compile(r"^[A-Z]{1,16}$")
SAFE_ADDRESS = re.compile(
    r"^(?:module\.[A-Za-z0-9_-]+(?:\[[0-9]+\])?\.)*"
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"(?:\[[0-9]+\]|\[[\"'][A-Za-z0-9_.-]{1,128}[\"']\])?$"
)

# Generic redacted errors — never interpolate untrusted plan address/type/values.
E_OWNERSHIP_CONFIG = "ownership configuration invalid or incomplete"
E_PLAN_OBJECT = "plan must be a JSON object"
E_MISSING_CHANGES = "plan missing resource_changes"
E_CHANGES_TYPE = "plan.resource_changes must be a list"
E_MALFORMED = "malformed plan structure"
E_UNEXPECTED_ADDRESS = "unexpected resource address"
E_UNEXPECTED_TYPE = "unexpected resource type"
E_FORBIDDEN_TYPE = "forbidden resource type"
E_FORBIDDEN_ACTION = "forbidden or unsupported action"
E_OWNERSHIP_MISMATCH = "ownership mismatch"
E_UNKNOWN_OWNERSHIP = "unknown ownership field"
E_MISSING_OWNERSHIP = "missing ownership field"
E_DNS_FORBIDDEN = "DNS resource forbidden for configured dnsChoice"
E_PROJECT_LINK = "project linkage unverified (pending APP-005)"
E_PROVISIONER = "provisioner or hook forbidden"
E_TYPE_BINDING = "resource type does not match address binding"


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _err(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _walk_configuration_resources(node: Any) -> list[dict]:
    found: list[dict] = []
    if not isinstance(node, dict):
        return found
    for resource in node.get("resources") or []:
        if isinstance(resource, dict):
            found.append(resource)
    module_calls = node.get("module_calls")
    if isinstance(module_calls, dict):
        for child in module_calls.values():
            if isinstance(child, dict):
                found.extend(_walk_configuration_resources(child.get("module") or {}))
    root = node.get("root_module")
    if isinstance(root, dict):
        found.extend(_walk_configuration_resources(root))
    return found


def _module_path_and_local(address: str) -> tuple[list[str], str] | None:
    """Split `module.a.module.b[0].type.name[...]` into module path + local addr."""
    if not isinstance(address, str) or not address:
        return None
    modules: list[str] = []
    rest = address
    while rest.startswith("module."):
        rest = rest[len("module.") :]
        m = re.match(r"^([A-Za-z0-9_-]+)(?:\[[0-9]+\])?\.(.*)$", rest)
        if not m:
            return None
        modules.append(m.group(1))
        rest = m.group(2)
    if not rest or "." not in rest:
        return None
    return modules, rest


def _find_config_resource(configuration: dict, full_address: str) -> dict | None:
    parsed = _module_path_and_local(full_address)
    if parsed is None:
        return None
    modules, local = parsed
    node: Any = configuration.get("root_module")
    if not isinstance(node, dict):
        return None
    for mod_name in modules:
        calls = node.get("module_calls")
        if not isinstance(calls, dict):
            return None
        child = calls.get(mod_name)
        if not isinstance(child, dict):
            return None
        node = child.get("module")
        if not isinstance(node, dict):
            return None
    resources = node.get("resources")
    if not isinstance(resources, list):
        return None
    # local may include index suffix; match on type.name prefix used in config.
    local_base = re.sub(r"(\[[0-9]+\]|\[\"[^\"]+\"\])$", "", local)
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        r_addr = resource.get("address")
        if r_addr == local or r_addr == local_base:
            return resource
        rtype = resource.get("type")
        rname = resource.get("name")
        if isinstance(rtype, str) and isinstance(rname, str) and f"{rtype}.{rname}" == local_base:
            return resource
    return None


def _project_id_expression_trusted(
    configuration: dict,
    change_address: str,
    vercel_project_binding: str,
) -> bool:
    """True when config expressions reference the bound vercel_project.id."""
    resource = _find_config_resource(configuration, change_address)
    if resource is None:
        return False
    expressions = resource.get("expressions")
    if not isinstance(expressions, dict):
        return False
    project_expr = expressions.get("project_id")
    if not isinstance(project_expr, dict):
        return False
    references = project_expr.get("references")
    if not isinstance(references, list) or not references:
        return False
    parsed = _module_path_and_local(vercel_project_binding)
    if parsed is None:
        return False
    _, local_project = parsed
    local_base = re.sub(r"(\[[0-9]+\]|\[\"[^\"]+\"\])$", "", local_project)
    trusted = {local_base, f"{local_base}.id"}
    return any(isinstance(ref, str) and ref in trusted for ref in references)


def validate_ownership_config(ownership: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(ownership, dict):
        return [E_OWNERSHIP_CONFIG]

    hostname = ownership.get("hostname")
    identity = ownership.get("deploymentIdentity")
    state_key = ownership.get("stateKey")
    dns_choice = ownership.get("dnsChoice")
    allowed_types = ownership.get("allowedResourceTypes")
    bindings = ownership.get("resourceBindings")
    vercel = ownership.get("vercel")
    cloudflare = ownership.get("cloudflare")

    if not isinstance(hostname, str) or not hostname.strip() or not SAFE_HOSTNAME.match(hostname):
        _err(errors, E_OWNERSHIP_CONFIG)
    if (
        not isinstance(identity, str)
        or not identity.strip()
        or not SAFE_OWNERSHIP_KEY.match(identity)
    ):
        _err(errors, E_OWNERSHIP_CONFIG)
    if (
        not isinstance(state_key, str)
        or not state_key.strip()
        or not SAFE_STATE_KEY.match(state_key)
    ):
        _err(errors, E_OWNERSHIP_CONFIG)
    if dns_choice not in ("unresolved", "cloudflare", "cloudfront"):
        _err(errors, E_OWNERSHIP_CONFIG)

    if not isinstance(allowed_types, list) or not allowed_types:
        _err(errors, E_OWNERSHIP_CONFIG)
    elif not all(isinstance(t, str) and t for t in allowed_types):
        _err(errors, E_OWNERSHIP_CONFIG)
    elif any(t not in SUPPORTED_RESOURCE_TYPES for t in allowed_types):
        _err(errors, E_OWNERSHIP_CONFIG)
    elif len(set(allowed_types)) != len(allowed_types):
        _err(errors, E_OWNERSHIP_CONFIG)

    if not isinstance(bindings, dict) or not bindings:
        _err(errors, E_OWNERSHIP_CONFIG)
    else:
        for address, rtype in bindings.items():
            if not isinstance(address, str) or not SAFE_ADDRESS.match(address):
                _err(errors, E_OWNERSHIP_CONFIG)
                continue
            if not isinstance(rtype, str) or rtype not in SUPPORTED_RESOURCE_TYPES:
                _err(errors, E_OWNERSHIP_CONFIG)
                continue
            if isinstance(allowed_types, list) and rtype not in allowed_types:
                _err(errors, E_OWNERSHIP_CONFIG)
            if dns_choice != "cloudflare" and rtype in DNS_RESOURCE_TYPES:
                _err(errors, E_OWNERSHIP_CONFIG)

    if (
        isinstance(allowed_types, list)
        and dns_choice != "cloudflare"
        and any(t in DNS_RESOURCE_TYPES for t in allowed_types if isinstance(t, str))
    ):
        _err(errors, E_OWNERSHIP_CONFIG)

    if not isinstance(vercel, dict):
        _err(errors, E_OWNERSHIP_CONFIG)
    else:
        team_id = vercel.get("teamId")
        project_id = vercel.get("projectId")
        if team_id is not None and (
            not isinstance(team_id, str) or not team_id.strip() or not SAFE_ID.match(team_id)
        ):
            _err(errors, E_OWNERSHIP_CONFIG)
        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or not SAFE_ID.match(project_id)
        ):
            _err(errors, E_OWNERSHIP_CONFIG)

    if dns_choice == "cloudflare":
        if not isinstance(cloudflare, dict):
            _err(errors, E_OWNERSHIP_CONFIG)
        else:
            zone_id = cloudflare.get("zoneId")
            record_name = cloudflare.get("recordName")
            record_type = cloudflare.get("recordType")
            target = cloudflare.get("target")
            if not isinstance(zone_id, str) or not zone_id.strip() or not SAFE_ID.match(zone_id):
                _err(errors, E_OWNERSHIP_CONFIG)
            if (
                not isinstance(record_name, str)
                or not record_name.strip()
                or not SAFE_DNS_LABEL.match(record_name)
            ):
                _err(errors, E_OWNERSHIP_CONFIG)
            if (
                not isinstance(record_type, str)
                or not SAFE_RECORD_TYPE.match(record_type)
            ):
                _err(errors, E_OWNERSHIP_CONFIG)
            if not isinstance(target, str) or not target.strip() or not SAFE_DNS_LABEL.match(target):
                _err(errors, E_OWNERSHIP_CONFIG)
    elif cloudflare is not None:
        # Non-cloudflare dnsChoice must not carry Cloudflare ownership claims.
        _err(errors, E_OWNERSHIP_CONFIG)

    return errors


def _side_field(
    change: dict, side: str, field_name: str
) -> tuple[Any | None, str]:
    """Return (value, status) status in ok|unknown|missing|malformed|absent_side."""
    if side == "after":
        after_unknown = change.get("after_unknown")
        if after_unknown is None:
            after_unknown = {}
        if not isinstance(after_unknown, dict):
            return None, "malformed"
        if after_unknown.get(field_name) is True:
            return None, "unknown"
        payload = change.get("after")
    elif side == "before":
        payload = change.get("before")
    else:
        return None, "malformed"

    if payload is None:
        return None, "absent_side"
    if not isinstance(payload, dict):
        return None, "malformed"
    if field_name not in payload:
        return None, "missing"
    return payload.get(field_name), "ok"


def _actions_tuple(actions: Any) -> tuple[str, ...] | None:
    if not isinstance(actions, list) or not actions:
        return None
    if not all(isinstance(a, str) and a for a in actions):
        return None
    return tuple(actions)


def _expected_fields(
    rtype: str, ownership: dict, *, actions: tuple[str, ...]
) -> dict[str, Any]:
    """Map of attribute → expected value for a supported type."""
    expected: dict[str, Any] = {}
    if rtype == "vercel_project":
        expected["name"] = ownership["deploymentIdentity"]
        team_id = (ownership.get("vercel") or {}).get("teamId")
        if team_id:
            expected["team_id"] = team_id
        # Existing project: resource id must match known projectId.
        if actions in (("update",), ("no-op",)):
            expected["id"] = ownership["vercel"]["projectId"]
    elif rtype == "vercel_project_domain":
        expected["domain"] = ownership["hostname"]
        expected["project_id"] = ownership["vercel"]["projectId"]
    elif rtype == "vercel_project_environment_variable":
        expected["project_id"] = ownership["vercel"]["projectId"]
        team_id = (ownership.get("vercel") or {}).get("teamId")
        if team_id:
            expected["team_id"] = team_id
    elif rtype == "cloudflare_record":
        cf = ownership["cloudflare"]
        expected["zone_id"] = cf["zoneId"]
        expected["name"] = cf["recordName"]
        expected["type"] = cf["recordType"]
        expected["content"] = cf["target"]
    return expected


def _check_field_value(
    errors: list[str],
    value: Any,
    status: str,
    expected: Any,
    *,
    allow_unknown: bool = False,
) -> str:
    """Validate one field. Returns 'ok'|'unknown'|'fail'."""
    if status == "malformed":
        _err(errors, E_MALFORMED)
        return "fail"
    if status == "absent_side":
        _err(errors, E_MALFORMED)
        return "fail"
    if status == "unknown":
        if allow_unknown:
            return "unknown"
        _err(errors, E_UNKNOWN_OWNERSHIP)
        return "fail"
    if status == "missing":
        _err(errors, E_MISSING_OWNERSHIP)
        return "fail"
    if value is None or (isinstance(value, str) and not value.strip()):
        _err(errors, E_MISSING_OWNERSHIP)
        return "fail"
    if not isinstance(value, (str, int, float, bool)):
        # Reject nested/object ownership values rather than comparing blindly.
        _err(errors, E_OWNERSHIP_MISMATCH)
        return "fail"
    if value != expected:
        _err(errors, E_OWNERSHIP_MISMATCH)
        return "fail"
    return "ok"


def _vercel_project_binding(bindings: dict) -> str | None:
    for address, rtype in bindings.items():
        if rtype == "vercel_project":
            return address
    return None


def _validate_resource_ownership(
    errors: list[str],
    *,
    change: dict,
    actions: tuple[str, ...],
    rtype: str,
    address: str,
    ownership: dict,
    configuration: dict,
) -> None:
    expected = _expected_fields(rtype, ownership, actions=actions)
    sides: list[str] = []
    if actions == ("create",):
        sides = ["after"]
    elif actions in (("update",), ("no-op",)):
        sides = ["before", "after"]
    else:
        _err(errors, E_FORBIDDEN_ACTION)
        return

    project_id_unknown_after = False

    for side in sides:
        for field_name, exp in expected.items():
            # On create, project_id may be provider-unknown if config references
            # the bound vercel_project (verified below). Other fields must be known.
            allow_unknown = (
                side == "after"
                and actions == ("create",)
                and field_name == "project_id"
                and rtype in PROJECT_LINKED_TYPES
            )
            value, status = _side_field(change, side, field_name)
            result = _check_field_value(
                errors, value, status, exp, allow_unknown=allow_unknown
            )
            if result == "unknown" and field_name == "project_id":
                project_id_unknown_after = True

    if project_id_unknown_after:
        bindings = ownership["resourceBindings"]
        project_binding = _vercel_project_binding(bindings)
        if project_binding is None:
            _err(errors, E_PROJECT_LINK)
        elif not _project_id_expression_trusted(
            configuration, address, project_binding
        ):
            _err(errors, E_PROJECT_LINK)


def validate_plan(plan: dict, ownership: dict) -> ValidationResult:
    errors: list[str] = []
    errors.extend(validate_ownership_config(ownership))
    if errors:
        return ValidationResult(ok=False, errors=errors)

    if not isinstance(plan, dict):
        return ValidationResult(ok=False, errors=[E_PLAN_OBJECT])

    allowed_types = set(ownership["allowedResourceTypes"])
    bindings: dict[str, str] = ownership["resourceBindings"]
    dns_choice = ownership["dnsChoice"]
    configuration = plan.get("configuration")
    if configuration is None:
        configuration = {}
    if not isinstance(configuration, dict):
        return ValidationResult(ok=False, errors=[E_MALFORMED])

    for resource in _walk_configuration_resources(configuration):
        provisioners = resource.get("provisioners")
        if provisioners:
            _err(errors, E_PROVISIONER)
        rtype = resource.get("type")
        if rtype in FORBIDDEN_TYPES:
            _err(errors, E_FORBIDDEN_TYPE)

    changes = plan.get("resource_changes")
    if changes is None:
        _err(errors, E_MISSING_CHANGES)
        return ValidationResult(ok=False, errors=errors)
    if not isinstance(changes, list):
        _err(errors, E_CHANGES_TYPE)
        return ValidationResult(ok=False, errors=errors)

    for change_entry in changes:
        if not isinstance(change_entry, dict):
            _err(errors, E_MALFORMED)
            continue

        address = change_entry.get("address")
        rtype = change_entry.get("type")
        change = change_entry.get("change")

        if not isinstance(change, dict):
            _err(errors, E_MALFORMED)
            continue

        actions = _actions_tuple(change.get("actions"))
        if actions is None:
            _err(errors, E_MALFORMED)
            continue
        if actions not in ALLOWED_ACTION_TUPLES:
            _err(errors, E_FORBIDDEN_ACTION)
            # Still fall through to type/address checks where safe.
            # Do not continue validating ownership on forbidden actions.

        if not isinstance(address, str) or not address:
            _err(errors, E_MALFORMED)
            continue
        if not isinstance(rtype, str) or not rtype:
            _err(errors, E_MALFORMED)
            continue

        if rtype in FORBIDDEN_TYPES:
            _err(errors, E_FORBIDDEN_TYPE)
            continue

        if address not in bindings:
            _err(errors, E_UNEXPECTED_ADDRESS)
            continue

        bound_type = bindings[address]
        if rtype != bound_type:
            _err(errors, E_TYPE_BINDING)
            continue

        if rtype not in allowed_types or rtype not in SUPPORTED_RESOURCE_TYPES:
            _err(errors, E_UNEXPECTED_TYPE)
            continue

        if rtype in DNS_RESOURCE_TYPES and dns_choice != "cloudflare":
            _err(errors, E_DNS_FORBIDDEN)
            continue

        if actions not in ALLOWED_ACTION_TUPLES:
            continue

        _validate_resource_ownership(
            errors,
            change=change,
            actions=actions,
            rtype=rtype,
            address=address,
            ownership=ownership,
            configuration=configuration,
        )

    return ValidationResult(ok=len(errors) == 0, errors=errors)


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"ERROR: file not found: {path.name}") from None
    except json.JSONDecodeError:
        raise SystemExit(f"ERROR: invalid JSON in {path.name}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"ERROR: {path.name} root must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed offline Terraform JSON plan guard (APP-002)"
    )
    parser.add_argument("--plan", required=True, help="Path to terraform show -json plan file")
    parser.add_argument(
        "--ownership",
        required=True,
        help="Path to explicit app ownership allowlist JSON",
    )
    args = parser.parse_args(argv)

    plan = _load_json(Path(args.plan))
    ownership = _load_json(Path(args.ownership))
    result = validate_plan(plan, ownership)

    if result.ok:
        print(f"OK: plan passed ownership guard ({Path(args.plan).name})")
        return 0

    print(f"FAIL: plan rejected by ownership guard ({Path(args.plan).name})", file=sys.stderr)
    for message in result.errors:
        print(f"- {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

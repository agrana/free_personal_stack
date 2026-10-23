#!/usr/bin/env python3
"""Validate non-secret app.config against APP-001 schema and invariants.

Draft mode: schema + closed fields + local invariants (unresolved DNS allowed).
Deployment-ready mode: also requires resolved DNS choice and verified preflight
metadata flags. Passing deployment-ready means metadata readiness only —
callers must independently verify live account, DNS, database, and secret-source
facts before any apply.

Never echoes unsafe or credential-like input values, nor arbitrary unknown
property names, in error messages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError, ValidationError
except ImportError as exc:  # pragma: no cover
    print(
        "error: Python package 'jsonschema' is required. "
        "Install into an isolated venv from requirements-bootstrap.txt "
        "(see docs/bootstrap.md).",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "app.config.schema.json"

RESERVED_NAMESPACES = frozenset(
    {
        "public",
        "auth",
        "storage",
        "extensions",
        "graphql_public",
        "realtime",
        "supabase_functions",
        "vault",
        "shared",
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "template",
        "postgres",
        "supabase_migrations",
    }
)

# Schema-declared property names only (safe to surface in paths/messages).
SCHEMA_KNOWN_KEYS = frozenset(
    {
        "schemaVersion",
        "app",
        "slug",
        "repository",
        "owner",
        "name",
        "hostname",
        "database",
        "mode",
        "namespace",
        "deployment",
        "identity",
        "stateKey",
        "dns",
        "choice",
        "secretAliases",
        "localConflictInventory",
        "slugs",
        "namespaces",
        "hostnames",
        "deploymentIdentities",
        "stateKeys",
        "externalVerification",
        "accountPermissions",
        "sharedDatabaseGuard",
        "secretSource",
        "liveConflictDiscovery",
        "CLOUDFLARE_API_TOKEN",
        "VERCEL_API_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ACCESS_TOKEN",
        "TERRAFORM_STATE_PASSWORD",
        "SUPABASE_PROJECT_ID",
        "DOMAIN_NAME",
        "SUPABASE_DATABASE_PASSWORD",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SUPPORT_EMAIL_DESTINATION",
        "CONTACT_EMAIL_DESTINATION",
        "VERCEL_TEAM_ID",
        "CLOUDFLARE_ACCOUNT_ID",
    }
)

OBJECT_ROOT_FIELDS = frozenset(
    {
        "app",
        "repository",
        "database",
        "deployment",
        "dns",
        "secretAliases",
        "localConflictInventory",
        "externalVerification",
    }
)

SHELL_METACHAR_RE = re.compile(r"""[;|&$`<>(){}[\]\\"'\n\r\t]""")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
CREDENTIAL_LIKE_RE = re.compile(
    r"(?i)(://[^/\s]+:[^/@\s]+@|password\s*=|secret\s*=|api[_-]?key\s*=|token\s*=|Bearer\s+[A-Za-z0-9._\-]+|eyJ[A-Za-z0-9_-]{10,})"
)
ALIAS_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)

    def extend(self, messages: Iterable[str]) -> None:
        self.errors.extend(messages)
        if messages:
            self.ok = False


def _safe_field_ref(path: str) -> str:
    return path


def _reject_value(path: str, reason: str) -> str:
    """Return an error that never includes the offending value."""
    return f"{_safe_field_ref(path)}: {reason}"


def _format_schema_path(path_parts: Sequence[Any]) -> str:
    """Build a JSON-pointer-like path using only schema-known key names."""
    if not path_parts:
        return "$"
    out = "$"
    for part in path_parts:
        if isinstance(part, int):
            out += f"[{part}]"
        elif isinstance(part, str) and part in SCHEMA_KNOWN_KEYS:
            out += f".{part}"
        else:
            out += ".<unknown>"
    return out


def _type_reason(expected: Any) -> str:
    if isinstance(expected, list):
        return "must be of an allowed schema type"
    if isinstance(expected, str):
        return f"must be of type {expected}"
    return "must be of the schema-declared type"


def _sanitize_schema_error(err: ValidationError) -> str:
    """Map jsonschema errors to schema-controlled paths and generic reasons."""
    path = _format_schema_path(list(err.path))
    validator = err.validator

    if validator == "additionalProperties":
        return _reject_value(path, "unknown field rejected")
    if validator == "propertyNames":
        return _reject_value(path, "property name rejected")
    if validator == "required":
        missing: List[str] = []
        required = err.validator_value if isinstance(err.validator_value, list) else []
        instance = err.instance if isinstance(err.instance, dict) else {}
        for name in required:
            if name not in instance and name in SCHEMA_KNOWN_KEYS:
                missing.append(name)
        if len(missing) == 1:
            return _reject_value(path, f"missing required property {missing[0]}")
        if missing:
            return _reject_value(
                path, "missing required properties (" + ", ".join(missing) + ")"
            )
        return _reject_value(path, "missing required property")
    if validator == "type":
        return _reject_value(path, _type_reason(err.validator_value))
    if validator == "const":
        return _reject_value(path, "must equal the schema-declared constant")
    if validator == "enum":
        return _reject_value(path, "must be one of the schema-declared enum values")
    if validator == "pattern":
        return _reject_value(path, "does not match the schema pattern")
    if validator in {"minProperties", "maxProperties", "minItems", "maxItems", "minLength", "maxLength"}:
        return _reject_value(path, "does not satisfy schema size constraints")
    if validator == "uniqueItems":
        return _reject_value(path, "items must be unique")
    return _reject_value(path, "failed schema validation")


def _is_structural_schema_error(err: ValidationError) -> bool:
    """True when shape is unsafe for cross-field `.get` invariants."""
    if err.validator != "type":
        return False
    parts = list(err.path)
    if not parts:
        return True
    if len(parts) == 1 and parts[0] in OBJECT_ROOT_FIELDS:
        return True
    return False


def validate_schema_document(schema: dict) -> None:
    Draft202012Validator.check_schema(schema)


def load_schema(schema_path: Optional[Path] = None) -> dict:
    path = schema_path or DEFAULT_SCHEMA_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"schema file unavailable ({exc.__class__.__name__})") from None
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("schema file is not valid JSON") from None
    if not isinstance(schema, dict):
        raise ValueError("schema root must be a JSON object")
    validate_schema_document(schema)
    return schema


def _walk_strings(node: Any, path: str = "$") -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key in SCHEMA_KNOWN_KEYS:
                child_path = f"{path}.{key}"
            else:
                child_path = f"{path}.<unknown>"
            found.extend(_walk_strings(value, child_path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk_strings(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        found.append((path, node))
    return found


def _check_unsafe_strings(config: dict) -> List[str]:
    errors: List[str] = []
    for path, value in _walk_strings(config):
        if CONTROL_CHAR_RE.search(value) or SHELL_METACHAR_RE.search(value):
            errors.append(
                _reject_value(path, "contains shell metacharacters (value omitted)")
            )
            continue
        if CREDENTIAL_LIKE_RE.search(value):
            errors.append(
                _reject_value(
                    path,
                    "looks like an embedded credential or URL-with-userinfo "
                    "(value omitted; aliases must be names only)",
                )
            )
    return errors


def _check_hostname_invariants(hostname: str) -> List[str]:
    errors: List[str] = []
    path = "$.hostname"
    if not isinstance(hostname, str):
        return [_reject_value(path, "must be a string")]
    lowered = hostname.lower()
    if hostname != lowered:
        errors.append(_reject_value(path, "must be lowercase"))
    if "*" in hostname:
        errors.append(_reject_value(path, "wildcard hostnames are not allowed"))
    if "://" in hostname or hostname.startswith("//"):
        errors.append(_reject_value(path, "URL schemes are not allowed"))
    if "/" in hostname or "?" in hostname or "#" in hostname:
        errors.append(_reject_value(path, "paths and URL components are not allowed"))
    if ":" in hostname:
        errors.append(_reject_value(path, "ports are not allowed"))
    if not HOSTNAME_RE.match(lowered):
        errors.append(_reject_value(path, "must be a valid FQDN hostname pattern"))
    return errors


def _check_namespace(namespace: str) -> List[str]:
    errors: List[str] = []
    path = "$.database.namespace"
    if namespace in RESERVED_NAMESPACES:
        errors.append(
            _reject_value(path, "namespace is reserved or shared (schema-reserved name)")
        )
    return errors


def _check_secret_aliases(aliases: dict) -> List[str]:
    errors: List[str] = []
    if not isinstance(aliases, dict):
        return [_reject_value("$.secretAliases", "must be an object of alias names")]
    for key, value in aliases.items():
        # Never put arbitrary instance keys into the path (may be secret-like).
        key_path = "$.secretAliases.<entry>"
        if not isinstance(key, str) or key not in SCHEMA_KNOWN_KEYS:
            errors.append(_reject_value(key_path, "unknown or invalid alias key"))
            continue
        key_path = f"$.secretAliases.{key}"
        if not ALIAS_NAME_RE.match(key):
            errors.append(
                _reject_value(key_path, "alias keys must be uppercase NAME tokens")
            )
        if not isinstance(value, str) or not ALIAS_NAME_RE.match(value):
            errors.append(
                _reject_value(
                    key_path,
                    "alias values must be uppercase NAME tokens, not credentials",
                )
            )
        elif value != value.upper() or any(ch.islower() for ch in value):
            errors.append(
                _reject_value(key_path, "alias values must be uppercase NAME tokens")
            )
    return errors


def _check_local_conflicts(config: dict) -> List[str]:
    errors: List[str] = []
    inventory = config.get("localConflictInventory")
    if not isinstance(inventory, dict):
        return errors

    app = config.get("app")
    database = config.get("database")
    deployment = config.get("deployment")

    app_slug = app.get("slug") if isinstance(app, dict) else None
    namespace = database.get("namespace") if isinstance(database, dict) else None
    hostname = config.get("hostname") if isinstance(config.get("hostname"), str) else None
    identity = deployment.get("identity") if isinstance(deployment, dict) else None
    state_key = deployment.get("stateKey") if isinstance(deployment, dict) else None

    checks = [
        ("slugs", app_slug, "$.app.slug"),
        ("namespaces", namespace, "$.database.namespace"),
        ("hostnames", hostname, "$.hostname"),
        ("deploymentIdentities", identity, "$.deployment.identity"),
        ("stateKeys", state_key, "$.deployment.stateKey"),
    ]
    for inventory_key, current, path in checks:
        known = inventory.get(inventory_key)
        if not isinstance(known, list):
            continue
        if current is not None and current in known:
            errors.append(
                _reject_value(
                    path,
                    f"conflicts with localConflictInventory.{inventory_key} "
                    "(local inventory only; live discovery is separate)",
                )
            )
    return errors


def _check_deployment_ready(config: dict) -> List[str]:
    errors: List[str] = []
    dns = config.get("dns")
    dns_choice = dns.get("choice") if isinstance(dns, dict) else None
    if dns_choice == "unresolved":
        errors.append(
            _reject_value(
                "$.dns.choice",
                "unresolved DNS is valid for draft configuration but blocks "
                "deployment-readiness preflight; choose cloudflare or cloudfront",
            )
        )
    verification = config.get("externalVerification")
    if not isinstance(verification, dict):
        errors.append(
            _reject_value(
                "$.externalVerification",
                "must be an object with verified preflight metadata before "
                "deployment-ready (metadata only; live facts must be verified "
                "independently before apply)",
            )
        )
        return errors
    required_verified = (
        ("accountPermissions", "verified"),
        ("sharedDatabaseGuard", "verified"),
        ("secretSource", "verified"),
        ("liveConflictDiscovery", "complete"),
    )
    for key, expected in required_verified:
        actual = verification.get(key)
        if actual != expected:
            errors.append(
                _reject_value(
                    f"$.externalVerification.{key}",
                    f"must be {expected!r} before deployment-ready "
                    f"(metadata readiness only; callers must independently "
                    f"verify live account/database/secret-source/DNS facts "
                    f"before apply)",
                )
            )
    return errors


def validate_config(
    config: Any,
    mode: str = "draft",
    schema: Optional[dict] = None,
    schema_path: Optional[Path] = None,
) -> ValidationResult:
    if mode not in {"draft", "deployment-ready"}:
        return ValidationResult(ok=False, errors=[f"unknown mode: {mode}"])

    result = ValidationResult(ok=True)
    try:
        loaded_schema = schema if schema is not None else load_schema(schema_path)
    except FileNotFoundError:
        return ValidationResult(
            ok=False, errors=["schema unavailable: file missing or unreadable"]
        )
    except ValueError as exc:
        return ValidationResult(ok=False, errors=[f"schema invalid: {exc}"])
    except SchemaError:
        return ValidationResult(
            ok=False, errors=["schema invalid: failed Draft 2020-12 schema check"]
        )

    try:
        validator = Draft202012Validator(loaded_schema)
        schema_error_list = sorted(
            validator.iter_errors(config), key=lambda e: list(e.path)
        )
    except SchemaError:
        return ValidationResult(
            ok=False, errors=["schema invalid: failed Draft 2020-12 schema check"]
        )

    for err in schema_error_list:
        result.extend([_sanitize_schema_error(err)])

    # Malformed shapes: return schema errors only; do not run `.get` invariants.
    if not isinstance(config, dict):
        return result
    if any(_is_structural_schema_error(err) for err in schema_error_list):
        return result

    result.extend(_check_unsafe_strings(config))

    hostname = config.get("hostname")
    if isinstance(hostname, str):
        result.extend(_check_hostname_invariants(hostname))

    database = config.get("database")
    if isinstance(database, dict):
        namespace = database.get("namespace")
        if isinstance(namespace, str):
            result.extend(_check_namespace(namespace))

    secret_aliases = config.get("secretAliases")
    if isinstance(secret_aliases, dict):
        result.extend(_check_secret_aliases(secret_aliases))

    if isinstance(config.get("localConflictInventory"), dict):
        result.extend(_check_local_conflicts(config))

    if mode == "deployment-ready":
        result.extend(_check_deployment_ready(config))

    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate APP-001 non-secret app configuration."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to app.config JSON file",
    )
    parser.add_argument(
        "--schema",
        default=str(DEFAULT_SCHEMA_PATH),
        help="Path to app.config.schema.json",
    )
    parser.add_argument(
        "--mode",
        choices=("draft", "deployment-ready"),
        default="draft",
        help="draft allows unresolved DNS; deployment-ready enforces preflight blocks",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    config_path = Path(args.config)
    schema_path = Path(args.schema)

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError:
        print("error: unable to read config: file missing or unreadable", file=sys.stderr)
        return 2
    except json.JSONDecodeError:
        print("error: unable to read config: invalid JSON", file=sys.stderr)
        return 2

    try:
        result = validate_config(config, mode=args.mode, schema_path=schema_path)
    except Exception:
        print("error: validation failed unexpectedly", file=sys.stderr)
        return 2

    if result.ok:
        print(f"OK: {config_path} passed {args.mode} validation")
        return 0

    print(f"FAIL: {config_path} failed {args.mode} validation", file=sys.stderr)
    for error in result.errors:
        print(f"- {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

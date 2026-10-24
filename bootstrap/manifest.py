"""Atomic durable manifest load/store validated against closed schema."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

from bootstrap.errors import ERR_SCHEMA, InitializerError

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError, ValidationError
except ImportError:  # pragma: no cover
    Draft202012Validator = None  # type: ignore
    SchemaError = Exception  # type: ignore
    ValidationError = Exception  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "bootstrap-manifest.schema.json"

_SCHEMA_CACHE: Optional[dict] = None
_VALIDATOR_CACHE: Any = None


def load_manifest_schema(schema_path: Optional[Path] = None) -> dict:
    global _SCHEMA_CACHE, _VALIDATOR_CACHE
    path = schema_path or DEFAULT_SCHEMA_PATH
    if _SCHEMA_CACHE is not None and schema_path is None:
        return _SCHEMA_CACHE
    if Draft202012Validator is None:
        raise InitializerError(ERR_SCHEMA)
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError):
        raise InitializerError(ERR_SCHEMA) from None
    if schema_path is None:
        _SCHEMA_CACHE = schema
        _VALIDATOR_CACHE = Draft202012Validator(schema)
    return schema


def validate_manifest(document: Mapping[str, Any], schema_path: Optional[Path] = None) -> None:
    if Draft202012Validator is None:
        raise InitializerError(ERR_SCHEMA)
    if schema_path is None:
        load_manifest_schema()
        validator = _VALIDATOR_CACHE
    else:
        schema = load_manifest_schema(schema_path)
        validator = Draft202012Validator(schema)
    try:
        validator.validate(dict(document))
    except ValidationError:
        raise InitializerError(ERR_SCHEMA) from None


def empty_manifest(
    *,
    app_slug: str,
    config_hash: str,
    template_revision: str,
    plan_digest: str,
    output_root: str,
    dns_resolved: bool,
) -> dict:
    return {
        "schemaVersion": 1,
        "appSlug": app_slug,
        "configHash": config_hash,
        "templateRevision": template_revision,
        "planDigest": plan_digest,
        "status": "planned",
        "completedStages": [],
        "resumeState": {"nextStage": "validate_inputs", "failureCode": None},
        "resourceIds": {
            "vercelProjectId": None,
            "dnsRecordIds": [],
            "infraStateKey": None,
        },
        "scaffold": {
            "outputRoot": output_root,
            "fileCount": 0,
            "scaffoldDigest": None,
        },
        "gates": {
            "liveApplyIntegrated": False,
            "cloudfrontSupported": False,
            "dnsResolved": dns_resolved,
            "rawTerraformPlanPersisted": False,
        },
    }


def load_manifest(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        raise InitializerError(ERR_SCHEMA) from None
    if not isinstance(data, dict):
        raise InitializerError(ERR_SCHEMA)
    validate_manifest(data)
    return data


def atomic_write_text(path: Path, payload: str) -> None:
    """Atomically replace path with payload (fsync file + best-effort dir)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = f".{path.name}.{os.getpid()}.tmp"
    tmp_path = path.parent / tmp_name
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise InitializerError(ERR_SCHEMA) from None


def atomic_write_manifest(path: Path, document: Mapping[str, Any]) -> None:
    validate_manifest(document)
    payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    atomic_write_text(path, payload)


def mark_stage_complete(manifest: MutableMapping[str, Any], stage: str, next_stage: Optional[str]) -> None:
    completed = list(manifest.get("completedStages") or [])
    if stage not in completed:
        completed.append(stage)
    manifest["completedStages"] = completed
    manifest["resumeState"] = {
        "nextStage": next_stage,
        "failureCode": None,
    }


def mark_failure(manifest: MutableMapping[str, Any], *, stage: str, code: str) -> None:
    manifest["status"] = "failed" if code != "stage.interrupted" else "interrupted"
    if code == "stage.interrupted" or code.endswith("interrupted"):
        manifest["status"] = "interrupted"
    manifest["resumeState"] = {
        "nextStage": stage,
        "failureCode": code,
    }

"""Canonical hashing for config identity and plan digests (secret-free)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_digest(data: bytes) -> str:
    return f"sha256:{sha256_hex(data)}"


def hash_mapping(document: Mapping[str, Any]) -> str:
    return sha256_digest(canonical_json_bytes(document))


def config_identity_hash(config: Mapping[str, Any]) -> str:
    """Hash the validated non-secret app.config document."""
    return hash_mapping(config)


def plan_digest(plan_document: Mapping[str, Any]) -> str:
    """Digest of the reviewed secret-free plan artifact (not raw Terraform)."""
    return hash_mapping(plan_document)

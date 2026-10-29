#!/usr/bin/env python3
"""Closed-schema loader for merge-reviewed onboarding desired-state policy.

The tracked onboarding.config.json is the merge authorization for automatic
shell deploy and hostname/DNS apply. Template defaults keep auto_apply false
and omit app-specific hostnames. Validation rejects unknown keys, weak types,
symlinks/escapes, and unbound source trees.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Mapping, Optional, Tuple

CONFIG_FILENAME = 'onboarding.config.json'
SCHEMA_VERSION = 1
ALLOWED_KEYS = (
    'schema_version',
    'hostname',
    'cloudflare_zone_name',
    'auto_apply',
    'expected_project_id',
    'confirm_repository_id',
)
HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
RESERVED_LABELS = frozenset({'www', 'mail'})
PROJECT_ID_RE = re.compile(r'^prj_[A-Za-z0-9]+$')
REPOSITORY_ID_RE = re.compile(r'^[1-9][0-9]*$')


class ConfigError(Exception):
    """Raised with a stable machine-readable status string."""


def _reject_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ConfigError('config_duplicate_key')
        seen[key] = value
    return seen


def normalize_hostname(value: str) -> str:
    if not isinstance(value, str):
        raise ConfigError('hostname_invalid')
    host = value.strip().lower().rstrip('.')
    if '://' in host or '/' in host or '?' in host or '#' in host or '@' in host:
        raise ConfigError('hostname_invalid')
    if ':' in host or '*' in host or ' ' in host or host.startswith('.'):
        raise ConfigError('hostname_invalid')
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', host):
        raise ConfigError('hostname_invalid')
    if not HOSTNAME_RE.fullmatch(host):
        raise ConfigError('hostname_invalid')
    labels = host.split('.')
    if any(not LABEL_RE.fullmatch(label) for label in labels):
        raise ConfigError('hostname_invalid')
    return host


def validate_hostname_under_zone(hostname: str, zone: str) -> Tuple[str, str]:
    host = normalize_hostname(hostname)
    zone_name = normalize_hostname(zone)
    if host == zone_name:
        raise ConfigError('apex_hostname_forbidden')
    suffix = '.' + zone_name
    if not host.endswith(suffix):
        raise ConfigError('hostname_outside_zone')
    relative = host[: -len(suffix)]
    if not relative or '.' in relative:
        raise ConfigError('hostname_not_single_subdomain')
    if relative in RESERVED_LABELS:
        raise ConfigError('reserved_hostname_forbidden')
    return host, zone_name


def config_path(source: Path) -> Path:
    root = Path(source).resolve()
    path = (root / CONFIG_FILENAME).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ConfigError('config_path_escape') from exc
    if path.is_symlink() or (root / CONFIG_FILENAME).is_symlink():
        raise ConfigError('config_symlink_forbidden')
    if not path.is_file() or path.is_symlink():
        raise ConfigError('config_missing')
    return path


def parse_mapping(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicates)
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError('config_json_invalid') from exc
    if not isinstance(data, dict):
        raise ConfigError('config_not_object')
    return data


def validate_mapping(data: Mapping[str, Any]) -> Dict[str, Any]:
    unknown = [k for k in data.keys() if k not in ALLOWED_KEYS]
    missing = [k for k in ALLOWED_KEYS if k not in data]
    if unknown:
        raise ConfigError('config_unknown_key')
    if missing:
        raise ConfigError('config_missing_key')
    if len(list(data.keys())) != len(ALLOWED_KEYS):
        raise ConfigError('config_duplicate_key')
    version = data['schema_version']
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise ConfigError('config_schema_version_invalid')
    auto_apply = data['auto_apply']
    if not isinstance(auto_apply, bool):
        raise ConfigError('config_auto_apply_not_bool')

    hostname = data['hostname']
    zone = data['cloudflare_zone_name']
    if hostname is not None and not isinstance(hostname, str):
        raise ConfigError('hostname_invalid')
    if zone is not None and not isinstance(zone, str):
        raise ConfigError('zone_invalid')
    if (hostname is None) != (zone is None):
        raise ConfigError('hostname_zone_pair_required')
    if hostname is not None:
        hostname, zone = validate_hostname_under_zone(hostname, zone)

    expected = data['expected_project_id']
    confirm = data['confirm_repository_id']
    if expected is not None and not isinstance(expected, str):
        raise ConfigError('expected_project_id_invalid')
    if confirm is not None and not isinstance(confirm, str):
        raise ConfigError('confirm_repository_id_invalid')
    if (expected is None) != (confirm is None):
        raise ConfigError('recovery_pair_required')
    if expected is not None:
        if not PROJECT_ID_RE.fullmatch(expected):
            raise ConfigError('expected_project_id_invalid')
        if not REPOSITORY_ID_RE.fullmatch(confirm):
            raise ConfigError('confirm_repository_id_invalid')

    if auto_apply and hostname is None:
        raise ConfigError('hostname_required_for_auto_apply')

    return {
        'schema_version': SCHEMA_VERSION,
        'hostname': hostname,
        'cloudflare_zone_name': zone,
        'auto_apply': auto_apply,
        'expected_project_id': expected,
        'confirm_repository_id': confirm,
    }


def _git(args, source: Path) -> str:
    try:
        result = subprocess.run(
            args, cwd=source, capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if k in ('PATH', 'HOME', 'TMPDIR', 'GIT_DIR', 'GIT_WORK_TREE')})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError('config_git_failed') from exc
    if result.returncode:
        raise ConfigError('config_git_failed')
    return result.stdout


def assert_tracked_clean_head(source: Path, expected_sha: Optional[str] = None) -> str:
    """Require config tracked at the exact clean HEAD (default-branch checkout)."""
    root = Path(source).resolve()
    path = config_path(root)
    head = _git(['git', 'rev-parse', 'HEAD'], root).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ConfigError('config_head_invalid')
    if expected_sha is not None and head != expected_sha:
        raise ConfigError('config_source_sha_mismatch')
    porcelain = _git(['git', 'status', '--porcelain', '--untracked-files=all'], root)
    if porcelain.strip():
        raise ConfigError('config_source_dirty')
    tracked = _git(['git', 'ls-files', '--error-unmatch', CONFIG_FILENAME], root).strip()
    if tracked != CONFIG_FILENAME:
        raise ConfigError('config_not_tracked')
    blob = _git(['git', 'show', 'HEAD:' + CONFIG_FILENAME], root)
    working = path.read_text(encoding='utf-8')
    if blob != working:
        raise ConfigError('config_working_tree_mismatch')
    # Reject if the path in the index is a symlink (git ls-files -s mode 120000).
    ls = _git(['git', 'ls-files', '-s', CONFIG_FILENAME], root).strip()
    if not ls or ls.split(None, 1)[0] == '120000':
        raise ConfigError('config_symlink_forbidden')
    return head


def load_onboarding_config(
    source: Path,
    *,
    require_tracked_clean: bool = True,
    expected_sha: Optional[str] = None,
) -> Dict[str, Any]:
    root = Path(source).resolve()
    path = config_path(root)
    if require_tracked_clean:
        assert_tracked_clean_head(root, expected_sha=expected_sha)
    raw = path.read_text(encoding='utf-8')
    return validate_mapping(parse_mapping(raw))


def auto_policy_enabled(config: Mapping[str, Any]) -> bool:
    return bool(config.get('auto_apply') is True and config.get('hostname'))

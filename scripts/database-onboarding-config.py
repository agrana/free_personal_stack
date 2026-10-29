#!/usr/bin/env python3
"""Closed-schema loader for opt-in database catalog preflight policy.

Tracked database-onboarding.config.json enables managed-service read-only
preflight for a reviewed app namespace. Template defaults keep enabled false
and namespace null. Missing file means unconfigured/disabled for older
generated apps (no network). A present malformed file fails closed.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Mapping, Optional

CONFIG_FILENAME = 'database-onboarding.config.json'
SCHEMA_VERSION = 1
ALLOWED_KEYS = (
    'schema_version',
    'enabled',
    'namespace',
)


class ConfigError(Exception):
    """Raised with a stable machine-readable status string."""


def _load_migrate():
    import sys
    path = Path(__file__).with_name('migrate_app_namespace.py')
    spec = importlib.util.spec_from_file_location('migrate_app_namespace_dbcfg', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _reject_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ConfigError('config_duplicate_key')
        seen[key] = value
    return seen


def config_path(source: Path, *, allow_missing: bool = False) -> Optional[Path]:
    root = Path(source).resolve()
    candidate = root / CONFIG_FILENAME
    if candidate.is_symlink() or candidate.resolve().is_symlink():
        raise ConfigError('config_symlink_forbidden')
    try:
        path = candidate.resolve()
        path.relative_to(root)
    except ValueError as exc:
        raise ConfigError('config_path_escape') from exc
    if not path.exists():
        if allow_missing:
            return None
        raise ConfigError('config_missing')
    if path.is_symlink() or not path.is_file():
        raise ConfigError('config_symlink_forbidden')
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
    enabled = data['enabled']
    if not isinstance(enabled, bool):
        raise ConfigError('config_enabled_not_bool')
    namespace = data['namespace']
    if enabled:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ConfigError('namespace_required_when_enabled')
        migrate = _load_migrate()
        try:
            namespace = migrate.validate_namespace(namespace.strip())
        except ValueError as exc:
            raise ConfigError('config_namespace_invalid') from exc
    else:
        if namespace is not None:
            raise ConfigError('config_namespace_must_be_null_when_disabled')
    return {
        'schema_version': SCHEMA_VERSION,
        'enabled': enabled,
        'namespace': namespace,
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


def read_config_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise ConfigError('config_read_failed') from None


def assert_tracked_clean_head(source: Path, expected_sha: Optional[str] = None) -> str:
    root = Path(source).resolve()
    path = config_path(root, allow_missing=False)
    assert path is not None
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
    working = read_config_text(path)
    if blob != working:
        raise ConfigError('config_working_tree_mismatch')
    ls = _git(['git', 'ls-files', '-s', CONFIG_FILENAME], root).strip()
    if not ls or ls.split(None, 1)[0] == '120000':
        raise ConfigError('config_symlink_forbidden')
    return head


def disabled_unconfigured() -> Dict[str, Any]:
    return {
        'schema_version': SCHEMA_VERSION,
        'enabled': False,
        'namespace': None,
        'config_status': 'unconfigured',
    }


def load_database_onboarding_config(
    source: Path,
    *,
    require_tracked_clean: bool = True,
    expected_sha: Optional[str] = None,
) -> Dict[str, Any]:
    root = Path(source).resolve()
    path = config_path(root, allow_missing=True)
    if path is None:
        return disabled_unconfigured()
    if require_tracked_clean:
        assert_tracked_clean_head(root, expected_sha=expected_sha)
    raw = read_config_text(path)
    validated = validate_mapping(parse_mapping(raw))
    validated['config_status'] = 'configured'
    return validated


def preflight_enabled(config: Mapping[str, Any]) -> bool:
    return bool(config.get('enabled') is True and config.get('namespace'))

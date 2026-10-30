#!/usr/bin/env python3
"""Closed-schema loader for opt-in managed database delivery policy.

Tracked database-delivery.config.json authorizes merge-time credential
bootstrap and app-scoped migrations against the repository Supabase target.
Template defaults keep enabled and auto_apply false. Missing file means
unconfigured/disabled for older generated apps (no network). A present
malformed file fails closed. Enabling live writes in a generated app requires
a separately reviewed config PR; this loader never performs provider calls.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

CONFIG_FILENAME = 'database-delivery.config.json'
SCHEMA_VERSION = 1
ALLOWED_KEYS = (
    'schema_version',
    'enabled',
    'auto_apply',
    'namespace',
    'migrations_path',
    'expected_project_ref',
    'rotate_migrator',
)
MIGRATIONS_PATH_RE = re.compile(r'^[a-z0-9][a-z0-9._/-]{0,198}$')
PROJECT_REF_RE = re.compile(r'^[a-z0-9]+$')
MIGRATION_ID_RE = re.compile(r'^[0-9]{4}_[a-z0-9_]+$')
# Keep a minimal clean env for Git; never honor foreign GIT_DIR / GIT_WORK_TREE.
_GIT_ENV_ALLOW = frozenset({'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE'})


class ConfigError(Exception):
    """Raised with a stable machine-readable status string."""


def _load_mdb():
    path = Path(__file__).with_name('managed_database.py')
    spec = importlib.util.spec_from_file_location('managed_database_delivery_cfg', path)
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


def _reject_symlink_ancestors(root: Path, relative: str) -> Path:
    """Reject symlink ancestors before resolve(), including same-repo links."""
    text = relative.strip().replace('\\', '/')
    current = root
    for part in text.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise ConfigError('config_migrations_path_invalid')
        current = current / part
        try:
            if current.is_symlink():
                raise ConfigError('config_migrations_symlink_forbidden')
        except ConfigError:
            raise
        except OSError:
            raise ConfigError('config_migrations_path_missing') from None
    return current


def _validate_migrations_path(root: Path, relative: str) -> str:
    if not isinstance(relative, str) or not relative.strip():
        raise ConfigError('config_migrations_path_invalid')
    text = relative.strip().replace('\\', '/')
    if text.startswith('/') or text.startswith('../') or '/../' in text or text.endswith('/..'):
        raise ConfigError('config_migrations_path_invalid')
    if '..' in text.split('/'):
        raise ConfigError('config_migrations_path_invalid')
    if not MIGRATIONS_PATH_RE.fullmatch(text):
        raise ConfigError('config_migrations_path_invalid')
    candidate = _reject_symlink_ancestors(root, text)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except FileNotFoundError as exc:
        raise ConfigError('config_migrations_path_missing') from exc
    except ValueError as exc:
        raise ConfigError('config_migrations_path_escape') from exc
    if resolved.is_symlink():
        raise ConfigError('config_migrations_symlink_forbidden')
    if not resolved.is_dir():
        raise ConfigError('config_migrations_path_missing')
    return text


def validate_mapping(data: Mapping[str, Any], *, source: Optional[Path] = None) -> Dict[str, Any]:
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
    auto_apply = data['auto_apply']
    rotate_migrator = data['rotate_migrator']
    if not isinstance(enabled, bool):
        raise ConfigError('config_enabled_not_bool')
    if not isinstance(auto_apply, bool):
        raise ConfigError('config_auto_apply_not_bool')
    if not isinstance(rotate_migrator, bool):
        raise ConfigError('config_rotate_migrator_not_bool')
    if not enabled and auto_apply:
        raise ConfigError('config_auto_apply_requires_enabled')
    if not enabled and rotate_migrator:
        raise ConfigError('config_rotate_migrator_requires_enabled')

    namespace = data['namespace']
    migrations_path = data['migrations_path']
    expected_project_ref = data['expected_project_ref']

    if not enabled:
        if namespace is not None:
            raise ConfigError('config_namespace_must_be_null_when_disabled')
        if migrations_path is not None:
            raise ConfigError('config_migrations_path_must_be_null_when_disabled')
        if expected_project_ref is not None:
            raise ConfigError('config_project_ref_must_be_null_when_disabled')
        return {
            'schema_version': SCHEMA_VERSION,
            'enabled': False,
            'auto_apply': False,
            'namespace': None,
            'migrations_path': None,
            'expected_project_ref': None,
            'rotate_migrator': False,
        }

    mdb = _load_mdb()
    if not isinstance(namespace, str) or not namespace.strip():
        raise ConfigError('namespace_required_when_enabled')
    try:
        namespace = mdb.validate_namespace(namespace.strip())
    except mdb.ManagedDatabaseError as exc:
        raise ConfigError('config_namespace_invalid') from exc

    if source is None:
        raise ConfigError('config_source_required_when_enabled')
    root = Path(source).resolve()
    migrations_path = _validate_migrations_path(root, migrations_path)

    if not isinstance(expected_project_ref, str) or not PROJECT_REF_RE.fullmatch(
            expected_project_ref.strip()):
        raise ConfigError('config_project_ref_invalid')
    expected_project_ref = expected_project_ref.strip()
    try:
        mdb.validate_project_ref(expected_project_ref)
    except mdb.ManagedDatabaseError as exc:
        raise ConfigError('config_project_ref_invalid') from exc

    return {
        'schema_version': SCHEMA_VERSION,
        'enabled': True,
        'auto_apply': auto_apply,
        'namespace': namespace,
        'migrations_path': migrations_path,
        'expected_project_ref': expected_project_ref,
        'rotate_migrator': rotate_migrator,
    }


def _git_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _GIT_ENV_ALLOW}
    # Explicitly drop foreign Git directory overrides even if somehow present.
    env.pop('GIT_DIR', None)
    env.pop('GIT_WORK_TREE', None)
    env.pop('GIT_COMMON_DIR', None)
    env.pop('GIT_OBJECT_DIRECTORY', None)
    env.pop('GIT_INDEX_FILE', None)
    return env


def _git(args, source: Path) -> str:
    try:
        result = subprocess.run(
            args, cwd=source, capture_output=True, text=True, timeout=30,
            env=_git_env())
    except (OSError, subprocess.TimeoutExpired):
        raise ConfigError('config_git_failed') from None
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


def capture_approved_migrations(
    source: Path,
    migrations_path: str,
    *,
    expected_sha: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Capture migration IDs/modes/bytes from the approved commit tree.

    Provenance is the immutable tree at expected_sha (or HEAD when omitted),
    never the mutable index. Working-tree paths/modes/bytes must match that
    tree; staged/uncommitted extras or content drift fail closed. Callers
    must execute these captured bytes, not re-read files later.
    """
    root = Path(source).resolve()
    head = _git(['git', 'rev-parse', 'HEAD'], root).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ConfigError('config_head_invalid')
    if expected_sha is not None:
        if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
            raise ConfigError('config_source_sha_mismatch')
        if head != expected_sha:
            raise ConfigError('config_source_sha_mismatch')
        approved_sha = expected_sha
    else:
        approved_sha = head

    rel = _validate_migrations_path(root, migrations_path)
    mig_dir = root / rel

    # Enumerate working-tree *.sql including ignored files (not via porcelain).
    working_sql: List[Path] = []
    try:
        for entry in sorted(mig_dir.iterdir()):
            if entry.name.endswith('.sql'):
                working_sql.append(entry)
    except OSError:
        raise ConfigError('config_migrations_path_missing') from None

    for path in working_sql:
        if path.is_symlink():
            raise ConfigError('config_migrations_symlink_forbidden')
        # Reject symlink ancestors on each SQL path before resolve.
        _reject_symlink_ancestors(root, f'{rel}/{path.name}')

    # Immutable approved-tree enumeration (not git ls-files --stage / INDEX).
    tree_lines = _git(
        ['git', 'ls-tree', '-rz', '--full-tree', approved_sha, '--', rel], root
    )
    tracked: Dict[str, Tuple[str, str]] = {}
    if tree_lines:
        # ls-tree -z: <mode> SP <type> SP <object> TAB <file>\0
        for part in tree_lines.split('\0'):
            if not part.strip():
                continue
            try:
                meta, path = part.split('\t', 1)
                mode, obj_type, blob_sha = meta.split(None, 2)
            except ValueError:
                raise ConfigError('config_migrations_git_invalid') from None
            if obj_type != 'blob' or not path.endswith('.sql'):
                continue
            name = Path(path).name
            if name in tracked:
                raise ConfigError('config_migrations_duplicate')
            tracked[name] = (mode, blob_sha)

    working_names = {p.name for p in working_sql}
    tracked_names = set(tracked)
    extras = working_names - tracked_names
    if extras:
        # Ignored, untracked, or index-only SQL present under migrations_path.
        raise ConfigError('config_migrations_extra_sql')
    missing = tracked_names - working_names
    if missing:
        raise ConfigError('config_migrations_missing_sql')

    manifest: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name in sorted(tracked_names):
        mode, blob_sha = tracked[name]
        if mode != '100644' and mode != '100755':
            # Symlink mode 120000 or unexpected modes are forbidden.
            raise ConfigError('config_migrations_symlink_forbidden')
        migration_id = Path(name).stem
        if not MIGRATION_ID_RE.fullmatch(migration_id):
            raise ConfigError('config_migrations_id_invalid')
        if migration_id in seen_ids:
            raise ConfigError('config_migrations_duplicate')
        seen_ids.add(migration_id)
        relative_path = f'{rel}/{name}'
        try:
            result = subprocess.run(
                ['git', 'cat-file', '-p', blob_sha],
                cwd=root, capture_output=True, timeout=30, env=_git_env())
        except (OSError, subprocess.TimeoutExpired):
            raise ConfigError('config_git_failed') from None
        if result.returncode:
            raise ConfigError('config_git_failed')
        blob = result.stdout
        working_path = mig_dir / name
        try:
            working_bytes = working_path.read_bytes()
        except OSError:
            raise ConfigError('config_migrations_read_failed') from None
        if working_bytes != blob:
            raise ConfigError('config_migrations_working_tree_mismatch')
        st = working_path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ConfigError('config_migrations_symlink_forbidden')
        # Compare executable bit against approved tree mode (not index stage).
        working_exec = bool(st.st_mode & stat.S_IXUSR)
        tree_exec = mode == '100755'
        if working_exec != tree_exec:
            raise ConfigError('config_migrations_working_tree_mismatch')
        manifest.append({
            'migration_id': migration_id,
            'relative_path': relative_path,
            'mode': mode,
            'git_sha': blob_sha,
            'sql_bytes': blob,
            'checksum_sha256': hashlib.sha256(blob).hexdigest(),
            'head_sha': approved_sha,
        })
    manifest.sort(key=lambda item: item['migration_id'])
    return manifest


def disabled_unconfigured() -> Dict[str, Any]:
    return {
        'schema_version': SCHEMA_VERSION,
        'enabled': False,
        'auto_apply': False,
        'namespace': None,
        'migrations_path': None,
        'expected_project_ref': None,
        'rotate_migrator': False,
        'config_status': 'unconfigured',
    }


def load_database_delivery_config(
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
    validated = validate_mapping(parse_mapping(raw), source=root)
    validated['config_status'] = 'configured'
    return validated


def delivery_enabled(config: Mapping[str, Any]) -> bool:
    return bool(config.get('enabled') is True and config.get('namespace'))


def delivery_auto_apply(config: Mapping[str, Any]) -> bool:
    return delivery_enabled(config) and config.get('auto_apply') is True


def workflow_delivery_deps_required(source: Path) -> bool:
    """Central workflow gate: True only when reviewed config enables delivery."""
    root = Path(source).resolve()
    path = config_path(root, allow_missing=True)
    if path is None:
        return False
    raw = read_config_text(path)
    data = parse_mapping(raw)
    # Validate closed schema; enabled configs need migrations path present.
    if data.get('enabled') is True:
        validate_mapping(data, source=root)
        return True
    validate_mapping(data, source=None)
    return False


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ['--workflow-deps-required']:
        try:
            required = workflow_delivery_deps_required(Path.cwd())
        except ConfigError:
            return 2
        return 10 if required else 0
    sys.stderr.write('usage: database-delivery-config.py --workflow-deps-required\n')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

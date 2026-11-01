#!/usr/bin/env python3
"""Read-only managed-service database catalog preflight (stdlib network client).

Inspects an approved Supabase project through the documented beta Management API
read-only SQL endpoint. Never applies migrations, never uses the write-capable
query endpoint, and never claims database_ready / live_migrations_authorized /
full_app_ready. Catalog facts are observations — not proof that managed
provisioning execute permissions or app-data connectivity work.

Credentials arrive only via environment variables (never argv). Transport is
injectable for offline tests.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence, Tuple

PLACEHOLDER = 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
API_HOST = 'api.supabase.com'
READ_ONLY_PATH_TMPL = '/v1/projects/{ref}/database/query/read-only'
FORBIDDEN_WRITE_PATH = '/database/query'
REQUEST_TIMEOUT_SEC = 30
MAX_RESPONSE_BYTES = 200_000
PROJECT_REF_RE = re.compile(r'^[a-z0-9]+$')
SUPABASE_URL_RE = re.compile(r'^https://([a-z0-9]+)\.supabase\.co$')

# Fixed SELECT-only catalog inspection. Namespace is bound as $1 only — never
# interpolated into this string by Python.
CATALOG_SQL = """
SELECT pg_catalog.jsonb_build_object(
  'server_version_num', (
    SELECT CAST(pg_catalog.current_setting('server_version_num') AS integer)
  ),
  'observer', pg_catalog.jsonb_build_object(
    'rolsuper', observer.rolsuper,
    'rolcreaterole', observer.rolcreaterole,
    'rolbypassrls', observer.rolbypassrls
  ),
  'bootstrap_candidate', pg_catalog.jsonb_build_object(
    'present', candidate.oid IS NOT NULL,
    'rolsuper', COALESCE(candidate.rolsuper, false),
    'rolcreaterole', COALESCE(candidate.rolcreaterole, false),
    'rolbypassrls', COALESCE(candidate.rolbypassrls, false)
  ),
  'namespace', pg_catalog.jsonb_build_object(
    'present', ns.oid IS NOT NULL,
    'expected_owner_match', CASE
      WHEN ns.oid IS NULL OR owner_role.oid IS NULL THEN false
      ELSE ns.nspowner = owner_role.oid
    END,
    'collision', CASE
      WHEN ns.oid IS NULL THEN false
      WHEN owner_role.oid IS NULL THEN true
      ELSE ns.nspowner IS DISTINCT FROM owner_role.oid
    END
  ),
  'roles', pg_catalog.jsonb_build_object(
    'owner', pg_catalog.jsonb_build_object(
      'present', owner_role.oid IS NOT NULL,
      'rolsuper', COALESCE(owner_role.rolsuper, false),
      'rolcreaterole', COALESCE(owner_role.rolcreaterole, false),
      'rolbypassrls', COALESCE(owner_role.rolbypassrls, false)
    ),
    'migrator', pg_catalog.jsonb_build_object(
      'present', migrator_role.oid IS NOT NULL,
      'rolsuper', COALESCE(migrator_role.rolsuper, false),
      'rolcreaterole', COALESCE(migrator_role.rolcreaterole, false),
      'rolbypassrls', COALESCE(migrator_role.rolbypassrls, false)
    ),
    'runtime', pg_catalog.jsonb_build_object(
      'present', runtime_role.oid IS NOT NULL,
      'rolsuper', COALESCE(runtime_role.rolsuper, false),
      'rolcreaterole', COALESCE(runtime_role.rolcreaterole, false),
      'rolbypassrls', COALESCE(runtime_role.rolbypassrls, false)
    )
  ),
  'control_schema_present', EXISTS (
    SELECT 1
    FROM pg_catalog.pg_namespace AS control_ns
    WHERE control_ns.nspname = 'app_migration_control'
  ),
  'app_tables', pg_catalog.jsonb_build_object(
    'count', COALESCE(tbl.table_count, 0),
    'rls_enabled_count', COALESCE(tbl.rls_enabled_count, 0),
    'rls_forced_count', COALESCE(tbl.rls_forced_count, 0),
    'policy_count', COALESCE(pol.policy_count, 0)
  )
) AS catalog
FROM pg_catalog.pg_roles AS observer
LEFT JOIN pg_catalog.pg_roles AS candidate ON candidate.rolname = 'postgres'
LEFT JOIN pg_catalog.pg_roles AS owner_role
  ON owner_role.rolname = ($1::text || '_owner')
LEFT JOIN pg_catalog.pg_roles AS migrator_role
  ON migrator_role.rolname = ($1::text || '_migrator')
LEFT JOIN pg_catalog.pg_roles AS runtime_role
  ON runtime_role.rolname = ($1::text || '_runtime')
LEFT JOIN pg_catalog.pg_namespace AS ns
  ON ns.nspname = $1::text
LEFT JOIN LATERAL (
  SELECT
    count(*)::integer AS table_count,
    count(*) FILTER (WHERE c.relrowsecurity)::integer AS rls_enabled_count,
    count(*) FILTER (WHERE c.relforcerowsecurity)::integer AS rls_forced_count
  FROM pg_catalog.pg_class AS c
  WHERE ns.oid IS NOT NULL
    AND c.relnamespace = ns.oid
    AND c.relkind IN ('r', 'p')
) AS tbl ON true
LEFT JOIN LATERAL (
  SELECT count(*)::integer AS policy_count
  FROM pg_catalog.pg_policy AS p
  INNER JOIN pg_catalog.pg_class AS c ON c.oid = p.polrelid
  WHERE ns.oid IS NOT NULL
    AND c.relnamespace = ns.oid
) AS pol ON true
WHERE observer.rolname = CURRENT_USER
""".strip()


class PreflightError(Exception):
    """Stable machine-readable preflight failure code."""


class Transport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> Tuple[int, bytes]:
        ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StdlibTransport:
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> Tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(request, timeout=timeout) as response:
                status = int(getattr(response, 'status', 200) or 200)
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                return status, payload
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read(MAX_RESPONSE_BYTES + 1)
            except Exception:
                payload = b''
            return int(exc.code), payload


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def project_ref_from_supabase_url(url: str) -> str:
    if not isinstance(url, str):
        raise PreflightError('supabase_url_invalid')
    text = url.strip()
    if not text or text == PLACEHOLDER:
        raise PreflightError('setup_required')
    # Exact hosted project URL only: no userinfo, path, query, fragment, or port.
    match = SUPABASE_URL_RE.fullmatch(text)
    if not match:
        raise PreflightError('supabase_url_invalid')
    ref = match.group(1)
    if not PROJECT_REF_RE.fullmatch(ref):
        raise PreflightError('supabase_url_invalid')
    return ref


def access_token_from_env(env: Optional[Mapping[str, str]] = None) -> str:
    source = os.environ if env is None else env
    raw = source.get('SUPABASE_ACCESS_TOKEN', '')
    if not isinstance(raw, str):
        raise PreflightError('setup_required')
    token = raw.strip()
    if not token or token == PLACEHOLDER:
        raise PreflightError('setup_required')
    return token


def supabase_url_from_env(env: Optional[Mapping[str, str]] = None) -> str:
    source = os.environ if env is None else env
    raw = source.get('SUPABASE_URL', '')
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == PLACEHOLDER:
        raise PreflightError('setup_required')
    return raw.strip()


def read_only_endpoint(ref: str) -> str:
    if not PROJECT_REF_RE.fullmatch(ref):
        raise PreflightError('supabase_url_invalid')
    path = READ_ONLY_PATH_TMPL.format(ref=ref)
    if FORBIDDEN_WRITE_PATH in path and not path.endswith('/read-only'):
        raise PreflightError('endpoint_invariant')
    return 'https://' + API_HOST + path


def request_body(namespace: str) -> bytes:
    payload = {
        'query': CATALOG_SQL,
        'parameters': [namespace],
    }
    return json.dumps(payload, separators=(',', ':')).encode('utf-8')


def _map_http_status(status: int) -> str:
    if status in (401, 403):
        return 'provider_auth_or_permission_denied'
    if status == 429:
        return 'provider_throttled'
    if status in (408, 502, 503, 504):
        return 'provider_unavailable'
    if 500 <= status <= 599:
        return 'provider_unavailable'
    if status in (301, 302, 303, 307, 308):
        return 'provider_redirect_rejected'
    return 'provider_http_error'


def _unwrap_catalog_row(data: Any) -> Dict[str, Any]:
    if isinstance(data, list):
        if len(data) != 1 or not isinstance(data[0], dict):
            raise PreflightError('provider_response_malformed')
        row = data[0]
        if 'catalog' in row:
            catalog = row['catalog']
        elif len(row) == 1:
            catalog = next(iter(row.values()))
        else:
            catalog = row
    elif isinstance(data, dict):
        if 'catalog' in data:
            catalog = data['catalog']
        elif 'result' in data:
            return _unwrap_catalog_row(data['result'])
        else:
            catalog = data
    else:
        raise PreflightError('provider_response_malformed')
    if isinstance(catalog, str):
        try:
            catalog = json.loads(catalog)
        except ValueError as exc:
            raise PreflightError('provider_response_malformed') from exc
    if not isinstance(catalog, dict):
        raise PreflightError('provider_response_malformed')
    return catalog


def _require_bool(obj: Mapping[str, Any], key: str) -> bool:
    value = obj.get(key)
    if not isinstance(value, bool):
        raise PreflightError('provider_response_malformed')
    return value


def _require_int(obj: Mapping[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreflightError('provider_response_malformed')
    return value


def _require_role_flags(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise PreflightError('provider_response_malformed')
    return {
        'present': _require_bool(obj, 'present'),
        'rolsuper': _require_bool(obj, 'rolsuper'),
        'rolcreaterole': _require_bool(obj, 'rolcreaterole'),
        'rolbypassrls': _require_bool(obj, 'rolbypassrls'),
    }


def normalize_catalog(raw: Mapping[str, Any]) -> Dict[str, Any]:
    version = _require_int(raw, 'server_version_num')
    if version <= 0:
        raise PreflightError('provider_response_malformed')
    observer = raw.get('observer')
    if not isinstance(observer, dict):
        raise PreflightError('provider_response_malformed')
    namespace = raw.get('namespace')
    if not isinstance(namespace, dict):
        raise PreflightError('provider_response_malformed')
    roles = raw.get('roles')
    if not isinstance(roles, dict):
        raise PreflightError('provider_response_malformed')
    app_tables = raw.get('app_tables')
    if not isinstance(app_tables, dict):
        raise PreflightError('provider_response_malformed')
    counts = {key: _require_int(app_tables, key) for key in (
        'count', 'rls_enabled_count', 'rls_forced_count', 'policy_count')}
    if (any(value < 0 for value in counts.values())
            or counts['rls_enabled_count'] > counts['count']
            or counts['rls_forced_count'] > counts['count']):
        raise PreflightError('provider_response_malformed')
    return {
        'server_version_num': version,
        'observer': {
            'rolsuper': _require_bool(observer, 'rolsuper'),
            'rolcreaterole': _require_bool(observer, 'rolcreaterole'),
            'rolbypassrls': _require_bool(observer, 'rolbypassrls'),
            'note': (
                'Observed effective-query-role catalog flags only; not proof of '
                'execute permission for managed provisioning.'
            ),
        },
        'bootstrap_candidate': {
            **_require_role_flags(raw.get('bootstrap_candidate')),
            'note': 'Catalog flags for the standard provider admin role only; '
                    'actual bootstrap credentials and execute rights remain unverified.',
        },
        'namespace': {
            'present': _require_bool(namespace, 'present'),
            'expected_owner_match': _require_bool(namespace, 'expected_owner_match'),
            'collision': _require_bool(namespace, 'collision'),
        },
        'roles': {
            'owner': _require_role_flags(roles.get('owner')),
            'migrator': _require_role_flags(roles.get('migrator')),
            'runtime': _require_role_flags(roles.get('runtime')),
        },
        'control_schema_present': _require_bool(raw, 'control_schema_present'),
        'app_tables': counts,
    }


def required_next_steps(catalog: Mapping[str, Any]) -> list:
    steps = []
    if not catalog['control_schema_present']:
        steps.append('provision_app003_control_schema')
    if catalog['namespace']['collision']:
        steps.append('resolve_namespace_ownership_collision')
    if not catalog['namespace']['present']:
        steps.append('provision_app_namespace_and_roles')
    else:
        for role_key in ('owner', 'migrator', 'runtime'):
            if not catalog['roles'][role_key]['present']:
                steps.append('provision_missing_' + role_key + '_role')
        if (catalog['namespace']['present']
                and not catalog['namespace']['expected_owner_match']
                and not catalog['namespace']['collision']):
            steps.append('align_namespace_owner')
    tables = catalog['app_tables']
    if tables['count'] > 0:
        if tables['rls_enabled_count'] < tables['count']:
            steps.append('enable_rls_on_app_tables')
        if tables['policy_count'] < 1:
            steps.append('add_explicit_rls_policies')
    steps.extend([
        'apply_reviewed_app_scoped_migrations',
        'verify_runtime_connectivity_under_rls',
        'verify_cross_app_isolation',
    ])
    return steps


def readiness_flags(*, catalog_inspection_completed: bool) -> Dict[str, Any]:
    return {
        'database_ready': False,
        'live_migrations_authorized': False,
        'full_app_ready': False,
        'catalog_inspection_completed': bool(catalog_inspection_completed),
    }


def execute_read_only_catalog(
    *,
    project_ref: str,
    access_token: str,
    namespace: str,
    transport: Transport,
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> Dict[str, Any]:
    url = read_only_endpoint(project_ref)
    if '/database/query/read-only' not in url or url.rstrip('/').endswith('/database/query'):
        raise PreflightError('endpoint_invariant')
    body = request_body(namespace)
    # SQL is the fixed constant above; namespace travels only in parameters.
    # Valid namespaces may themselves be words present in that fixed SQL.
    headers = {
        'Authorization': 'Bearer ' + access_token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    try:
        status, payload = transport.request(
            method='POST', url=url, headers=headers, body=body, timeout=timeout)
    except PreflightError:
        raise
    except Exception:
        raise PreflightError('provider_transport_error') from None
    if status in (301, 302, 303, 307, 308):
        raise PreflightError('provider_redirect_rejected')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise PreflightError('provider_response_oversized')
    if status not in (200, 201):
        raise PreflightError(_map_http_status(status))
    try:
        parsed = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise PreflightError('provider_response_malformed') from None
    catalog = normalize_catalog(_unwrap_catalog_row(parsed))
    return catalog


def build_report(
    *,
    status: str,
    namespace: Optional[str] = None,
    project_ref_present: bool = False,
    catalog: Optional[Mapping[str, Any]] = None,
    required_secret_names: Optional[Sequence[str]] = None,
    config_status: str = 'configured',
    error: Optional[str] = None,
) -> Dict[str, Any]:
    completed = status == 'catalog_inspection_completed' and catalog is not None
    report: Dict[str, Any] = {
        'status': status,
        'config_status': config_status,
        'namespace': namespace,
        'project_ref_present': bool(project_ref_present),
        'endpoint': 'POST /v1/projects/{ref}/database/query/read-only',
        'required_missing_stages': [
            'database_connection_verification',
            'app_scoped_schema_migrations',
            'grants_and_rls_isolation',
        ],
        **readiness_flags(catalog_inspection_completed=completed),
    }
    if required_secret_names is not None:
        report['required_secret_names'] = list(required_secret_names)
    if error is not None:
        report['error'] = error
    if catalog is not None:
        report['catalog'] = dict(catalog)
        report['required_next_steps'] = required_next_steps(catalog)
        report['note'] = (
            'Catalog facts were observed through the read-only Management API. '
            'They do not prove managed provisioning execute rights, applied '
            'migrations, runtime connectivity, or RLS isolation.'
        )
    return report


def run_preflight(
    source: Path,
    *,
    require_tracked_clean: bool = True,
    expected_sha: Optional[str] = None,
    require_github_context: bool = False,
    transport: Optional[Transport] = None,
    env: Optional[Mapping[str, str]] = None,
    artifact_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    cfg_mod = load('database-onboarding-config')
    env_map = dict(os.environ if env is None else env)
    try:
        config = cfg_mod.load_database_onboarding_config(
            source,
            require_tracked_clean=require_tracked_clean,
            expected_sha=expected_sha,
        )
    except cfg_mod.ConfigError as exc:
        raise PreflightError(str(exc)) from None

    if not cfg_mod.preflight_enabled(config):
        report = build_report(
            status='preflight_disabled',
            namespace=config.get('namespace'),
            config_status=config.get('config_status', 'configured'),
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    if require_github_context:
        merge = load('merge-onboarding')
        # Reuse authenticated identity / clean default-head checks; never spoof.
        try:
            merge.repository_context(source)
        except merge.Failure as exc:
            raise PreflightError(str(exc)) from None

    missing = []
    url_raw = env_map.get('SUPABASE_URL', '')
    token_raw = env_map.get('SUPABASE_ACCESS_TOKEN', '')
    if (not isinstance(url_raw, str) or not url_raw.strip()
            or url_raw.strip() == PLACEHOLDER):
        missing.append('SUPABASE_URL')
    if (not isinstance(token_raw, str) or not token_raw.strip()
            or token_raw.strip() == PLACEHOLDER):
        missing.append('SUPABASE_ACCESS_TOKEN')
    if missing:
        report = build_report(
            status='setup_required',
            namespace=config['namespace'],
            required_secret_names=sorted(missing),
            config_status=config.get('config_status', 'configured'),
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    try:
        ref = project_ref_from_supabase_url(url_raw.strip())
        token = access_token_from_env(env_map)
    except PreflightError as exc:
        if str(exc) == 'setup_required':
            report = build_report(
                status='setup_required',
                namespace=config['namespace'],
                required_secret_names=['SUPABASE_ACCESS_TOKEN'],
            )
            _maybe_write_artifact(artifact_dir, report)
            return report
        raise

    client = transport if transport is not None else StdlibTransport()
    catalog = execute_read_only_catalog(
        project_ref=ref,
        access_token=token,
        namespace=config['namespace'],
        transport=client,
    )
    report = build_report(
        status='catalog_inspection_completed',
        namespace=config['namespace'],
        project_ref_present=True,
        catalog=catalog,
        config_status=config.get('config_status', 'configured'),
    )
    _maybe_write_artifact(artifact_dir, report)
    return report


def _maybe_write_artifact(artifact_dir: Optional[Path], report: Mapping[str, Any]) -> None:
    if artifact_dir is None:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / 'database-preflight-result.json').write_text(
        json.dumps(dict(report), indent=2, sort_keys=True) + '\n', encoding='utf-8')


def run_for_merge(
    source: Path,
    *,
    expected_sha: str,
    artifact_dir: Path,
    transport: Optional[Transport] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Merge-onboarding hook: config already under clean HEAD; quality already passed."""
    try:
        return run_preflight(
            source,
            require_tracked_clean=True,
            expected_sha=expected_sha,
            require_github_context=False,
            transport=transport,
            env=env,
            artifact_dir=artifact_dir,
        )
    except PreflightError as exc:
        _maybe_write_artifact(
            artifact_dir, build_report(status='preflight_failed', error=str(exc)))
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--artifact-dir', default=None)
    parser.add_argument(
        '--require-github-context',
        action='store_true',
        help='Compatibility flag; GitHub identity checks are always enforced by the CLI',
    )
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    artifact = Path(args.artifact_dir).resolve() if args.artifact_dir else None
    try:
        report = run_preflight(
            source,
            require_tracked_clean=True,
            require_github_context=True,
            artifact_dir=artifact,
        )
    except PreflightError as exc:
        report = build_report(status='preflight_failed', error=str(exc))
        _maybe_write_artifact(artifact, report)
        print(json.dumps(report, sort_keys=True), flush=True)
        return 1
    print(json.dumps(report, sort_keys=True), flush=True)
    if report['status'] in ('preflight_disabled', 'catalog_inspection_completed', 'setup_required'):
        return 0
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except PreflightError as exc:
        print(json.dumps(build_report(status='preflight_failed', error=str(exc)),
                         sort_keys=True), flush=True)
        sys.exit(1)

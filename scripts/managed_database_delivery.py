#!/usr/bin/env python3
"""Managed database delivery orchestration using existing provider credentials.

Coordinates: reviewed delivery config, Supabase CLI login-role + session
pooler metadata, Vercel encrypted runtime custody, and managed_database
provision/migrate/runtime probe. Import performs no network or database
action.

Production callers must use execute_authorized_delivery (reviewed config +
source SHA + owned project + quality evidence). execute_delivery requires an
explicit allow_test_orchestration flag for disposable fixtures. Merge wires
owned enrollment → authorized delivery → production deploy.

Reports contain only non-secret operation/status/binding facts. Credentials
stay in memory for the job; migrator secrets are never written to Vercel and
have no durable expiry API — job-memory custody only for this slice.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

PLACEHOLDER = 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
TRUSTED_PROVISIONER_ROLE = 'postgres'
RUNTIME_ENV_KEY = 'MDB_RUNTIME_DATABASE_URL'
BOOTSTRAP_ENV_KEY = 'MDB_BOOTSTRAP_DATABASE_URL'
MARKER_PREFIX = 'TEMPLATE_REPOSITORY_ID_'
AUTH_RETRY_ATTEMPTS = 6
AUTH_RETRY_SLEEP_SECONDS = 0.5
# Auth-cache / invalid-authorization SQLSTATEs worth bounded reconnect retry.
AUTH_RETRY_SQLSTATES = frozenset({'28P01', '28000', '08P01'})
EXPECTED_DENIAL_SQLSTATE = '42501'
PASSWORD_BYTES = 32
REQUEST_TIMEOUT_SEC = 30
MAX_RESPONSE_BYTES = 2_000_000
SUPABASE_URL_RE = re.compile(r'^https://([a-z0-9]+)\.supabase\.co$')
DELIVERY_SECRETS = (
    'SUPABASE_URL',
    'SUPABASE_ACCESS_TOKEN',
    'VERCEL_API_TOKEN',
    'VERCEL_TEAM_ID',
)
PROJECT_ID_RE = re.compile(r'^prj_[A-Za-z0-9]+$')
TEAM_ID_RE = re.compile(r'^team_[A-Za-z0-9]+$')
REPO_ID_RE = re.compile(r'^[1-9][0-9]{0,18}$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
API_HOST = 'api.vercel.com'


class DeliveryError(Exception):
    def __init__(self, code, diagnostics=None):
        super().__init__(code)
        self.diagnostics = dict(diagnostics or {})

    def __repr__(self) -> str:
        return f"DeliveryError({str(self)!r})"


class Transport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
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
        body: Optional[bytes],
        timeout: float,
    ) -> Tuple[int, bytes]:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method=method)
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


@dataclass(frozen=True)
class _Secret:
    value: str

    def __repr__(self) -> str:
        return 'Secret(<redacted>)'

    def __str__(self) -> str:
        return 'Secret(<redacted>)'


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_password() -> str:
    return secrets.token_urlsafe(PASSWORD_BYTES)


def project_ref_from_supabase_url(url: str) -> str:
    if not isinstance(url, str):
        raise DeliveryError('supabase_url_invalid')
    text = url.strip()
    if not text or text == PLACEHOLDER:
        raise DeliveryError('setup_required')
    match = SUPABASE_URL_RE.fullmatch(text)
    if not match:
        raise DeliveryError('supabase_url_invalid')
    return match.group(1)


def missing_delivery_secrets(env: Optional[Mapping[str, str]] = None, *,
                             bootstrap_mode: str = 'temporary_login') -> list:
    source = os.environ if env is None else env
    missing = []
    names = DELIVERY_SECRETS + ((BOOTSTRAP_ENV_KEY,) if bootstrap_mode == 'existing_secret' else ())
    for name in names:
        raw = source.get(name, '')
        if not isinstance(raw, str) or not raw.strip() or raw.strip() == PLACEHOLDER:
            missing.append(name)
    return missing


def capability_matrix() -> Dict[str, Any]:
    """Document required existing-token capabilities (no live probing)."""
    return {
        'supabase_management': {
            'secret': 'SUPABASE_ACCESS_TOKEN',
            'required': [
                'database_write',
                'database_pooling_config_read',
            ],
            'used_endpoints': [
                'POST /v1/projects/{ref}/cli/login-role',
                'GET /v1/projects/{ref}/config/database/pooler',
            ],
            'bootstrap_modes': {
                'temporary_login': {
                    'required': ['database_write', 'database_pooling_config_read'],
                    'cli_login_post': True,
                },
                'existing_secret': {
                    'required': ['database_pooling_config_read'],
                    'bootstrap_secret': BOOTSTRAP_ENV_KEY,
                    'cli_login_post': False,
                },
            },
            'never': [
                'DELETE /v1/projects/{ref}/cli/login-role',
                'project postgres password reset',
                'Management API arbitrary SQL migration',
            ],
        },
        'vercel': {
            'secrets': ['VERCEL_API_TOKEN', 'VERCEL_TEAM_ID'],
            'required': [
                'owned project env inventory',
                'encrypted production env create',
                'encrypted env value read',
                'production deploy',
            ],
            'runtime_env_key': RUNTIME_ENV_KEY,
            'never': [
                'NEXT_PUBLIC runtime DB secrets',
                'migrator/admin/Management credentials in app env',
                'sensitive write-only retrieval claims',
            ],
        },
        'github': {
            'required': [
                'repository PR/config merge rights',
                'Actions secrets configuration by operators',
            ],
            'workflow_token_limitation': (
                'GITHUB_TOKEN is contents/actions read-only and cannot persist '
                'repository Secrets'
            ),
        },
        'migrator_custody': {
            'durable_expiry': False,
            'policy': (
                'Migrator passwords are job-memory only for this slice; no '
                'provider expiry API is claimed. rotate_migrator explicitly '
                'rotates the owned migrator LOGIN after invariants verify.'
            ),
        },
        'not_routine_authority': [
            'SUPABASE_SERVICE_ROLE_KEY',
            'SUPABASE_DB_URL',
            'DB_PASSWORD',
        ],
    }


def build_report(**fields: Any) -> Dict[str, Any]:
    report = {
        'status': fields.get('status', 'unknown'),
        'database_ready': False,
        'live_migrations_authorized': False,
        'full_app_ready': False,
        'capability_matrix': capability_matrix(),
    }
    for key, value in fields.items():
        if key == 'status':
            continue
        if key in ('password', 'value', 'dsn', 'secret', 'access_token', 'sql_bytes'):
            continue
        report[key] = value
    return report


def sanitize_exception_text(text: str, secrets_to_redact: Sequence[str]) -> str:
    sanitized = text
    for secret in secrets_to_redact:
        if secret and secret in sanitized:
            sanitized = sanitized.replace(secret, '<redacted>')
    return sanitized


def _maybe_write_artifact(artifact_dir: Optional[Path], report: Mapping[str, Any]) -> None:
    if artifact_dir is None:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in report.items() if k not in ('value', 'sql_bytes')}
    (path / 'database-delivery-result.json').write_text(
        json.dumps(dict(safe), indent=2, sort_keys=True) + '\n', encoding='utf-8')


def connect_with_retries(dsn: str, *, attempts: int = AUTH_RETRY_ATTEMPTS):
    """Connect with bounded retries for Supavisor auth-cache lag only.

    Retries establishing a connection on auth-related SQLSTATE. Never retries
    migration execution after a possibly committed statement.
    """
    import psycopg

    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            conn = psycopg.connect(dsn)
            conn.autocommit = True
            return conn
        except Exception as exc:  # noqa: BLE001 — map to public code below
            last = exc
            sqlstate = getattr(exc, 'sqlstate', None)
            retryable = sqlstate in AUTH_RETRY_SQLSTATES or sqlstate is None
            if attempt < attempts and retryable:
                time.sleep(AUTH_RETRY_SLEEP_SECONDS * attempt)
                continue
            break
    # Never include DSN/password material from the exception chain.
    raise DeliveryError('database_auth_failed') from None


def assume_trusted_provisioner(conn, *, role: str = TRUSTED_PROVISIONER_ROLE) -> None:
    from psycopg import sql as psql

    conn.execute(psql.SQL('SET ROLE {}').format(psql.Identifier(role)))
    current = conn.execute('SELECT current_user').fetchone()[0]
    if current != role:
        raise DeliveryError('provisioner_delegation_failed')


def compute_delivery_binding_digest(
    *,
    config: Mapping[str, Any],
    repository_id: str,
    vercel_project_id: str,
    source_sha: str,
    migration_checksums: Optional[Sequence[str]] = None,
) -> str:
    """Non-secret reviewed delivery state for deployment reuse binding."""
    payload = {
        'source_sha': source_sha,
        'repository_id': str(repository_id),
        'vercel_project_id': vercel_project_id,
        'namespace': config.get('namespace'),
        'migrations_path': config.get('migrations_path'),
        'expected_project_ref': config.get('expected_project_ref'),
        'rotate_migrator': bool(config.get('rotate_migrator')),
        'bootstrap_mode': config.get('bootstrap_mode', 'temporary_login'),
        'migration_checksums': list(migration_checksums or ()),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    ).hexdigest()


def assert_quality_evidence(
    quality: Mapping[str, Any],
    *,
    expected_sha: str,
    repository_id: str,
    repository_full_name: str,
) -> Dict[str, Any]:
    """Bind authoritative exact-head quality evidence (no caller boolean)."""
    if not isinstance(quality, Mapping):
        raise DeliveryError('quality_evidence_invalid')
    if quality.get('status') != 'quality_gate_passed':
        raise DeliveryError('quality_evidence_not_passed')
    if quality.get('head_sha') != expected_sha:
        raise DeliveryError('quality_evidence_sha_mismatch')
    if str(quality.get('repository_id')) != str(repository_id):
        raise DeliveryError('quality_evidence_repository_mismatch')
    if quality.get('repository_full_name') != repository_full_name:
        raise DeliveryError('quality_evidence_repository_mismatch')
    for key in ('workflow_id', 'run_id', 'run_attempt'):
        value = quality.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DeliveryError('quality_evidence_invalid')
    return {
        'status': 'quality_gate_passed',
        'head_sha': expected_sha,
        'workflow_id': quality['workflow_id'],
        'run_id': quality['run_id'],
        'run_attempt': quality['run_attempt'],
        'repository_id': str(repository_id),
        'repository_full_name': repository_full_name,
        'path': quality.get('path'),
        'event': quality.get('event'),
        'head_branch': quality.get('head_branch'),
    }


def _vercel_transport_get(
    transport: Transport,
    *,
    path: str,
    token: str,
) -> Dict[str, Any]:
    if not path.startswith('/') or path.startswith('//'):
        raise DeliveryError('vercel_path_invalid')
    url = f'https://{API_HOST}{path}'
    status, body = transport.request(
        method='GET',
        url=url,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
        body=None,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    if status in (401, 403):
        raise DeliveryError('provider_auth_or_permission_denied')
    if status != 200:
        raise DeliveryError('vercel_ownership_read_failed')
    if len(body) > MAX_RESPONSE_BYTES:
        raise DeliveryError('vercel_response_bound')
    try:
        data = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise DeliveryError('vercel_ownership_read_failed') from None
    if not isinstance(data, dict):
        raise DeliveryError('vercel_ownership_read_failed')
    return data


def verify_owned_vercel_project(
    *,
    vercel_project_id: str,
    team_id: str,
    token: str,
    repository_id: str,
    project_name: str,
    transport: Transport,
) -> Dict[str, Any]:
    """GET owned project/team/name/marker — never trust a caller boolean."""
    if not PROJECT_ID_RE.fullmatch(vercel_project_id):
        raise DeliveryError('vercel_project_id_invalid')
    if not TEAM_ID_RE.fullmatch(team_id):
        raise DeliveryError('vercel_team_id_invalid')
    if not REPO_ID_RE.fullmatch(repository_id):
        raise DeliveryError('repository_id_invalid')
    query = urllib.parse.urlencode({'teamId': team_id})
    project = _vercel_transport_get(
        transport,
        path=f'/v9/projects/{vercel_project_id}?{query}',
        token=token,
    )
    if (project.get('id') != vercel_project_id
            or project.get('name') != project_name
            or project.get('accountId') != team_id):
        raise DeliveryError('project_ownership_mismatch')
    envs_payload = _vercel_transport_get(
        transport,
        path=f'/v9/projects/{vercel_project_id}/env?{query}',
        token=token,
    )
    envs = envs_payload.get('envs')
    if not isinstance(envs, list):
        raise DeliveryError('repository_marker_missing')
    marker_key = MARKER_PREFIX + repository_id
    candidates = [
        e for e in envs
        if isinstance(e, dict) and str(e.get('key', '')).startswith(MARKER_PREFIX.rstrip('_'))
    ]
    if any(e.get('key') != marker_key for e in candidates):
        raise DeliveryError('repository_marker_conflict')
    matches = [e for e in candidates if 'production' in (e.get('target') or [])]
    if (len(matches) != 1
            or matches[0].get('type') != 'encrypted'
            or matches[0].get('visibility') != 'config'):
        raise DeliveryError('repository_marker_missing')
    return {
        'project_id': vercel_project_id,
        'team_id': team_id,
        'project_name': project_name,
        'repository_id': repository_id,
        'marker_key': marker_key,
    }


def authorize_delivery_context(
    *,
    source: Path,
    expected_sha: str,
    repository_id: str,
    vercel_project_id: str,
    env: Mapping[str, str],
    transport: Transport,
    quality_evidence: Optional[Mapping[str, Any]] = None,
    quality_waiter: Optional[Callable[..., Mapping[str, Any]]] = None,
    github_context_fn: Optional[Callable[[Path], Tuple[str, str, str, str]]] = None,
) -> Dict[str, Any]:
    """Reuse merge/launch ownership policy before custody or CLI login."""
    if not isinstance(expected_sha, str) or not SHA_RE.fullmatch(expected_sha):
        raise DeliveryError('source_sha_invalid')
    if not isinstance(repository_id, str) or not REPO_ID_RE.fullmatch(repository_id):
        raise DeliveryError('repository_id_invalid')
    if not isinstance(vercel_project_id, str) or not PROJECT_ID_RE.fullmatch(vercel_project_id):
        raise DeliveryError('vercel_project_id_invalid')
    env_rid = env.get('GITHUB_REPOSITORY_ID') or os.environ.get('GITHUB_REPOSITORY_ID', '')
    env_sha = env.get('GITHUB_SHA') or os.environ.get('GITHUB_SHA', '')
    if env_rid != repository_id:
        raise DeliveryError('repository_id_mismatch')
    if env_sha != expected_sha:
        raise DeliveryError('source_sha_mismatch')
    merge = load('merge-onboarding')
    context_fn = github_context_fn or merge.repository_context
    # Temporarily overlay env for helpers that read os.environ.
    overlay = {
        'GITHUB_REPOSITORY_ID': repository_id,
        'GITHUB_SHA': expected_sha,
        'GITHUB_REPOSITORY': env.get('GITHUB_REPOSITORY') or os.environ.get('GITHUB_REPOSITORY', ''),
        'GITHUB_REF': env.get('GITHUB_REF') or os.environ.get('GITHUB_REF', ''),
        'GITHUB_EVENT_NAME': env.get('GITHUB_EVENT_NAME') or os.environ.get('GITHUB_EVENT_NAME', ''),
        'GITHUB_TOKEN': env.get('GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN', ''),
    }
    saved = {k: os.environ.get(k) for k in overlay}
    try:
        for key, value in overlay.items():
            if value:
                os.environ[key] = value
        def _reraise_context_failure(exc: BaseException) -> None:
            # Injected fixtures may raise a separately loaded Failure class.
            if isinstance(exc, merge.Failure) or type(exc).__name__ == 'Failure':
                raise DeliveryError(str(exc)) from None
            raise exc

        try:
            rid, repo, sha, branch = context_fn(Path(source))
        except Exception as exc:
            _reraise_context_failure(exc)
        if rid != repository_id or sha != expected_sha:
            raise DeliveryError('source_binding_mismatch')
        # Caller quality_evidence is a binding hint only. Always revalidate via
        # the authoritative newest exact-head selector before provider/DB writes.
        waiter = quality_waiter or merge.wait_for_quality_gate
        try:
            authoritative = waiter(repo, sha, branch, rid, repo)
        except Exception as exc:
            _reraise_context_failure(exc)
        quality = assert_quality_evidence(
            authoritative,
            expected_sha=expected_sha,
            repository_id=repository_id,
            repository_full_name=repo,
        )
        if quality_evidence is not None:
            if not isinstance(quality_evidence, Mapping):
                raise DeliveryError('quality_evidence_invalid')
            if (quality_evidence.get('status') != 'quality_gate_passed'
                    or quality_evidence.get('head_sha') != expected_sha
                    or str(quality_evidence.get('repository_id')) != str(repository_id)
                    or quality_evidence.get('repository_full_name') != repo
                    or quality_evidence.get('run_id') != quality['run_id']
                    or quality_evidence.get('run_attempt') != quality['run_attempt']):
                raise DeliveryError('quality_evidence_stale_or_forged')
    finally:
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
    team_id = env.get('VERCEL_TEAM_ID', '')
    token = env.get('VERCEL_API_TOKEN', '')
    if not TEAM_ID_RE.fullmatch(team_id):
        raise DeliveryError('vercel_team_id_invalid')
    if not token or token.strip() == PLACEHOLDER:
        raise DeliveryError('setup_required')
    launch = load('launch-app')
    try:
        project_name = launch.canonical_vercel_project_name(repo.split('/')[1])
    except launch.Failure as exc:
        raise DeliveryError(str(exc)) from None
    ownership = verify_owned_vercel_project(
        vercel_project_id=vercel_project_id,
        team_id=team_id,
        token=token,
        repository_id=repository_id,
        project_name=project_name,
        transport=transport,
    )
    return {
        'repository_id': rid,
        'repository_full_name': repo,
        'source_sha': sha,
        'default_branch': branch,
        'team_id': team_id,
        'project_id': ownership['project_id'],
        'project_name': ownership['project_name'],
        'quality': quality,
    }


def run_for_merge(
    source: Path,
    *,
    expected_sha: str,
    artifact_dir: Path,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    vercel_project_id: Optional[str] = None,
    repository_id: Optional[str] = None,
    quality_evidence: Optional[Mapping[str, Any]] = None,
    connect_fn: Optional[Callable[[str], Any]] = None,
    quality_waiter: Optional[Callable[..., Mapping[str, Any]]] = None,
    github_context_fn: Optional[Callable[[Path], Tuple[str, str, str, str]]] = None,
) -> Dict[str, Any]:
    """Merge-onboarding seam after exact-head quality and before shell deploy.

    Disabled config: no network. Enabled without auto_apply: report only.
    Enabled+auto_apply requires enrolled project id + quality evidence, then
    runs execute_authorized_delivery. Missing enrollment binding fails closed
    without claiming a finished streamlined workflow.
    """
    cfg_mod = load('database-delivery-config')
    env_map = dict(os.environ if env is None else env)
    try:
        config = cfg_mod.load_database_delivery_config(
            source, require_tracked_clean=True, expected_sha=expected_sha)
    except cfg_mod.ConfigError as exc:
        report = build_report(status='delivery_config_invalid', error=str(exc))
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError(str(exc)) from None

    if not cfg_mod.delivery_enabled(config):
        report = build_report(
            status='delivery_disabled',
            config_status=config.get('config_status', 'configured'),
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    missing = missing_delivery_secrets(env_map, bootstrap_mode=config.get('bootstrap_mode', 'temporary_login'))
    if missing:
        report = build_report(
            status='setup_required',
            required_secret_names=missing,
            namespace=config['namespace'],
            config_status=config.get('config_status'),
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    if not cfg_mod.delivery_auto_apply(config):
        report = build_report(
            status='delivery_auto_apply_disabled',
            namespace=config['namespace'],
            migrations_path=config['migrations_path'],
            expected_project_ref=config['expected_project_ref'],
            config_status=config.get('config_status'),
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    if not vercel_project_id or not repository_id or quality_evidence is None:
        report = build_report(
            status='delivery_enrollment_binding_required',
            namespace=config['namespace'],
            migrations_path=config['migrations_path'],
            expected_project_ref=config['expected_project_ref'],
            detail=(
                'Reviewed auto_apply requires owned Vercel enrollment and '
                'exact-head quality evidence before delivery mutations. Merge '
                'must enroll first, then call this seam with the binding.'
            ),
            vercel_project_id_supplied=bool(vercel_project_id),
            repository_id_supplied=bool(repository_id),
            quality_evidence_supplied=quality_evidence is not None,
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    return execute_authorized_delivery(
        source=source,
        expected_sha=expected_sha,
        env=env_map,
        transport=transport or StdlibTransport(),
        vercel_project_id=vercel_project_id,
        repository_id=repository_id,
        quality_evidence=quality_evidence,
        connect_fn=connect_fn,
        artifact_dir=artifact_dir,
        quality_waiter=quality_waiter,
        github_context_fn=github_context_fn,
    )


def execute_authorized_delivery(
    *,
    source: Path,
    expected_sha: str,
    env: Mapping[str, str],
    transport: Transport,
    vercel_project_id: str,
    repository_id: str,
    quality_evidence: Optional[Mapping[str, Any]] = None,
    connect_fn: Optional[Callable[[str], Any]] = None,
    artifact_dir: Optional[Path] = None,
    sslmode: str = 'verify-full',
    quality_waiter: Optional[Callable[..., Mapping[str, Any]]] = None,
    github_context_fn: Optional[Callable[[Path], Tuple[str, str, str, str]]] = None,
) -> Dict[str, Any]:
    """Production entry: config/SHA + GitHub/quality/ownership before mutation.

    Rejects forged project/repo IDs and optional production test overrides.
    Loopback plaintext and pooler/login/role injection are fixture-only via
    execute_delivery(allow_test_orchestration=True).
    """
    cfg_mod = load('database-delivery-config')
    if not isinstance(vercel_project_id, str) or not PROJECT_ID_RE.fullmatch(vercel_project_id):
        raise DeliveryError('vercel_project_id_invalid')
    if not isinstance(repository_id, str) or not REPO_ID_RE.fullmatch(repository_id):
        raise DeliveryError('repository_id_invalid')
    if not isinstance(expected_sha, str) or not SHA_RE.fullmatch(expected_sha):
        raise DeliveryError('source_sha_invalid')
    try:
        config = cfg_mod.load_database_delivery_config(
            source, require_tracked_clean=True, expected_sha=expected_sha)
    except cfg_mod.ConfigError as exc:
        report = build_report(status='delivery_config_invalid', error=str(exc))
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError(str(exc)) from None
    if not cfg_mod.delivery_enabled(config) or not cfg_mod.delivery_auto_apply(config):
        report = build_report(
            status='delivery_not_authorized',
            config_status=config.get('config_status'),
            enabled=bool(config.get('enabled')),
            auto_apply=bool(config.get('auto_apply')),
        )
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError('delivery_not_authorized')
    missing = missing_delivery_secrets(env, bootstrap_mode=config.get('bootstrap_mode', 'temporary_login'))
    if missing:
        report = build_report(
            status='setup_required',
            required_secret_names=missing,
            namespace=config['namespace'],
        )
        _maybe_write_artifact(artifact_dir, report)
        return report
    try:
        authorization = authorize_delivery_context(
            source=source,
            expected_sha=expected_sha,
            repository_id=repository_id,
            vercel_project_id=vercel_project_id,
            env=env,
            transport=transport,
            quality_evidence=quality_evidence,
            quality_waiter=quality_waiter,
            github_context_fn=github_context_fn,
        )
    except DeliveryError as exc:
        report = build_report(
            status=str(exc),
            namespace=config['namespace'],
            vercel_writes=0,
            password_writes=0,
            ddl_writes=0,
        )
        _maybe_write_artifact(artifact_dir, report)
        raise
    report = _execute_delivery_core(
        source=source,
        config=config,
        expected_sha=expected_sha,
        env=env,
        transport=transport,
        vercel_project_id=authorization['project_id'],
        repository_id=authorization['repository_id'],
        connect_fn=connect_fn,
        artifact_dir=artifact_dir,
        sslmode=sslmode,
        loopback_fixture=False,
        pooler_override=None,
        cli_login_override=None,
        provisioner_role=TRUSTED_PROVISIONER_ROLE,
    )
    if isinstance(report, dict):
        report['authorization'] = {
            'project_id': authorization['project_id'],
            'team_id': authorization['team_id'],
            'repository_id': authorization['repository_id'],
            'source_sha': authorization['source_sha'],
            'quality_run_id': authorization['quality']['run_id'],
        }
        # Binding must come from the immutable executed manifest already used by
        # the core — never recapture (index/working tree may have mutated) or
        # fall back to empty checksums.
        binding = report.get('delivery_binding_sha256')
        if not isinstance(binding, str) or not re.fullmatch(r'[0-9a-f]{64}', binding):
            raise DeliveryError('delivery_binding_unavailable')
        _maybe_write_artifact(artifact_dir, report)
    return report


def execute_delivery(
    *,
    source: Path,
    config: Mapping[str, Any],
    env: Mapping[str, str],
    transport: Transport,
    vercel_project_id: str,
    repository_id: str,
    connect_fn: Optional[Callable[[str], Any]] = None,
    artifact_dir: Optional[Path] = None,
    sslmode: str = 'verify-full',
    loopback_fixture: bool = False,
    pooler_override: Any = None,
    cli_login_override: Any = None,
    provisioner_role: str = TRUSTED_PROVISIONER_ROLE,
    allow_test_orchestration: bool = False,
    expected_sha: Optional[str] = None,
    quality_evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Orchestration entry.

    Production callers must not omit authorization: without
    allow_test_orchestration=True this delegates to execute_authorized_delivery
    (requires expected_sha, quality/ownership proof, reviewed config). Tests may
    pass allow_test_orchestration=True with injected transports/loopback.
    """
    if not allow_test_orchestration:
        if expected_sha is None:
            raise DeliveryError('delivery_authorization_required')
        return execute_authorized_delivery(
            source=source,
            expected_sha=expected_sha,
            env=env,
            transport=transport,
            vercel_project_id=vercel_project_id,
            repository_id=repository_id,
            quality_evidence=quality_evidence,
            connect_fn=connect_fn,
            artifact_dir=artifact_dir,
            sslmode=sslmode,
        )
    if loopback_fixture and sslmode != 'disable':
        sslmode = 'disable'
    return _execute_delivery_core(
        source=source,
        config=config,
        expected_sha=expected_sha,
        env=env,
        transport=transport,
        vercel_project_id=vercel_project_id,
        repository_id=repository_id,
        connect_fn=connect_fn,
        artifact_dir=artifact_dir,
        sslmode=sslmode,
        loopback_fixture=loopback_fixture,
        pooler_override=pooler_override,
        cli_login_override=cli_login_override,
        provisioner_role=provisioner_role,
    )


def _execute_delivery_core(
    *,
    source: Path,
    config: Mapping[str, Any],
    expected_sha: Optional[str],
    env: Mapping[str, str],
    transport: Transport,
    vercel_project_id: str,
    repository_id: str,
    connect_fn: Optional[Callable[[str], Any]],
    artifact_dir: Optional[Path],
    sslmode: str,
    loopback_fixture: bool,
    pooler_override: Any,
    cli_login_override: Any,
    provisioner_role: str,
) -> Dict[str, Any]:
    mdb = load('managed_database')
    provider = load('supabase_management_database')
    custody = load('vercel_env_custody')
    cfg_mod = load('database-delivery-config')

    namespace = config['namespace']
    migrations_rel = config['migrations_path']
    expected_ref = config['expected_project_ref']
    rotate_migrator = bool(config.get('rotate_migrator'))
    bootstrap_mode = config.get('bootstrap_mode', 'temporary_login')
    if bootstrap_mode not in ('temporary_login', 'existing_secret'):
        raise DeliveryError('config_bootstrap_mode_invalid')
    canaries: List[str] = []

    url = env.get('SUPABASE_URL', '')
    token = env.get('SUPABASE_ACCESS_TOKEN', '')
    vercel_token = env.get('VERCEL_API_TOKEN', '')
    team_id = env.get('VERCEL_TEAM_ID', '')
    try:
        project_ref = project_ref_from_supabase_url(url)
    except DeliveryError as exc:
        report = build_report(status=str(exc), namespace=namespace)
        _maybe_write_artifact(artifact_dir, report)
        raise
    if project_ref != expected_ref:
        report = build_report(
            status='project_ref_binding_mismatch',
            namespace=namespace,
            expected_project_ref=expected_ref,
        )
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError('project_ref_binding_mismatch')

    binding = custody.EnvBinding(
        repository_id=str(repository_id),
        project_ref=project_ref,
        namespace=namespace,
        vercel_project_id=vercel_project_id,
    )

    # Capture approved migration bytes before any network/DB mutation.
    try:
        if expected_sha is not None:
            manifest = cfg_mod.capture_approved_migrations(
                Path(source), migrations_rel, expected_sha=expected_sha)
        else:
            # Test orchestration may skip git HEAD binding when explicitly allowed.
            manifest = cfg_mod.capture_approved_migrations(Path(source), migrations_rel)
    except cfg_mod.ConfigError as exc:
        # Tests often use ephemeral repos: fall back to discover only under
        # loopback_fixture after symlink-safe path validation already ran.
        if not loopback_fixture:
            report = build_report(status=str(exc), namespace=namespace)
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError(str(exc)) from None
        migrations_dir = Path(source).resolve() / migrations_rel
        captured_migrations = mdb.discover_migrations(migrations_dir)
        manifest = None
    else:
        captured_migrations = mdb.migrations_from_captured_manifest(manifest)

    if pooler_override is not None:
        pooler = pooler_override
    else:
        try:
            pooler = provider.fetch_primary_session_pooler(
                project_ref=project_ref,
                access_token=token,
                transport=transport,
            )
        except provider.ProviderError as exc:
            report = build_report(status=str(exc), namespace=namespace, **exc.diagnostic_fields())
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError(str(exc), exc.diagnostic_fields()) from None

    effective_ssl = 'disable' if loopback_fixture else sslmode
    sslrootcert = None
    if effective_ssl == 'verify-full':
        try:
            sslrootcert = provider.resolve_provider_ssl_rootcert()
        except provider.ProviderError as exc:
            report = build_report(status=str(exc), namespace=namespace, **exc.diagnostic_fields())
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError(str(exc), exc.diagnostic_fields()) from None

    # Validate before custody or login mutation. No automatic fallback.
    existing = None
    if bootstrap_mode == 'existing_secret':
        try:
            if effective_ssl != 'verify-full':
                raise provider.ProviderError('bootstrap_secret_sslmode_invalid')
            existing = provider.validate_existing_bootstrap_conninfo(
                env.get(BOOTSTRAP_ENV_KEY, ''), target=pooler)
        except provider.ProviderError as exc:
            report = build_report(status=str(exc), namespace=namespace)
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError(str(exc)) from None
        canaries.extend((env[BOOTSTRAP_ENV_KEY], existing.password.value))

    # Custody inventory (read-only) before any DB mutation or env write.
    try:
        custody_state = custody.inspect_runtime_custody(
            project_id=vercel_project_id,
            team_id=team_id,
            token=vercel_token,
            binding=binding,
            transport=transport,
        )
    except custody.CustodyError as exc:
        report = build_report(status=str(exc), namespace=namespace, **exc.diagnostic_fields())
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError(str(exc), exc.diagnostic_fields()) from None

    if custody_state['status'] == 'recovery_required':
        report = build_report(
            status='recovery_required',
            namespace=namespace,
            reason=custody_state.get('reason'),
            runtime_env_key=RUNTIME_ENV_KEY,
            vercel_writes=0,
            password_writes=0,
            ddl_writes=0,
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    if existing is not None:
        login = None
    elif cli_login_override is not None:
        login = cli_login_override
    else:
        try:
            login = provider.create_cli_login_role(
                project_ref=project_ref,
                access_token=token,
                transport=transport,
            )
        except provider.ProviderError as exc:
            report = build_report(status=str(exc), namespace=namespace, **exc.diagnostic_fields())
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError(str(exc), exc.diagnostic_fields()) from None
    if login is not None:
        canaries.append(login.password.value)

    roles = mdb.derive_role_names(namespace)
    try:
        if existing is not None:
            provisioner_dsn = existing.conninfo
        else:
            assert login is not None
            user = login.role if loopback_fixture else provider.pooler_login_username(
                login.role, project_ref)
            provisioner_dsn = provider.build_psycopg_dsn(
                host=pooler.host,
                port=pooler.port,
                database=pooler.database,
                user=user,
                password=login.password.value,
                sslmode=effective_ssl,
                sslrootcert=sslrootcert,
                loopback_fixture=loopback_fixture,
            )
    except provider.ProviderError as exc:
        report = build_report(status=str(exc), namespace=namespace)
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError(str(exc)) from None

    connector = connect_fn or connect_with_retries
    try:
        conn = connector(provisioner_dsn)
    except Exception:
        report = build_report(status='database_auth_failed', namespace=namespace)
        _maybe_write_artifact(artifact_dir, report)
        raise DeliveryError('database_auth_failed') from None

    runtime_created: Optional[bool] = False
    runtime_write_attempted = False
    runtime_persisted: Optional[bool] = custody_state['status'] == 'reusable'
    database_mutation_attempted = False
    database_mutation_completion = 'not_attempted'
    provision_created: Optional[bool] = None
    runtime_password: Optional[str] = None
    runtime_validated_dsn: Optional[str] = None
    migrator_password = _Secret(generate_password())
    canaries.append(migrator_password.value)
    created = False
    can_migrate = True
    migrate_result = None
    call_order: List[str] = [
        'existing_secret' if existing is not None else 'cli_login_or_override',
        'provisioner_connect',
    ]

    try:
        assume_trusted_provisioner(conn, role=provisioner_role)
        call_order.append('catalog_probe')
        probe = mdb.inspect_app_catalog_state(
            namespace=namespace,
            repository_id=str(repository_id),
            project_ref=project_ref,
            provisioner_connection=conn,
            expected_provisioner_role=provisioner_role,
            allow_trusted_bootstrap_bypassrls=True,
        )
        if probe.status == 'collision':
            report = build_report(
                status=probe.reason or 'catalog_collision',
                namespace=namespace,
                vercel_writes=0,
                password_writes=0,
                ddl_writes=0,
                call_order=call_order,
            )
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError(probe.reason or 'catalog_collision')

        if probe.status == 'owned' and custody_state['status'] == 'absent':
            report = build_report(
                status='recovery_required',
                namespace=namespace,
                reason='owned_app_missing_runtime_custody',
                runtime_env_key=RUNTIME_ENV_KEY,
                vercel_writes=0,
                password_writes=0,
                ddl_writes=0,
                call_order=call_order,
            )
            _maybe_write_artifact(artifact_dir, report)
            return report

        expected_runtime_user = (
            roles.runtime if loopback_fixture
            else provider.pooler_login_username(roles.runtime, project_ref)
        )

        if custody_state['status'] == 'reusable':
            call_order.append('validate_runtime_secret')
            try:
                validated = provider.validate_runtime_conninfo(
                    custody_state['value'].value,
                    expected_host=pooler.host,
                    expected_port=pooler.port,
                    expected_database=pooler.database,
                    expected_user=expected_runtime_user,
                    expected_sslmode=effective_ssl,
                    expected_sslrootcert=sslrootcert,
                )
            except provider.ProviderError as exc:
                report = build_report(
                    status='recovery_required',
                    namespace=namespace,
                    reason=str(exc),
                    vercel_writes=0,
                    password_writes=0,
                    ddl_writes=0,
                )
                _maybe_write_artifact(artifact_dir, report)
                return report
            runtime_password = validated.password.value
            runtime_validated_dsn = validated.conninfo
            canaries.append(runtime_password)
            if probe.status == 'owned':
                # Authenticate exact persisted settings BEFORE migrator/DDL.
                call_order.append('runtime_auth_before_mutation')
                try:
                    runtime_probe = connector(runtime_validated_dsn)
                    try:
                        who = runtime_probe.execute(
                            'SELECT current_user'
                        ).fetchone()[0]
                        if who != roles.runtime:
                            raise DeliveryError('runtime_login_mismatch')
                    finally:
                        runtime_probe.close()
                except DeliveryError as exc:
                    report = build_report(
                        status='recovery_required',
                        namespace=namespace,
                        reason=str(exc),
                        vercel_writes=0,
                        password_writes=0,
                        ddl_writes=0,
                        call_order=call_order,
                    )
                    _maybe_write_artifact(artifact_dir, report)
                    return report
                except Exception:
                    report = build_report(
                        status='recovery_required',
                        namespace=namespace,
                        reason='runtime_login_failed',
                        vercel_writes=0,
                        password_writes=0,
                        ddl_writes=0,
                        call_order=call_order,
                    )
                    _maybe_write_artifact(artifact_dir, report)
                    return report
            else:
                # Resume after custody persist, before first provision.
                call_order.append('resume_new_app_with_persisted_custody')
        elif custody_state['status'] == 'absent':
            if probe.status != 'absent':
                report = build_report(
                    status='recovery_required',
                    namespace=namespace,
                    reason='catalog_custody_inconsistent',
                    vercel_writes=0,
                )
                _maybe_write_artifact(artifact_dir, report)
                return report
            runtime_password = generate_password()
            canaries.append(runtime_password)
            runtime_dsn_value = provider.build_psycopg_dsn(
                host=pooler.host,
                port=pooler.port,
                database=pooler.database,
                user=expected_runtime_user,
                password=runtime_password,
                sslmode=effective_ssl,
                sslrootcert=sslrootcert,
                loopback_fixture=loopback_fixture,
            )
            call_order.append('custody_persist')
            # A dispatched write may complete even when its caller raises.
            runtime_write_attempted = True
            runtime_persisted = None
            runtime_created = None
            try:
                persisted = custody.persist_runtime_env(
                    project_id=vercel_project_id,
                    team_id=team_id,
                    token=vercel_token,
                    binding=binding,
                    value=runtime_dsn_value,
                    transport=transport,
                )
            except custody.CustodyError as exc:
                fields = dict(exc.report_fields())
                fields.update(exc.diagnostic_fields())
                fields.update({
                    'status': str(exc),
                    'namespace': namespace,
                    'call_order': call_order,
                    # Persist happens before DB login/DDL; never claim those ran.
                    'password_writes': 0,
                    'ddl_writes': 0,
                })
                # Do not claim vercel_writes=0 after a POST attempt.
                if (exc.custody_completion != 'uncertain'
                        and exc.vercel_writes_attempted == 0):
                    fields['vercel_writes'] = 0
                report = build_report(**fields)
                _maybe_write_artifact(artifact_dir, report)
                raise DeliveryError(str(exc), exc.diagnostic_fields()) from None
            runtime_created = bool(persisted.get('created'))
            runtime_persisted = True
            # Re-validate exact readback before creating DB login.
            validated = provider.validate_runtime_conninfo(
                persisted['value'].value,
                expected_host=pooler.host,
                expected_port=pooler.port,
                expected_database=pooler.database,
                expected_user=expected_runtime_user,
                expected_sslmode=effective_ssl,
                expected_sslrootcert=sslrootcert,
            )
            runtime_password = validated.password.value
            runtime_validated_dsn = validated.conninfo
        else:
            report = build_report(
                status='env_custody_conflict',
                namespace=namespace,
                custody_status=custody_state.get('status'),
            )
            _maybe_write_artifact(artifact_dir, report)
            raise DeliveryError('env_custody_conflict')

        assert runtime_password is not None
        assert runtime_validated_dsn is not None

        call_order.append('provision_or_verify')
        database_mutation_attempted = True
        database_mutation_completion = 'uncertain'
        result = mdb.provision_app(
            namespace=namespace,
            repository_id=str(repository_id),
            project_ref=project_ref,
            migrator_password=migrator_password.value,
            runtime_password=runtime_password,
            provisioner_connection=conn,
            expected_provisioner_role=provisioner_role,
            allow_trusted_bootstrap_bypassrls=True,
        )
        created = bool(result.created)
        provision_created = created
        database_mutation_completion = 'confirmed'
        if not created and rotate_migrator:
            call_order.append('migrator_rotate')
            database_mutation_completion = 'uncertain'
            mdb.set_owned_login_password(
                namespace=namespace,
                repository_id=str(repository_id),
                project_ref=project_ref,
                role_kind='migrator',
                new_password=migrator_password.value,
                provisioner_connection=conn,
                expected_provisioner_role=provisioner_role,
                allow_trusted_bootstrap_bypassrls=True,
            )
            database_mutation_completion = 'confirmed'
        elif not created and not rotate_migrator:
            can_migrate = False
    except DeliveryError:
        raise
    except Exception as exc:
        # Driver errors can contain credentials; never expose their chains.
        # Library-normalized errors preserve the same uncertain checkpoints.
        status = exc.code if isinstance(exc, mdb.ManagedDatabaseError) else 'database_bootstrap_failed'
        # Preserve completed checkpoints, and do not mistake an interrupted
        # dispatch for proof that its writes did not happen. None means unknown.
        fields = {
            'runtime_env_write_attempted': runtime_write_attempted,
            'runtime_env_persisted': runtime_persisted,
            'runtime_env_created': runtime_created,
            'database_mutation_attempted': database_mutation_attempted,
            'database_mutation_completion': database_mutation_completion,
            'call_order': call_order,
        }
        if provision_created is not None:
            fields['provision_created'] = provision_created
        if not database_mutation_attempted:
            fields.update(password_writes=0, ddl_writes=0)
        if not runtime_write_attempted:
            fields['vercel_writes'] = 0
        report = build_report(
            status=status, namespace=namespace, **fields)
        _redact_report_artifact(artifact_dir, report, canaries)
        raise DeliveryError(status) from None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not can_migrate:
        report = build_report(
            status='migrator_credential_job_only_requires_rotation_flag',
            namespace=namespace,
            created=False,
            runtime_env_persisted=True,
            runtime_env_created=runtime_created,
            migrator_custody='job_memory_only',
            call_order=call_order,
        )
        _maybe_write_artifact(artifact_dir, report)
        return report

    migrator_user = roles.migrator if loopback_fixture else provider.pooler_login_username(
        roles.migrator, project_ref)
    migrator_dsn = provider.build_psycopg_dsn(
        host=pooler.host,
        port=pooler.port,
        database=pooler.database,
        user=migrator_user,
        password=migrator_password.value,
        sslmode=effective_ssl,
        sslrootcert=sslrootcert,
        loopback_fixture=loopback_fixture,
    )
    # Retry only connection establishment; pass idle autocommit connection in.
    call_order.append('migrator_connect_with_retries')
    try:
        migrator_conn = connector(migrator_dsn)
    except DeliveryError as exc:
        report = build_report(status=str(exc), namespace=namespace, provision_created=created)
        _maybe_write_artifact(artifact_dir, report)
        raise
    try:
        call_order.append('migrate')
        migrate_result = mdb.migrate_app(
            namespace=namespace,
            repository_id=str(repository_id),
            migrations=captured_migrations,
            migrator_connection=migrator_conn,
        )
    except mdb.ManagedDatabaseError as exc:
        report = build_report(
            status=exc.code, namespace=namespace, provision_created=created,
            call_order=call_order)
        _redact_report_artifact(artifact_dir, report, canaries)
        raise DeliveryError(exc.code) from None
    finally:
        try:
            migrator_conn.close()
        except Exception:
            pass

    # Local runtime proof using the exact validated custody DSN settings.
    call_order.append('runtime_local_rls_proof')
    try:
        proof = _prove_runtime_local_isolation(
            runtime_dsn=runtime_validated_dsn,
            connector=connector,
            namespace=namespace,
            roles=roles,
            allow_fixture_crud=loopback_fixture,
            second_namespace=None,
        )
    except DeliveryError as exc:
        report = build_report(
            status=str(exc),
            namespace=namespace,
            provision_created=created,
            migrations_applied=migrate_result.applied_count if migrate_result else 0,
            call_order=call_order,
        )
        _redact_report_artifact(artifact_dir, report, canaries)
        raise

    checksums: List[str] = []
    if manifest is not None:
        checksums = [item['checksum_sha256'] for item in manifest]
    binding_digest = compute_delivery_binding_digest(
        config=config,
        repository_id=str(repository_id),
        vercel_project_id=vercel_project_id,
        source_sha=str(expected_sha or ''),
        migration_checksums=checksums,
    ) if expected_sha else None

    report = build_report(
        status='delivery_completed_local_runtime_proof',
        namespace=namespace,
        project_ref=project_ref,
        repository_id=str(repository_id),
        vercel_project_id=vercel_project_id,
        provision_created=created,
        migrations_applied=migrate_result.applied_count,
        migrations_skipped=migrate_result.skipped_count,
        runtime_env_key=RUNTIME_ENV_KEY,
        runtime_env_created=runtime_created,
        trusted_provisioner_role=provisioner_role,
        bootstrap_mode=bootstrap_mode,
        cli_login_ttl_seconds=login.ttl_seconds if login is not None else None,
        migrator_custody='job_memory_only',
        local_runtime_proof=proof,
        call_order=call_order,
        delivery_binding_sha256=binding_digest,
        note=(
            'Local restricted LOGIN proof succeeded under fixture or approved '
            'target. database_ready and full_app_ready stay false until '
            'deployed HTTP/JWT/data-path verification.'
        ),
    )
    _redact_report_artifact(artifact_dir, report, canaries)
    return report


def _redact_report_artifact(
    artifact_dir: Optional[Path],
    report: Mapping[str, Any],
    canaries: Sequence[str],
) -> None:
    if artifact_dir is None:
        return
    text = json.dumps(dict(report), indent=2, sort_keys=True)
    text = sanitize_exception_text(text, canaries)
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / 'database-delivery-result.json').write_text(text + '\n', encoding='utf-8')


def _require_denial_sqlstate(exc: BaseException, *, label: str) -> str:
    state = getattr(exc, 'sqlstate', None)
    if state == '25P02':
        raise DeliveryError('runtime_failed_txn_sqlstate') from None
    if state != EXPECTED_DENIAL_SQLSTATE:
        raise DeliveryError(f'{label}_sqlstate_unexpected') from None
    return state


def _prove_runtime_local_isolation(
    *,
    runtime_dsn: str,
    connector: Callable[[str], Any],
    namespace: str,
    roles: Any,
    allow_fixture_crud: bool,
    second_namespace: Optional[str],
) -> Dict[str, Any]:
    """Production: nonmutating login/metadata. Fixture CRUD is rolled back."""
    from psycopg import sql as psql
    import uuid as uuid_mod

    runtime_conn = connector(runtime_dsn)
    try:
        who = runtime_conn.execute('SELECT current_user').fetchone()[0]
        if who != roles.runtime:
            raise DeliveryError('runtime_login_mismatch')
        schema = runtime_conn.execute('SELECT current_schema()').fetchone()[0]
        search_path = runtime_conn.execute('SHOW search_path').fetchone()[0]
        production_evidence = {
            'login_confirmed': True,
            'own_user_crud': False,
            'current_user': who,
            'current_schema': schema,
            'search_path': search_path,
            'proof_kind': 'production_login_metadata',
            'note': (
                'Production default performs nonmutating scoped login/metadata '
                'access only. Synthetic JWT CRUD is fixture-only.'
            ),
        }
        if not allow_fixture_crud:
            return production_evidence

        has_items = runtime_conn.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = current_schema() AND table_name = 'items'
            )
            """
        ).fetchone()[0]
        if not has_items:
            production_evidence['proof_kind'] = 'fixture_login_only_no_items_table'
            return production_evidence

        denials: List[str] = []
        items = psql.Identifier(schema, 'items')
        # Fully transactional fixture CRUD — no canary rows remain.
        prior_autocommit = runtime_conn.autocommit
        runtime_conn.autocommit = False
        try:
            user_a = str(uuid_mod.uuid4())
            user_b = str(uuid_mod.uuid4())
            with runtime_conn.transaction():
                runtime_conn.execute(
                    "SELECT set_config('request.jwt.claim.sub', %s, true)", (user_a,)
                )
                runtime_conn.execute(
                    psql.SQL(
                        'INSERT INTO {} (owner_id, note) VALUES (%s::uuid, %s)'
                    ).format(items),
                    (user_a, 'own'),
                )
                count = runtime_conn.execute(
                    psql.SQL(
                        'SELECT count(*) FROM {} WHERE owner_id = %s::uuid'
                    ).format(items),
                    (user_a,),
                ).fetchone()[0]
                if count != 1:
                    raise DeliveryError('runtime_own_read_failed')
                runtime_conn.execute(
                    psql.SQL(
                        'UPDATE {} SET note = %s WHERE owner_id = %s::uuid'
                    ).format(items),
                    ('updated', user_a),
                )
                updated = runtime_conn.execute(
                    psql.SQL(
                        'SELECT note FROM {} WHERE owner_id = %s::uuid'
                    ).format(items),
                    (user_a,),
                ).fetchone()[0]
                if updated != 'updated':
                    raise DeliveryError('runtime_own_update_failed')
                runtime_conn.execute(
                    psql.SQL(
                        'DELETE FROM {} WHERE owner_id = %s::uuid'
                    ).format(items),
                    (user_a,),
                )
                remaining = runtime_conn.execute(
                    psql.SQL('SELECT count(*) FROM {}').format(items)
                ).fetchone()[0]
                if remaining != 0:
                    raise DeliveryError('runtime_own_delete_failed')

                # Fresh savepoint per expected denial — never rely on 25P02.
                with runtime_conn.transaction():
                    runtime_conn.execute(
                        "SELECT set_config('request.jwt.claim.sub', %s, true)",
                        (user_b,),
                    )
                    try:
                        with runtime_conn.transaction():
                            runtime_conn.execute(
                                psql.SQL(
                                    'INSERT INTO {} (owner_id, note) '
                                    'VALUES (%s::uuid, %s)'
                                ).format(items),
                                (user_a, 'steal'),
                            )
                        raise DeliveryError('runtime_cross_user_write_not_denied')
                    except DeliveryError:
                        raise
                    except Exception as exc:
                        denials.append(
                            _require_denial_sqlstate(
                                exc, label='runtime_cross_user_write'))
                    visible = runtime_conn.execute(
                        psql.SQL('SELECT count(*) FROM {}').format(items)
                    ).fetchone()[0]
                    if visible != 0:
                        raise DeliveryError('runtime_cross_user_read_not_denied')

                try:
                    with runtime_conn.transaction():
                        runtime_conn.execute(
                            psql.SQL('CREATE TABLE {} (id int)').format(
                                psql.Identifier(schema, 'evil_runtime'))
                        )
                    raise DeliveryError('runtime_ddl_not_denied')
                except DeliveryError:
                    raise
                except Exception as exc:
                    denials.append(
                        _require_denial_sqlstate(exc, label='runtime_ddl'))

                if second_namespace:
                    try:
                        with runtime_conn.transaction():
                            runtime_conn.execute(
                                psql.SQL('SELECT count(*) FROM {}').format(
                                    psql.Identifier(second_namespace, 'items'))
                            )
                        raise DeliveryError('runtime_cross_app_read_not_denied')
                    except DeliveryError:
                        raise
                    except Exception as exc:
                        denials.append(
                            _require_denial_sqlstate(
                                exc, label='runtime_cross_app'))
                # Roll back the outer transaction — no fixture residue.
                raise _FixtureProofRollback()
        except _FixtureProofRollback:
            pass
        finally:
            try:
                runtime_conn.rollback()
            except Exception:
                pass
            runtime_conn.autocommit = prior_autocommit

        leftover = runtime_conn.execute(
            psql.SQL('SELECT count(*) FROM {}').format(items)
        ).fetchone()[0]
        if leftover != 0:
            raise DeliveryError('runtime_fixture_residue_remaining')
        evil = runtime_conn.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = %s AND table_name = 'evil_runtime'
            )
            """,
            (schema,),
        ).fetchone()[0]
        if evil:
            raise DeliveryError('runtime_fixture_residue_remaining')
        return {
            'login_confirmed': True,
            'own_user_crud': True,
            'cross_user_denied_sqlstates': denials,
            'proof_kind': 'fixture_runtime_rls_rolled_back',
            'current_schema': schema,
        }
    finally:
        runtime_conn.close()


class _FixtureProofRollback(Exception):
    """Internal control to roll back fixture CRUD transaction."""

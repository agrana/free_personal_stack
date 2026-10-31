#!/usr/bin/env python3
"""Supabase Management API helpers for temporary CLI login and pooler metadata.

Uses fixed provider hosts/paths only. Transport is injectable. Never logs
credentials, raw bodies, or password-bearing URLs. Does not call
DELETE /cli/login-role. Does not reset project postgres passwords.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import unquote, urlparse

API_HOST = 'api.supabase.com'
CLI_LOGIN_PATH_TMPL = '/v1/projects/{ref}/cli/login-role'
POOLER_PATH_TMPL = '/v1/projects/{ref}/config/database/pooler'
FORBIDDEN_DELETE_LOGIN = '/cli/login-role'
REQUEST_TIMEOUT_SEC = 30
MAX_RESPONSE_BYTES = 200_000
PROJECT_REF_RE = re.compile(r'^[a-z0-9]+$')
POOLER_HOST_RE = re.compile(
    r'^aws-[0-9]+-[a-z0-9-]+\.pooler\.supabase\.com$'
)
SESSION_POOLER_PORT = 5432
TRANSACTION_POOLER_PORT = 6543
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})
# Reviewed system trust stores only; never invent or downgrade.
SYSTEM_CA_CANDIDATES = (
    '/etc/ssl/certs/ca-certificates.crt',  # Debian/Ubuntu/Fedora ca-certificates
    '/etc/pki/tls/certs/ca-bundle.crt',  # RHEL/CentOS
    '/etc/ssl/cert.pem',  # generic / some Alpine
)
ALLOWED_CONNINFO_KEYS = frozenset({
    'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'sslrootcert',
})
PRODUCTION_SSLMODE = 'verify-full'
FIXTURE_SSLMODE = 'disable'


class ProviderError(Exception):
    """Stable machine-readable provider failure code (never includes secrets)."""

    _operation: Optional[str] = None
    _http_status: Optional[int] = None

    def diagnostic_fields(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        operation = getattr(self, '_operation', None)
        if operation in ('supabase_pooler_get', 'supabase_cli_login_post'):
            result['provider_operation'] = operation
        status = getattr(self, '_http_status', None)
        if operation in ('supabase_pooler_get', 'supabase_cli_login_post') and type(status) is int and 100 <= status <= 599:
            result['provider_http_status'] = status
        if operation == 'supabase_cli_login_post':
            result['cli_login_mutation_possible'] = True
        return result

    def __repr__(self) -> str:
        return f"ProviderError({str(self)!r})"


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


@dataclass(frozen=True)
class _Secret:
    value: str

    def __repr__(self) -> str:
        return 'Secret(<redacted>)'

    def __str__(self) -> str:
        return 'Secret(<redacted>)'


@dataclass(frozen=True)
class CliLoginRole:
    role: str
    password: _Secret
    ttl_seconds: int


@dataclass(frozen=True)
class SessionPoolerTarget:
    project_ref: str
    host: str
    port: int
    database: str
    db_user_suffix: str


@dataclass(frozen=True)
class ValidatedConninfo:
    host: str
    port: int
    database: str
    user: str
    password: _Secret
    sslmode: str
    sslrootcert: Optional[str]
    conninfo: str

    def __repr__(self) -> str:
        return (
            "ValidatedConninfo("
            f"host={self.host!r}, port={self.port!r}, database={self.database!r}, "
            f"user={self.user!r}, password=Secret(<redacted>), "
            f"sslmode={self.sslmode!r}, sslrootcert={self.sslrootcert!r}, "
            "conninfo=Secret(<redacted>))"
        )


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


def _require_ref(ref: str) -> str:
    if not isinstance(ref, str) or not PROJECT_REF_RE.fullmatch(ref):
        raise ProviderError('project_ref_invalid')
    return ref


def cli_login_url(ref: str) -> str:
    ref = _require_ref(ref)
    path = CLI_LOGIN_PATH_TMPL.format(ref=ref)
    if FORBIDDEN_DELETE_LOGIN in path and path.endswith('/cli/login-role'):
        # Path is the create endpoint; DELETE is never constructed here.
        pass
    return 'https://' + API_HOST + path


def pooler_url(ref: str) -> str:
    ref = _require_ref(ref)
    return 'https://' + API_HOST + POOLER_PATH_TMPL.format(ref=ref)


def _parse_json(payload: bytes) -> Any:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ProviderError('provider_response_too_large')
    if not payload:
        raise ProviderError('provider_response_invalid')
    try:
        return json.loads(payload.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise ProviderError('provider_response_invalid') from None


def create_cli_login_role(
    *,
    project_ref: str,
    access_token: str,
    transport: Transport,
    read_only: bool = False,
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> CliLoginRole:
    """POST /v1/projects/{ref}/cli/login-role — temporary native CLI login.

    Requires Management API database_write. Response passwords stay in memory.
    Never issues DELETE /cli/login-role (shared role deletion).
    """
    if read_only:
        raise ProviderError('cli_login_read_only_forbidden_for_delivery')
    if not isinstance(access_token, str) or not access_token.strip():
        raise ProviderError('access_token_missing')
    url = cli_login_url(project_ref)
    body = json.dumps({'read_only': False}, separators=(',', ':')).encode('utf-8')
    headers = {
        'Authorization': 'Bearer ' + access_token.strip(),
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    try:
        try:
            status, payload = transport.request(
                method='POST', url=url, headers=headers, body=body, timeout=timeout)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('provider_transport_failed') from None
        if status not in (200, 201):
            error = ProviderError(_map_http_status(status))
            error._http_status = status
            raise error
        data = _parse_json(payload)
        if not isinstance(data, dict):
            raise ProviderError('cli_login_response_invalid')
        role = data.get('role')
        password = data.get('password')
        ttl = data.get('ttl_seconds')
        if not isinstance(role, str) or not role.strip():
            raise ProviderError('cli_login_response_invalid')
        if not isinstance(password, str) or not password:
            raise ProviderError('cli_login_response_invalid')
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
            raise ProviderError('cli_login_response_invalid')
    except ProviderError as exc:
        exc._operation = 'supabase_cli_login_post'
        raise
    except Exception:
        error = ProviderError('cli_login_response_invalid')
        error._operation = 'supabase_cli_login_post'
        raise error from None
    return CliLoginRole(role=role.strip(), password=_Secret(password), ttl_seconds=ttl)


def _connection_string_from_row(row: Mapping[str, Any]) -> str:
    for key in ('connection_string', 'connectionString'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ProviderError('pooler_connection_string_missing')


def _validate_pooler_host(host: str) -> str:
    if not isinstance(host, str) or not POOLER_HOST_RE.fullmatch(host):
        raise ProviderError('pooler_host_invalid')
    return host


def normalize_primary_session_pooler(
    rows: Any,
    *,
    project_ref: str,
) -> SessionPoolerTarget:
    """Derive the PRIMARY shared session-mode pooler target.

    Discards credentials in connection strings. The Management API can advertise
    transaction mode by default; the documented shared-host session endpoint is
    always port 5432. Never change project pooler settings or connect on 6543.
    https://supabase.com/docs/guides/database/connecting-to-postgres
    """
    ref = _require_ref(project_ref)
    if not isinstance(rows, list) or not rows:
        raise ProviderError('pooler_inventory_invalid')
    primary = None
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError('pooler_inventory_invalid')
        if row.get('database_type') == 'PRIMARY':
            if primary is not None:
                raise ProviderError('pooler_primary_ambiguous')
            primary = row
    if primary is None:
        raise ProviderError('pooler_primary_missing')

    db_name = primary.get('db_name')
    if not isinstance(db_name, str) or not db_name.strip():
        raise ProviderError('pooler_database_invalid')
    db_name = db_name.strip()

    pool_mode = primary.get('pool_mode')
    db_port = primary.get('db_port')
    # Metadata describes the advertised/default endpoint, not the absence of
    # session mode on this shared host. Select the documented 5432 endpoint.
    if pool_mode is not None and pool_mode not in ('session', 'transaction'):
        raise ProviderError('pooler_session_mode_required')
    if db_port is not None and (
        not isinstance(db_port, int)
        or isinstance(db_port, bool)
        or db_port not in (SESSION_POOLER_PORT, TRANSACTION_POOLER_PORT)
    ):
        raise ProviderError('pooler_port_invalid')

    raw_cs = _connection_string_from_row(primary)
    try:
        # Provider placeholders such as [YOUR-PASSWORD] are not IPv6 literals.
        # Escape brackets before URL parsing; credentials are discarded below.
        parsed = urlparse(raw_cs.replace('[', '%5B').replace(']', '%5D'))
        advertised_port = parsed.port or SESSION_POOLER_PORT
    except Exception:
        raise ProviderError('pooler_connection_string_invalid') from None
    if parsed.scheme not in ('postgres', 'postgresql'):
        raise ProviderError('pooler_connection_string_invalid')
    if parsed.hostname is None:
        raise ProviderError('pooler_host_invalid')
    host = _validate_pooler_host(parsed.hostname)
    if advertised_port not in (SESSION_POOLER_PORT, TRANSACTION_POOLER_PORT):
        raise ProviderError('pooler_port_invalid')
    if parsed.path and parsed.path not in ('', '/'):
        path_db = unquote(parsed.path.lstrip('/').split('/', 1)[0])
        if path_db and path_db != db_name:
            raise ProviderError('pooler_database_mismatch')

    # Username in shared pooler URLs is role.project_ref; bind the suffix only.
    username = unquote(parsed.username or '')
    if '.' not in username:
        # Metadata db_user may be the role prefix only; require project suffix
        # when the connection string carries a username.
        meta_user = primary.get('db_user')
        if isinstance(meta_user, str) and meta_user.strip():
            username = meta_user.strip()
    if username and '.' in username:
        _, suffix = username.rsplit('.', 1)
        if suffix != ref:
            raise ProviderError('pooler_project_ref_mismatch')
    meta_host = primary.get('db_host')
    if isinstance(meta_host, str) and meta_host.strip():
        if _validate_pooler_host(meta_host.strip()) != host:
            raise ProviderError('pooler_host_mismatch')

    return SessionPoolerTarget(
        project_ref=ref,
        host=host,
        port=SESSION_POOLER_PORT,
        database=db_name,
        db_user_suffix=ref,
    )


def fetch_primary_session_pooler(
    *,
    project_ref: str,
    access_token: str,
    transport: Transport,
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> SessionPoolerTarget:
    """GET /v1/projects/{ref}/config/database/pooler.

    Requires database_pooling_config_read. Never guesses hosts from region.
    """
    if not isinstance(access_token, str) or not access_token.strip():
        raise ProviderError('access_token_missing')
    url = pooler_url(project_ref)
    headers = {
        'Authorization': 'Bearer ' + access_token.strip(),
        'Accept': 'application/json',
    }
    try:
        status, payload = transport.request(
            method='GET', url=url, headers=headers, body=None, timeout=timeout)
    except ProviderError as exc:
        exc._operation = 'supabase_pooler_get'
        raise
    except Exception:
        error = ProviderError('provider_transport_failed')
        error._operation = 'supabase_pooler_get'
        raise error from None
    if status != 200:
        error = ProviderError(_map_http_status(status))
        error._operation = 'supabase_pooler_get'
        error._http_status = status
        raise error
    try:
        data = _parse_json(payload)
        return normalize_primary_session_pooler(data, project_ref=project_ref)
    except ProviderError as exc:
        exc._operation = 'supabase_pooler_get'
        raise


def pooler_login_username(role: str, project_ref: str) -> str:
    ref = _require_ref(project_ref)
    if not isinstance(role, str) or not role.strip() or '.' in role:
        raise ProviderError('cli_login_role_invalid')
    return f'{role.strip()}.{ref}'


def resolve_system_ssl_rootcert(
    *,
    candidates: Sequence[str] = SYSTEM_CA_CANDIDATES,
) -> str:
    """Select an explicit reviewed system CA bundle for libpq verify-full.

    verify-full alone does not load system roots in standard libpq; sslrootcert
    must be set. Fail closed when no known bundle exists — never downgrade.
    """
    for path in candidates:
        if not isinstance(path, str) or not path.startswith('/'):
            continue
        if os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    raise ProviderError('system_ca_unavailable')


def build_psycopg_dsn(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sslmode: str,
    sslrootcert: Optional[str] = None,
    loopback_fixture: bool = False,
) -> str:
    """Build a keyword DSN with libpq-safe quoting via make_conninfo.

    Real Supabase requires sslmode=verify-full plus an explicit system CA.
    Loopback fixtures may pass sslmode=disable explicitly; that is never a
    production policy bypass. Dynamic password/user/host values cannot inject
    extra libpq options.
    """
    if sslmode not in (PRODUCTION_SSLMODE, FIXTURE_SSLMODE):
        raise ProviderError('sslmode_unsupported')
    if not isinstance(host, str) or not host.strip():
        raise ProviderError('host_invalid')
    host = host.strip()
    if sslmode == FIXTURE_SSLMODE:
        if not loopback_fixture and host not in LOOPBACK_HOSTS:
            raise ProviderError('plaintext_tls_forbidden_for_non_loopback')
        if host not in LOOPBACK_HOSTS:
            raise ProviderError('plaintext_tls_forbidden_for_non_loopback')
        if sslrootcert is not None:
            raise ProviderError('sslrootcert_forbidden_for_plaintext_fixture')
    else:
        if host in LOOPBACK_HOSTS:
            raise ProviderError('verify_full_forbidden_for_loopback_fixture')
        if sslrootcert is None:
            sslrootcert = resolve_system_ssl_rootcert()
        if not isinstance(sslrootcert, str) or not sslrootcert.startswith('/'):
            raise ProviderError('sslrootcert_invalid')
        if not os.path.isfile(sslrootcert):
            raise ProviderError('system_ca_unavailable')
    if not isinstance(password, str) or not password:
        raise ProviderError('password_missing')
    if not isinstance(user, str) or not user:
        raise ProviderError('user_missing')
    if not isinstance(database, str) or not database:
        raise ProviderError('database_missing')
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        raise ProviderError('port_invalid') from None
    if port_i < 1 or port_i > 65535:
        raise ProviderError('port_invalid')

    try:
        from psycopg.conninfo import make_conninfo
    except ImportError:
        raise ProviderError('psycopg_required') from None

    kwargs: Dict[str, Any] = {
        'host': host,
        'port': port_i,
        'dbname': database,
        'user': user,
        'password': password,
        'sslmode': sslmode,
    }
    if sslrootcert is not None:
        kwargs['sslrootcert'] = sslrootcert
    try:
        return make_conninfo(**kwargs)
    except Exception:
        raise ProviderError('conninfo_build_failed') from None


def parse_conninfo(conninfo: str) -> Dict[str, str]:
    """Parse a libpq keyword conninfo string. Rejects URI form and extras."""
    if not isinstance(conninfo, str) or not conninfo.strip():
        raise ProviderError('runtime_secret_invalid')
    text = conninfo.strip()
    if '://' in text.split(None, 1)[0]:
        raise ProviderError('runtime_secret_uri_forbidden')
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        raise ProviderError('psycopg_required') from None
    try:
        parsed = conninfo_to_dict(text)
    except Exception:
        raise ProviderError('runtime_secret_invalid') from None
    if not isinstance(parsed, dict) or not parsed:
        raise ProviderError('runtime_secret_invalid')
    unknown = set(parsed) - ALLOWED_CONNINFO_KEYS
    if unknown:
        raise ProviderError('runtime_secret_unapproved_options')
    # Normalize values to str for stable comparison.
    return {str(k): '' if v is None else str(v) for k, v in parsed.items()}


def validate_runtime_conninfo(
    stored: str,
    *,
    expected_host: str,
    expected_port: int,
    expected_database: str,
    expected_user: str,
    expected_sslmode: str,
    expected_sslrootcert: Optional[str],
) -> ValidatedConninfo:
    """Validate a recovered custody value as a complete approved DSN.

    Rejects raw passwords, wrong binding fields, weakened TLS, wrong CA, and
    any unapproved libpq options. Password is never returned in repr.
    """
    if not isinstance(stored, str) or not stored.strip():
        raise ProviderError('runtime_secret_invalid')
    # Raw password alone has no keyword assignments.
    if 'host=' not in stored and 'dbname=' not in stored and 'user=' not in stored:
        raise ProviderError('runtime_secret_not_conninfo')
    parsed = parse_conninfo(stored)
    required = ('host', 'port', 'dbname', 'user', 'password', 'sslmode')
    for key in required:
        if key not in parsed or not parsed[key]:
            raise ProviderError('runtime_secret_incomplete')
    if parsed.get('host') != expected_host:
        raise ProviderError('runtime_secret_host_mismatch')
    try:
        port = int(parsed['port'])
    except ValueError:
        raise ProviderError('runtime_secret_port_mismatch') from None
    if port != int(expected_port):
        raise ProviderError('runtime_secret_port_mismatch')
    if parsed.get('dbname') != expected_database:
        raise ProviderError('runtime_secret_database_mismatch')
    if parsed.get('user') != expected_user:
        raise ProviderError('runtime_secret_user_mismatch')
    if parsed.get('sslmode') != expected_sslmode:
        raise ProviderError('runtime_secret_sslmode_mismatch')
    if expected_sslmode == PRODUCTION_SSLMODE:
        ca = parsed.get('sslrootcert')
        if not ca or ca != expected_sslrootcert:
            raise ProviderError('runtime_secret_sslrootcert_mismatch')
    elif expected_sslmode == FIXTURE_SSLMODE:
        if 'sslrootcert' in parsed and parsed['sslrootcert']:
            raise ProviderError('runtime_secret_sslrootcert_mismatch')
    else:
        raise ProviderError('runtime_secret_sslmode_mismatch')
    # Rebuild so stored quirks cannot introduce extras at connect time.
    rebuilt = build_psycopg_dsn(
        host=expected_host,
        port=int(expected_port),
        database=expected_database,
        user=expected_user,
        password=parsed['password'],
        sslmode=expected_sslmode,
        sslrootcert=expected_sslrootcert,
        loopback_fixture=(expected_sslmode == FIXTURE_SSLMODE),
    )
    return ValidatedConninfo(
        host=expected_host,
        port=int(expected_port),
        database=expected_database,
        user=expected_user,
        password=_Secret(parsed['password']),
        sslmode=expected_sslmode,
        sslrootcert=expected_sslrootcert,
        conninfo=rebuilt,
    )

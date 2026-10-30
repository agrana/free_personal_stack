#!/usr/bin/env python3
"""Owned Vercel project encrypted production env custody for runtime DB secrets.

Uses fixed api.vercel.com paths. Inventory before write; never overwrites
unknown values; never uses NEXT_PUBLIC / preview / development / plaintext
types. Migrator and Management credentials must not be stored here.

POST create responses are verified against OpenAPI envelopes (single object or
created/failed arrays). Opaque alphanumeric env IDs are accepted; path
traversal in IDs is rejected. Persistence is proven by re-inventory + decrypted
value readback, never by echoing a local password.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlencode

API_HOST = 'api.vercel.com'
REQUEST_TIMEOUT_SEC = 30
MAX_RESPONSE_BYTES = 2_000_000
PROJECT_ID_RE = re.compile(r'^prj_[A-Za-z0-9]+$')
TEAM_ID_RE = re.compile(r'^team_[A-Za-z0-9]+$')
# Official project env IDs are opaque strings (examples include alphanumeric
# and env_* forms). Reject path separators and traversal while accepting both.
ENV_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
RUNTIME_ENV_KEY = 'MDB_RUNTIME_DATABASE_URL'
OWNERSHIP_COMMENT_PREFIX = 'fps-mdb-runtime'


class CustodyError(Exception):
    """Custody failure with honest partial-write accounting."""

    def __init__(
        self,
        code: str,
        *,
        vercel_writes_attempted: int = 0,
        vercel_writes_confirmed: int = 0,
        custody_completion: str = 'failed',
        env_id: Optional[str] = None,
        recoverable_checkpoint: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(code)
        self.vercel_writes_attempted = int(vercel_writes_attempted)
        self.vercel_writes_confirmed = int(vercel_writes_confirmed)
        self.custody_completion = custody_completion
        self.env_id = env_id
        self.recoverable_checkpoint = (
            dict(recoverable_checkpoint) if recoverable_checkpoint else None)

    def __repr__(self) -> str:
        return f"CustodyError({str(self)!r})"

    def report_fields(self) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            'vercel_writes_attempted': self.vercel_writes_attempted,
            'vercel_writes_confirmed': self.vercel_writes_confirmed,
            'custody_completion': self.custody_completion,
        }
        if self.env_id is not None:
            fields['env_id'] = self.env_id
        if self.recoverable_checkpoint is not None:
            fields['recoverable_checkpoint'] = dict(self.recoverable_checkpoint)
        return fields


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
class EnvBinding:
    repository_id: str
    project_ref: str
    namespace: str
    vercel_project_id: str


@dataclass(frozen=True)
class EnvRecord:
    env_id: str
    key: str
    env_type: str
    targets: Tuple[str, ...]
    comment: Optional[str]


def ownership_comment(binding: EnvBinding) -> str:
    return (
        f'{OWNERSHIP_COMMENT_PREFIX};'
        f'repo={binding.repository_id};'
        f'ref={binding.project_ref};'
        f'ns={binding.namespace};'
        f'prj={binding.vercel_project_id}'
    )


def parse_ownership_comment(comment: str) -> EnvBinding:
    if not isinstance(comment, str) or not comment.startswith(OWNERSHIP_COMMENT_PREFIX + ';'):
        raise CustodyError('env_ownership_comment_invalid')
    parts = comment.split(';')
    if parts[0] != OWNERSHIP_COMMENT_PREFIX:
        raise CustodyError('env_ownership_comment_invalid')
    fields: Dict[str, str] = {}
    for part in parts[1:]:
        if '=' not in part:
            raise CustodyError('env_ownership_comment_invalid')
        key, value = part.split('=', 1)
        if key in fields or not value:
            raise CustodyError('env_ownership_comment_invalid')
        fields[key] = value
    required = ('repo', 'ref', 'ns', 'prj')
    if set(fields) != set(required):
        raise CustodyError('env_ownership_comment_invalid')
    return EnvBinding(
        repository_id=fields['repo'],
        project_ref=fields['ref'],
        namespace=fields['ns'],
        vercel_project_id=fields['prj'],
    )


def _map_http_status(status: int) -> str:
    if status in (401, 403):
        return 'provider_auth_or_permission_denied'
    if status == 429:
        return 'provider_throttled'
    if status in (408, 502, 503, 504) or 500 <= status <= 599:
        return 'provider_unavailable'
    if status in (301, 302, 303, 307, 308):
        return 'provider_redirect_rejected'
    if status == 409:
        return 'env_conflict'
    return 'provider_http_error'


def _require_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise CustodyError('vercel_project_id_invalid')
    return project_id


def _require_team_id(team_id: str) -> str:
    if not isinstance(team_id, str) or not TEAM_ID_RE.fullmatch(team_id):
        raise CustodyError('vercel_team_id_invalid')
    return team_id


def _require_env_id(env_id: str) -> str:
    if not isinstance(env_id, str) or not ENV_ID_RE.fullmatch(env_id):
        raise CustodyError('env_id_invalid')
    if '/' in env_id or '\\' in env_id or '..' in env_id or '%' in env_id:
        raise CustodyError('env_id_invalid')
    return env_id


def _parse_json(payload: bytes) -> Any:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise CustodyError('provider_response_too_large')
    try:
        return json.loads(payload.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise CustodyError('provider_response_invalid') from None


def _headers(token: str) -> Dict[str, str]:
    if not isinstance(token, str) or not token.strip():
        raise CustodyError('vercel_token_missing')
    return {
        'Authorization': 'Bearer ' + token.strip(),
        'Accept': 'application/json',
    }


def _request(
    transport: Transport,
    *,
    method: str,
    path: str,
    token: str,
    body: Optional[bytes] = None,
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> Tuple[int, Any]:
    if not path.startswith('/') or path.startswith('//'):
        raise CustodyError('api_route_invalid')
    if '/../' in path or path.endswith('/..'):
        raise CustodyError('api_route_invalid')
    url = 'https://' + API_HOST + path
    headers = _headers(token)
    if body is not None:
        headers = dict(headers)
        headers['Content-Type'] = 'application/json'
    try:
        status, payload = transport.request(
            method=method, url=url, headers=headers, body=body, timeout=timeout)
    except CustodyError:
        raise
    except Exception:
        raise CustodyError('provider_transport_failed') from None
    if status in (301, 302, 303, 307, 308):
        raise CustodyError('provider_redirect_rejected')
    data = None
    if payload:
        data = _parse_json(payload)
    return status, data


def _normalize_record(raw: Mapping[str, Any]) -> EnvRecord:
    env_id = raw.get('id')
    key = raw.get('key')
    env_type = raw.get('type')
    target = raw.get('target')
    comment = raw.get('comment')
    if not isinstance(env_id, str):
        raise CustodyError('env_record_invalid')
    env_id = _require_env_id(env_id)
    if not isinstance(key, str) or not key:
        raise CustodyError('env_record_invalid')
    if not isinstance(env_type, str) or not env_type:
        raise CustodyError('env_record_invalid')
    if not isinstance(target, list) or not all(isinstance(t, str) for t in target):
        raise CustodyError('env_record_invalid')
    if comment is not None and not isinstance(comment, str):
        raise CustodyError('env_record_invalid')
    return EnvRecord(
        env_id=env_id,
        key=key,
        env_type=env_type,
        targets=tuple(target),
        comment=comment,
    )


def list_project_envs(
    *,
    project_id: str,
    team_id: str,
    token: str,
    transport: Transport,
) -> List[EnvRecord]:
    project_id = _require_project_id(project_id)
    team_id = _require_team_id(team_id)
    query = urlencode({'teamId': team_id})
    path = f'/v10/projects/{project_id}/env?{query}'
    status, data = _request(transport, method='GET', path=path, token=token)
    if status != 200:
        raise CustodyError(_map_http_status(status))
    if not isinstance(data, dict):
        raise CustodyError('env_inventory_invalid')
    envs = data.get('envs')
    if not isinstance(envs, list):
        raise CustodyError('env_inventory_invalid')
    return [_normalize_record(row) for row in envs if isinstance(row, dict)]


def get_env_value(
    *,
    project_id: str,
    env_id: str,
    team_id: str,
    token: str,
    transport: Transport,
) -> _Secret:
    project_id = _require_project_id(project_id)
    team_id = _require_team_id(team_id)
    env_id = _require_env_id(env_id)
    query = urlencode({'teamId': team_id})
    path = f'/v1/projects/{project_id}/env/{env_id}?{query}'
    status, data = _request(transport, method='GET', path=path, token=token)
    if status != 200:
        raise CustodyError(_map_http_status(status))
    if not isinstance(data, dict):
        raise CustodyError('env_value_invalid')
    value = data.get('value')
    if not isinstance(value, str) or not value:
        raise CustodyError('env_value_unreadable')
    return _Secret(value)


def find_runtime_env(records: Sequence[EnvRecord]) -> Optional[EnvRecord]:
    matches = [r for r in records if r.key == RUNTIME_ENV_KEY]
    if not matches:
        return None
    if len(matches) > 1:
        raise CustodyError('env_runtime_key_ambiguous')
    return matches[0]


def assert_compatible_runtime_record(record: EnvRecord, binding: EnvBinding) -> None:
    if record.key != RUNTIME_ENV_KEY:
        raise CustodyError('env_key_mismatch')
    if record.env_type != 'encrypted':
        raise CustodyError('env_type_not_encrypted')
    if record.targets != ('production',) and set(record.targets) != {'production'}:
        if 'production' not in record.targets or len(record.targets) != 1:
            raise CustodyError('env_target_not_production_only')
    if record.comment is None:
        raise CustodyError('env_ownership_comment_missing')
    existing = parse_ownership_comment(record.comment)
    if existing != binding:
        raise CustodyError('env_ownership_binding_mismatch')


def _normalize_create_response(data: Any, *, binding: EnvBinding) -> EnvRecord:
    """Handle single-object and OpenAPI created/failed array envelopes.

    Any non-empty failed list or partial multi-create is failure/recovery, not
    silent success.
    """
    if not isinstance(data, dict):
        raise CustodyError('env_create_response_invalid')
    failed = data.get('failed')
    if isinstance(failed, list) and failed:
        raise CustodyError('env_create_partial_or_failed')
    created = data.get('created')
    if isinstance(created, list):
        if len(created) != 1 or not isinstance(created[0], dict):
            raise CustodyError('env_create_response_invalid')
        record = _normalize_record(created[0])
    elif isinstance(created, dict):
        record = _normalize_record(created)
    elif data.get('key') == RUNTIME_ENV_KEY and 'id' in data:
        record = _normalize_record(data)
    else:
        raise CustodyError('env_create_response_invalid')
    assert_compatible_runtime_record(record, binding)
    return record


def _uncertain_after_create_attempt(
    code: str,
    *,
    env_id: Optional[str] = None,
    checkpoint: Optional[Mapping[str, Any]] = None,
) -> CustodyError:
    """POST may have persisted; never report no-write after the attempt."""
    return CustodyError(
        code,
        vercel_writes_attempted=1,
        vercel_writes_confirmed=0,
        custody_completion='uncertain',
        env_id=env_id,
        recoverable_checkpoint=checkpoint or {
            'status': 'create_attempt_unconfirmed',
            'key': RUNTIME_ENV_KEY,
        },
    )


def create_runtime_env(
    *,
    project_id: str,
    team_id: str,
    token: str,
    binding: EnvBinding,
    value: str,
    transport: Transport,
) -> EnvRecord:
    """POST encrypted production-only runtime secret. Default: no overwrite.

    Validates response metadata only. Callers that need durable proof must use
    persist_runtime_env (re-inventory + decrypted readback). After the POST is
    issued, failures are uncertain (attempted=1), never silent no-write.
    """
    project_id = _require_project_id(project_id)
    team_id = _require_team_id(team_id)
    if binding.vercel_project_id != project_id:
        raise CustodyError('env_ownership_project_mismatch')
    if not isinstance(value, str) or not value:
        raise CustodyError('env_value_missing')
    if value.startswith('NEXT_PUBLIC_') or RUNTIME_ENV_KEY.startswith('NEXT_PUBLIC_'):
        raise CustodyError('env_public_key_forbidden')
    comment = ownership_comment(binding)
    body = json.dumps(
        {
            'key': RUNTIME_ENV_KEY,
            'value': value,
            'type': 'encrypted',
            'target': ['production'],
            'comment': comment,
        },
        separators=(',', ':'),
    ).encode('utf-8')
    query = urlencode({'teamId': team_id})
    path = f'/v10/projects/{project_id}/env?{query}'
    if not path.startswith('/') or path.startswith('//') or '/../' in path:
        raise CustodyError('api_route_invalid')
    url = 'https://' + API_HOST + path
    headers = dict(_headers(token))
    headers['Content-Type'] = 'application/json'
    pre_attempt_checkpoint = {
        'status': 'create_attempt_unconfirmed',
        'key': RUNTIME_ENV_KEY,
    }
    # Attempted once transport.request is invoked — reply loss still means
    # possible persistence; never report no-write after this point.
    try:
        status, payload = transport.request(
            method='POST',
            url=url,
            headers=headers,
            body=body,
            timeout=REQUEST_TIMEOUT_SEC,
        )
    except Exception:
        raise _uncertain_after_create_attempt(
            'provider_transport_failed',
            checkpoint=pre_attempt_checkpoint,
        ) from None
    if status in (301, 302, 303, 307, 308):
        raise _uncertain_after_create_attempt(
            'provider_redirect_rejected', checkpoint=pre_attempt_checkpoint)
    data = None
    if payload:
        try:
            data = _parse_json(payload)
        except CustodyError as exc:
            raise _uncertain_after_create_attempt(
                str(exc), checkpoint=pre_attempt_checkpoint) from None
    if status == 409:
        raise CustodyError('env_conflict')
    if status not in (200, 201):
        raise _uncertain_after_create_attempt(
            _map_http_status(status), checkpoint=pre_attempt_checkpoint)
    try:
        return _normalize_create_response(data, binding=binding)
    except CustodyError as exc:
        raise _uncertain_after_create_attempt(
            str(exc), checkpoint=pre_attempt_checkpoint) from None


def persist_runtime_env(
    *,
    project_id: str,
    team_id: str,
    token: str,
    binding: EnvBinding,
    value: str,
    transport: Transport,
) -> Dict[str, Any]:
    """Create, verify POST metadata, then re-read decrypted value for proof.

    On 409/race: re-inventory, validate the winner, compare exact value, and
    fail closed without overwrite. Never treats a local password echo as proof.
    After POST is attempted, every failure path reports uncertain custody with
    attempted=1 and a safe resume checkpoint — never vercel_writes=0.
    """
    project_id = _require_project_id(project_id)
    team_id = _require_team_id(team_id)
    if binding.vercel_project_id != project_id:
        raise CustodyError('env_ownership_project_mismatch')
    try:
        created = create_runtime_env(
            project_id=project_id,
            team_id=team_id,
            token=token,
            binding=binding,
            value=value,
            transport=transport,
        )
    except CustodyError as exc:
        if str(exc) != 'env_conflict':
            # Preserve uncertain accounting from create_runtime_env; never drop
            # attempted back to the CustodyError default of 0.
            if exc.custody_completion == 'uncertain' or exc.vercel_writes_attempted > 0:
                raise
            raise _uncertain_after_create_attempt(str(exc)) from None
        state = inspect_runtime_custody(
            project_id=project_id,
            team_id=team_id,
            token=token,
            binding=binding,
            transport=transport,
        )
        if state['status'] != 'reusable':
            raise CustodyError('env_conflict_unreadable') from None
        if state['value'].value != value:
            raise CustodyError('env_conflict_value_mismatch') from None
        return {
            'status': 'recovered_race_winner',
            'env_id': state['env_id'],
            'value': state['value'],
            'created': False,
        }

    # Re-inventory and decrypted readback — do not trust POST echo alone.
    # POST may have succeeded; never claim vercel_writes=0 after that point.
    checkpoint = {
        'status': 'created_readback_unconfirmed',
        'env_id': created.env_id,
        'key': RUNTIME_ENV_KEY,
    }
    try:
        try:
            records = list_project_envs(
                project_id=project_id, team_id=team_id, token=token, transport=transport)
            record = find_runtime_env(records)
            if record is None:
                raise _uncertain_after_create_attempt(
                    'env_persist_missing_after_create',
                    env_id=created.env_id,
                    checkpoint=checkpoint,
                )
            if record.env_id != created.env_id:
                raise _uncertain_after_create_attempt(
                    'env_persist_id_mismatch',
                    env_id=created.env_id,
                    checkpoint={
                        **checkpoint,
                        'inventory_env_id': record.env_id,
                    },
                )
            assert_compatible_runtime_record(record, binding)
            secret = get_env_value(
                project_id=project_id,
                env_id=record.env_id,
                team_id=team_id,
                token=token,
                transport=transport,
            )
            if secret.value != value:
                raise _uncertain_after_create_attempt(
                    'env_persist_value_mismatch',
                    env_id=record.env_id,
                    checkpoint=checkpoint,
                )
        except CustodyError as exc:
            if exc.custody_completion == 'uncertain' or exc.vercel_writes_attempted > 0:
                raise
            raise _uncertain_after_create_attempt(
                str(exc),
                env_id=created.env_id,
                checkpoint=checkpoint,
            ) from None
    except CustodyError:
        raise
    except Exception:
        raise _uncertain_after_create_attempt(
            'env_persist_readback_failed',
            env_id=created.env_id,
            checkpoint=checkpoint,
        ) from None
    return {
        'status': 'persisted',
        'env_id': record.env_id,
        'value': secret,
        'created': True,
        'vercel_writes_attempted': 1,
        'vercel_writes_confirmed': 1,
        'custody_completion': 'confirmed',
    }


def inspect_runtime_custody(
    *,
    project_id: str,
    team_id: str,
    token: str,
    binding: EnvBinding,
    transport: Transport,
) -> Dict[str, Any]:
    """Inspect inventory for the runtime key without mutating.

    Returns status among: absent, reusable, recovery_required, conflict.
    """
    if binding.vercel_project_id != project_id:
        raise CustodyError('env_ownership_project_mismatch')
    records = list_project_envs(
        project_id=project_id, team_id=team_id, token=token, transport=transport)
    # Reject unexpected keys that collide with delivery naming.
    for record in records:
        if record.key.startswith('MDB_') and record.key != RUNTIME_ENV_KEY:
            raise CustodyError('env_unknown_mdb_key')
        if record.key == RUNTIME_ENV_KEY:
            continue
    record = find_runtime_env(records)
    if record is None:
        return {'status': 'absent', 'env_id': None}
    try:
        assert_compatible_runtime_record(record, binding)
    except CustodyError as exc:
        code = str(exc)
        if code in (
            'env_ownership_comment_missing',
            'env_ownership_comment_invalid',
            'env_ownership_binding_mismatch',
            'env_type_not_encrypted',
            'env_target_not_production_only',
        ):
            return {'status': 'recovery_required', 'env_id': record.env_id, 'reason': code}
        raise
    try:
        secret = get_env_value(
            project_id=project_id,
            env_id=record.env_id,
            team_id=team_id,
            token=token,
            transport=transport,
        )
    except CustodyError as exc:
        if str(exc) in ('env_value_unreadable', 'provider_auth_or_permission_denied'):
            return {
                'status': 'recovery_required',
                'env_id': record.env_id,
                'reason': 'env_value_unreadable',
            }
        raise
    return {
        'status': 'reusable',
        'env_id': record.env_id,
        'value': secret,
    }

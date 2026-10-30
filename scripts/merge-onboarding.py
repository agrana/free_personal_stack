#!/usr/bin/env python3
"""Merge-driven shell deploy + hostname/DNS orchestration for generated apps.

Triggered on default-branch push. Authorization is the merge-reviewed
onboarding.config.json (auto_apply + hostname/zone). Never spoofs
GITHUB_EVENT_NAME. Provider writes require a successful current-head App Quality
run. Optional opt-in database-onboarding.config.json runs read-only managed
catalog preflight after quality and before shell writes; success does not
authorize migrations. Optional database-delivery.config.json (template
enabled/auto_apply false) can enroll an owned Vercel project, run authorized
delivery, then production deploy + DNS. This path never claims full-app-ready.

Quality-gate selection policy: among runs that match the expected workflow path,
head SHA, default branch, event, and repository identity, choose the highest
workflow run ``id`` and, for ties, the highest ``run_attempt``. That newest
valid matching run is authoritative; an older success never overrides a newer
failed or pending run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UPSTREAM_ID = '1051601680'
PLACEHOLDER = 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
SHELL_SECRETS = ('VERCEL_API_TOKEN', 'VERCEL_TEAM_ID', 'SUPABASE_URL', 'SUPABASE_ANON_KEY')
DOMAIN_SECRETS = ('VERCEL_API_TOKEN', 'VERCEL_TEAM_ID', 'CLOUDFLARE_API_TOKEN')
DATABASE_PREFLIGHT_SECRETS = ('SUPABASE_URL', 'SUPABASE_ACCESS_TOKEN')
DATABASE_DELIVERY_SECRETS = (
    'SUPABASE_URL', 'SUPABASE_ACCESS_TOKEN', 'VERCEL_API_TOKEN', 'VERCEL_TEAM_ID',
)
SECRET_ENV_KEYS = (*SHELL_SECRETS, 'CLOUDFLARE_API_TOKEN', 'SUPABASE_ACCESS_TOKEN', 'GITHUB_TOKEN')
QUALITY_WORKFLOW = 'app-quality.yml'
QUALITY_WORKFLOW_PATH = '.github/workflows/' + QUALITY_WORKFLOW
QUALITY_TIMEOUT_SEC = 1500
QUALITY_POLL_SEC = 15
# DNS/TLS activation often exceeds a one-minute window; bound overall verify.
VERIFY_WINDOW_SEC = 20 * 60
VERIFY_POLL_SEC = 15
DOMAIN_SUBPROCESS_CAP_SEC = 900
SHELL_SUBPROCESS_CAP_SEC = 900
DOMAIN_REPLAN_BOUND = 4
ORCHESTRATION_LIFETIME_SEC = 40 * 60
ALLOWED_SHELL_STATUSES = frozenset({
    'page_and_health_verified',
    'ready_but_verification_protected',
})
ALLOWED_ENROLL_STATUSES = frozenset({
    'project_enrolled',
})
DELIVERY_SUCCESS_STATUSES = frozenset({
    'delivery_completed_local_runtime_proof',
})
DELIVERY_SKIP_STATUSES = frozenset({
    'delivery_disabled',
    'delivery_auto_apply_disabled',
})
PENDING_VERIFY_STATUSES = frozenset({
    'verification_pending',
    'verification_incomplete',
})
HARD_FAIL_DOMAIN_STATUSES = frozenset({
    'deployment_changed_replanning_required',
    'source_changed_replanning_required',
    'expected_deployment_mismatch',
    'expected_project_mismatch',
    'merge_app_deployment_sha_mismatch',
})


class Failure(Exception):
    pass


def load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def emit(**fields):
    text = json.dumps(fields, sort_keys=True)
    print(text, flush=True)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write('```json\n' + text + '\n```\n')


def write_artifact(directory, name, payload):
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github(path, token=None):
    auth = token if token is not None else os.environ.get('GITHUB_TOKEN', '')
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + auth,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise Failure('github_response_bound')
        data = json.loads(body)
        return data
    except (OSError, ValueError, urllib.error.URLError):
        raise Failure('github_discovery_failed') from None


def command(args, source, timeout=30):
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'HOME', 'TMPDIR', 'CI', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    try:
        result = subprocess.run(
            args, cwd=source, env=env, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('command_transport') from None
    if result.returncode:
        raise Failure('source_check_failed')
    return result.stdout.strip()


def missing_secrets(names):
    return [k for k in names
            if not os.environ.get(k, '').strip() or os.environ[k].strip() == PLACEHOLDER]


def require_positive_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Failure(label + '_invalid')
    return value


def require_project_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'prj_[A-Za-z0-9]+', value):
        raise Failure('project_id_invalid')
    return value


def require_deployment_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'dpl_[A-Za-z0-9]+', value):
        raise Failure('deployment_id_invalid')
    return value


def repository_context(source):
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    rid = os.environ.get('GITHUB_REPOSITORY_ID', '')
    sha = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Failure('repository_invalid')
    if not re.fullmatch(r'[1-9][0-9]*', rid) or rid == UPSTREAM_ID:
        raise Failure('template_or_repository_invalid')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise Failure('source_sha_invalid')
    metadata = github('/repositories/' + rid)
    if (str(metadata.get('id')) != rid or metadata.get('full_name') != repo
            or metadata.get('is_template') is not False or metadata.get('fork') is not False):
        raise Failure('repository_identity_mismatch')
    branch = metadata.get('default_branch')
    if not isinstance(branch, str) or not branch:
        raise Failure('default_branch_invalid')
    # Preserve original event identity — never spoof GITHUB_EVENT_NAME.
    if (os.environ.get('GITHUB_EVENT_NAME') != 'push'
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + branch):
        raise Failure('event_not_authorized')
    remote = github('/repos/' + repo + '/commits/' + urllib.parse.quote(branch, safe=''))
    if remote.get('sha') != sha or command(['git', 'rev-parse', 'HEAD'], source) != sha:
        raise Failure('source_sha_mismatch')
    if command(['git', 'status', '--porcelain', '--untracked-files=all'], source):
        raise Failure('source_dirty')
    return rid, repo, sha, branch


def quality_runs(repo, sha):
    query = urllib.parse.urlencode({
        'per_page': 20,
        'head_sha': sha,
    })
    path = '/repos/' + repo + '/actions/workflows/' + urllib.parse.quote(QUALITY_WORKFLOW, safe='')
    path += '/runs?' + query
    data = github(path)
    if not isinstance(data, dict):
        raise Failure('quality_gate_lookup_failed')
    runs = data.get('workflow_runs')
    if not isinstance(runs, list):
        raise Failure('quality_gate_lookup_failed')
    return runs


def classify_quality_run(run, sha, default_branch, expected_repo_id, expected_full_name):
    """Return (class, meta_or_none). Classes: success|failed|pending|invalid|stale|wrong_*."""
    if not isinstance(run, dict):
        return 'invalid', None
    try:
        run_id = require_positive_int(run.get('id'), 'quality_run_id')
        run_attempt = require_positive_int(run.get('run_attempt'), 'quality_run_attempt')
        workflow_id = require_positive_int(run.get('workflow_id'), 'quality_workflow_id')
    except Failure:
        return 'invalid', None
    path = run.get('path')
    if path != QUALITY_WORKFLOW_PATH:
        if isinstance(path, str) and path:
            return 'wrong_workflow', None
        return 'invalid', None
    if run.get('head_sha') != sha:
        return 'stale', None
    event = run.get('event')
    if event not in ('push', 'workflow_dispatch'):
        return 'wrong_event', None
    head_branch = run.get('head_branch')
    if head_branch != default_branch:
        # Missing/wrong branch rejected for push and for dispatch on another branch.
        return 'wrong_branch', None
    head_repo = run.get('head_repository')
    if not isinstance(head_repo, dict):
        return 'invalid', None
    head_id = head_repo.get('id')
    head_name = head_repo.get('full_name')
    if (not isinstance(head_id, int) or isinstance(head_id, bool)
            or str(head_id) != str(expected_repo_id)
            or head_name != expected_full_name):
        return 'wrong_repository', None
    meta = {
        'workflow_id': workflow_id,
        'run_id': run_id,
        'run_attempt': run_attempt,
        'event': event,
        'head_sha': sha,
        'head_branch': head_branch,
        'repository_id': str(head_id),
        'repository_full_name': head_name,
        'path': path,
        'status': run.get('status'),
        'conclusion': run.get('conclusion'),
    }
    status = run.get('status')
    conclusion = run.get('conclusion')
    if status in ('queued', 'in_progress', 'waiting', 'requested', 'pending'):
        return 'pending', meta
    if status != 'completed':
        return 'failed', meta
    if conclusion == 'success':
        return 'success', meta
    return 'failed', meta


def select_authoritative_quality_run(runs, sha, default_branch, expected_repo_id, expected_full_name):
    """Newest valid matching run wins (max run id, then max run_attempt)."""
    candidates = []
    saw_invalid = False
    saw_stale_only = True
    for run in runs:
        kind, meta = classify_quality_run(
            run, sha, default_branch, expected_repo_id, expected_full_name)
        if kind in ('invalid', 'wrong_workflow', 'wrong_event', 'wrong_branch', 'wrong_repository'):
            saw_invalid = True
            continue
        if kind == 'stale':
            continue
        saw_stale_only = False
        candidates.append((kind, meta))
    if not candidates:
        if runs and saw_stale_only and not saw_invalid:
            return 'stale', None
        if runs and saw_invalid and saw_stale_only:
            return 'invalid', None
        return 'missing', None
    candidates.sort(key=lambda item: (item[1]['run_id'], item[1]['run_attempt']), reverse=True)
    return candidates[0]


def wait_for_quality_gate(
        repo, sha, default_branch, expected_repo_id, expected_full_name,
        *, timeout_sec=None, poll_sec=None, sleeper=None, clock=None):
    timeout_sec = QUALITY_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    poll_sec = QUALITY_POLL_SEC if poll_sec is None else poll_sec
    sleeper = time.sleep if sleeper is None else sleeper
    clock = time.monotonic if clock is None else clock
    deadline = clock() + timeout_sec
    saw_pending = False
    while True:
        runs = quality_runs(repo, sha)
        kind, meta = select_authoritative_quality_run(
            runs, sha, default_branch, expected_repo_id, expected_full_name)
        if kind == 'success':
            return {
                'status': 'quality_gate_passed',
                'head_sha': sha,
                'workflow_id': meta['workflow_id'],
                'run_id': meta['run_id'],
                'run_attempt': meta['run_attempt'],
                'event': meta['event'],
                'head_branch': meta['head_branch'],
                'repository_id': meta['repository_id'],
                'repository_full_name': meta['repository_full_name'],
                'path': meta['path'],
                'selection_policy': 'newest_matching_run_id_then_run_attempt',
            }
        if kind == 'failed':
            raise Failure('quality_gate_failed')
        if kind == 'pending':
            saw_pending = True
        if kind == 'stale':
            raise Failure('quality_gate_stale')
        if kind == 'invalid':
            raise Failure('quality_gate_invalid')
        if clock() >= deadline:
            if kind == 'pending' or saw_pending:
                raise Failure('quality_gate_timeout')
            raise Failure('quality_gate_missing')
        sleeper(poll_sec)


def decode_captured(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, str):
        return value
    return str(value)


def sanitize_captured(text, env):
    out = text
    for key in SECRET_ENV_KEYS:
        value = env.get(key, '')
        if value and value != PLACEHOLDER:
            out = out.replace(value, '[redacted]')
    return out


def parse_json_events(stdout):
    lines = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            lines.append(parsed)
    return lines


def enrollment_team_binding(team_id):
    """Fingerprint a validated, non-credential provider team identifier."""
    if not isinstance(team_id, str) or not re.fullmatch(r'team_[A-Za-z0-9]+', team_id):
        return None
    return hashlib.sha256(team_id.encode('utf-8')).hexdigest()


def persist_stage_capture(artifact_dir, name, *, returncode, events, stdout, stderr,
                          timed_out=False, timeout_sec=None, stage=None):
    payload = {
        'returncode': returncode,
        'events': events,
        'stderr': stderr[-4000:],
        'stdout_tail': stdout[-4000:],
        'timed_out': bool(timed_out),
    }
    if timed_out:
        payload['timeout_sec'] = timeout_sec
        payload['stage_timeout'] = True
        payload['stage'] = stage
    write_artifact(artifact_dir, name, payload)
    return payload


def resolve_orchestrator_python():
    """Prefer the workflow-selected delivery interpreter when present."""
    configured = os.environ.get('MDB_DELIVERY_PYTHON', '').strip()
    if configured:
        return configured
    marker = os.environ.get('MDB_DELIVERY_INTERPRETER_FILE', '').strip()
    if marker:
        path = Path(marker)
        if path.is_file():
            text = path.read_text(encoding='utf-8').strip()
            if text:
                return text
    return sys.executable


def run_python_script(script, source, binary, extra_args, env_updates, artifact_dir,
                      *, timeout=DOMAIN_SUBPROCESS_CAP_SEC, capture_name=None, stage=None):
    env = os.environ.copy()
    env.update(env_updates)
    python = resolve_orchestrator_python()
    # Never forward secret values into argv.
    args = [
        python, str(Path(__file__).with_name(script)),
        '--source', str(source),
        '--vercel-bin', binary,
        *extra_args,
    ]
    timed_out = False
    returncode = None
    stdout = ''
    stderr = ''
    try:
        result = subprocess.run(
            args, cwd=source, env=env, text=True, capture_output=True, timeout=timeout)
        returncode = result.returncode
        stdout = decode_captured(result.stdout)
        stderr = decode_captured(result.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = decode_captured(exc.stdout)
        stderr = decode_captured(exc.stderr)
        returncode = None
    except OSError:
        raise Failure('stage_command_transport') from None
    # Binding is operational data; report redaction must not change its meaning.
    # Always overwrite a child-supplied fingerprint using the captured team ID.
    enrollment_bindings = [
        enrollment_team_binding(event.get('team_id'))
        if event.get('status') == 'project_enrolled' else None
        for event in parse_json_events(stdout)
    ]
    stdout = sanitize_captured(stdout, env)
    stderr = sanitize_captured(stderr, env)
    lines = parse_json_events(stdout)
    if len(lines) != len(enrollment_bindings):
        raise Failure('stage_event_redaction_mismatch')
    for event, binding in zip(lines, enrollment_bindings):
        if event.get('status') == 'project_enrolled':
            event['enrollment_team_binding_sha256'] = binding
    if capture_name:
        persist_stage_capture(
            artifact_dir, capture_name, returncode=returncode, events=lines,
            stdout=stdout, stderr=stderr, timed_out=timed_out, timeout_sec=timeout,
            stage=stage or script)
    if timed_out:
        raise Failure('stage_timeout')
    return returncode, lines, stdout, stderr


def extract_shell_binding(lines, expected_sha):
    for event in reversed(lines):
        status = event.get('status')
        if status not in ALLOWED_SHELL_STATUSES:
            continue
        project_id = require_project_id(event.get('project_id'))
        deployment_id = require_deployment_id(event.get('deployment_id'))
        source_sha = event.get('source_sha')
        if source_sha != expected_sha:
            raise Failure('shell_source_sha_mismatch')
        return status, project_id, deployment_id, source_sha
    raise Failure('shell_binding_missing')


def extract_enroll_binding(lines, expected_sha, expected_repository_id, expected_team_id):
    for event in reversed(lines):
        if event.get('status') != 'project_enrolled':
            continue
        project_id = require_project_id(event.get('project_id'))
        repository_id = str(event.get('repository_id') or '')
        team_id = str(event.get('team_id') or '')
        source_sha = event.get('source_sha')
        if source_sha != expected_sha:
            raise Failure('enroll_source_sha_mismatch')
        if repository_id != expected_repository_id:
            raise Failure('enroll_repository_mismatch')
        binding = event.get('enrollment_team_binding_sha256')
        expected_binding = enrollment_team_binding(expected_team_id)
        if binding is not None:
            if (not expected_binding or binding != expected_binding
                    or team_id not in (expected_team_id, '[redacted]')):
                raise Failure('enroll_team_mismatch')
        elif team_id != expected_team_id:
            raise Failure('enroll_team_mismatch')
        return project_id, repository_id, team_id, source_sha
    raise Failure('enroll_binding_missing')


def database_gap_report(preflight=None, delivery=None):
    report = {
        'status': 'database_stages_unimplemented',
        'required_missing_stages': [
            'database_connection_verification',
            'app_scoped_schema_migrations',
            'grants_and_rls_isolation',
        ],
        'note': (
            'Merge-driven shell deploy and hostname/DNS do not complete onboarding. '
            'Live database/migration/RLS orchestration remains an implementation gap.'
        ),
        'database_ready': False,
        'live_migrations_authorized': False,
        'full_app_ready': False,
        'catalog_inspection_completed': False,
    }
    if isinstance(preflight, dict):
        report['preflight_status'] = preflight.get('status')
        if preflight.get('catalog_inspection_completed') is True:
            report['catalog_inspection_completed'] = True
            report['status'] = 'database_catalog_inspected_stages_incomplete'
            report['note'] = (
                'Read-only managed-service catalog preflight completed. '
                'Shell/DNS may continue, but database readiness, live migrations, '
                'and RLS isolation remain incomplete.'
            )
            if isinstance(preflight.get('required_next_steps'), list):
                report['required_next_steps'] = list(preflight['required_next_steps'])
    if isinstance(delivery, dict):
        delivery_status = delivery.get('status')
        report['delivery_status'] = delivery_status
        if delivery.get('namespace') is not None:
            report['delivery_namespace'] = delivery.get('namespace')
        if delivery.get('delivery_binding_sha256'):
            report['delivery_binding_sha256'] = delivery.get('delivery_binding_sha256')
        completed = []
        if delivery_status in DELIVERY_SUCCESS_STATUSES:
            if delivery.get('provision_created') is not None or delivery_status:
                completed.append('managed_provision_or_ownership_verified')
            if int(delivery.get('migrations_applied') or 0) > 0 or delivery_status:
                completed.append('app_scoped_schema_migrations_applied')
            if delivery.get('local_runtime_proof') or delivery_status:
                completed.append('local_runtime_login_rls_proof')
            report['completed_delivery_stages'] = completed
            report['required_missing_stages'] = [
                'deployed_http_jwt_data_path_verification',
            ]
            report['status'] = (
                'database_delivery_completed_deployed_verification_pending'
            )
            report['live_migrations_authorized'] = True
            report['note'] = (
                'Managed delivery completed provision/migrations/local runtime '
                'proof. database_ready and full_app_ready stay false until '
                'deployed HTTP/JWT/data-path verification.'
            )
        elif delivery_status in DELIVERY_SKIP_STATUSES:
            report['delivery_skipped'] = True
        elif delivery_status in ('setup_required', 'recovery_required'):
            report['status'] = 'database_delivery_' + str(delivery_status)
            report['note'] = (
                'Managed delivery did not complete; shell/DNS must not claim '
                'database stages finished.'
            )
    report['database_ready'] = False
    report['full_app_ready'] = False
    return report


def finalize(artifact_dir, payload, code):
    write_artifact(artifact_dir, 'merge-onboarding-result.json', payload)
    emit(**payload)
    return code


def remaining_timeout(deadline, clock, cap):
    left = int(deadline - clock())
    if left <= 0:
        raise Failure('orchestration_lifetime_exceeded')
    return max(1, min(cap, left))


def run(source, binary, artifact_dir, *, quality_wait=None, sleeper=None, clock=None,
        verify_window_sec=None, orchestration_lifetime_sec=None):
    artifact_dir = str(Path(artifact_dir).resolve())
    Path(artifact_dir).mkdir(parents=True, exist_ok=True)
    cfg_mod = load('onboarding-config')
    sleeper = time.sleep if sleeper is None else sleeper
    clock = time.monotonic if clock is None else clock
    verify_window_sec = VERIFY_WINDOW_SEC if verify_window_sec is None else verify_window_sec
    orchestration_lifetime_sec = (
        ORCHESTRATION_LIFETIME_SEC if orchestration_lifetime_sec is None
        else orchestration_lifetime_sec)
    orch_deadline = clock() + orchestration_lifetime_sec
    result = {
        'status': 'starting',
        'full_app_ready': False,
        'stages': {},
        'database': database_gap_report(),
    }
    try:
        rid, repo, sha, branch = repository_context(source)
        result['repository_id'] = rid
        result['source_sha'] = sha
        result['default_branch'] = branch
        try:
            config = cfg_mod.load_onboarding_config(
                source, require_tracked_clean=True, expected_sha=sha)
        except cfg_mod.ConfigError as exc:
            raise Failure(str(exc)) from None
        result['config'] = {
            'auto_apply': config['auto_apply'],
            'hostname': config['hostname'],
            'cloudflare_zone_name': config['cloudflare_zone_name'],
            'recovery_configured': bool(config['expected_project_id']),
        }
        if not config['auto_apply']:
            result['status'] = 'onboarding_auto_apply_disabled'
            result['stages']['provider_writes'] = 'skipped'
            return finalize(artifact_dir, result, 0)
        if not config['hostname'] or not config['cloudflare_zone_name']:
            result['status'] = 'hostname_required_before_provisioning'
            result['stages']['provider_writes'] = 'blocked'
            return finalize(artifact_dir, result, 1)

        shell_missing = missing_secrets(SHELL_SECRETS)
        domain_missing = missing_secrets(DOMAIN_SECRETS)
        if shell_missing or domain_missing:
            result['status'] = 'setup_required'
            result['required_secret_names'] = sorted(set(shell_missing + domain_missing))
            result['stages']['provider_writes'] = 'blocked'
            return finalize(artifact_dir, result, 0)

        waiter = quality_wait or wait_for_quality_gate
        quality = waiter(repo, sha, branch, rid, repo)
        result['stages']['quality_gate'] = quality
        write_artifact(artifact_dir, 'quality-gate.json', quality)

        # Recheck source/config immediately before provider writes.
        repository_context(source)
        config = cfg_mod.load_onboarding_config(
            source, require_tracked_clean=True, expected_sha=sha)

        db_preflight = load('database-preflight')
        try:
            preflight_report = db_preflight.run_for_merge(
                source, expected_sha=sha, artifact_dir=Path(artifact_dir))
        except db_preflight.PreflightError as exc:
            result['status'] = 'database_preflight_failed'
            result['stages']['database_preflight'] = {
                'status': 'preflight_failed',
                'error': str(exc),
            }
            result['database'] = database_gap_report(result['stages']['database_preflight'])
            result['stages']['provider_writes'] = 'blocked'
            return finalize(artifact_dir, result, 1)
        result['stages']['database_preflight'] = {
            'status': preflight_report.get('status'),
            'catalog_inspection_completed': bool(
                preflight_report.get('catalog_inspection_completed')),
            'config_status': preflight_report.get('config_status'),
        }
        if preflight_report.get('status') == 'setup_required':
            result['status'] = 'setup_required'
            result['required_secret_names'] = list(
                preflight_report.get('required_secret_names') or DATABASE_PREFLIGHT_SECRETS)
            result['database'] = database_gap_report(preflight_report)
            result['stages']['provider_writes'] = 'blocked'
            return finalize(artifact_dir, result, 0)
        if preflight_report.get('status') not in (
                'preflight_disabled', 'catalog_inspection_completed'):
            result['status'] = 'database_preflight_failed'
            result['database'] = database_gap_report(preflight_report)
            result['stages']['provider_writes'] = 'blocked'
            return finalize(artifact_dir, result, 1)
        result['database'] = database_gap_report(preflight_report)

        # Default-disabled delivery: decision only (no network) when disabled.
        # Enabled+auto_apply: enroll → authorized delivery → production deploy.
        db_delivery = load('managed_database_delivery')
        delivery_cfg_mod = load('database-delivery-config')
        try:
            delivery_config = delivery_cfg_mod.load_database_delivery_config(
                source, require_tracked_clean=True, expected_sha=sha)
        except delivery_cfg_mod.ConfigError as exc:
            result['status'] = 'database_delivery_failed'
            result['stages']['database_delivery'] = {
                'status': 'delivery_config_invalid',
                'error': str(exc),
            }
            result['stages']['provider_writes'] = 'blocked'
            return finalize(artifact_dir, result, 1)

        delivery_live = (
            delivery_cfg_mod.delivery_enabled(delivery_config)
            and delivery_cfg_mod.delivery_auto_apply(delivery_config)
        )
        delivery_binding_digest = None
        delivery_report = None
        enrolled_project_id = None
        # Clear any inherited delivery binding; only a current verified delivery
        # may set this for the deployment stage.
        launch_env = {
            'APP_HOSTNAME': config['hostname'],
            'CLOUDFLARE_ZONE_NAME': config['cloudflare_zone_name'],
            'ONBOARDING_MERGE_POLICY': '1',
            'ONBOARDING_DELIVERY_BINDING_SHA256': '',
        }
        recovery_args = []
        if config['expected_project_id']:
            recovery_args.extend([
                '--expected-project-id', config['expected_project_id'],
                '--confirm-repository-id', config['confirm_repository_id'],
            ])

        if delivery_live:
            delivery_missing = missing_secrets(DATABASE_DELIVERY_SECRETS)
            if delivery_missing:
                result['status'] = 'setup_required'
                result['required_secret_names'] = delivery_missing
                result['stages']['database_delivery'] = {
                    'status': 'setup_required',
                    'required_secret_names': delivery_missing,
                }
                result['database'] = database_gap_report(
                    preflight_report,
                    {'status': 'setup_required'},
                )
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 0)

            enroll_args = ['--operation', 'enroll', *recovery_args]
            enroll_timeout = remaining_timeout(
                orch_deadline, clock, SHELL_SUBPROCESS_CAP_SEC)
            enroll_code, enroll_lines, _eo, _ee = run_python_script(
                'launch-app.py', source, binary, enroll_args, launch_env,
                artifact_dir, timeout=enroll_timeout,
                capture_name='launch-app-enroll-stdout.json', stage='project_enroll')
            result['stages']['project_enroll'] = (
                enroll_lines[-1] if enroll_lines else {
                    'status': 'no_output', 'returncode': enroll_code})
            enroll_status = (enroll_lines[-1] or {}).get('status') if enroll_lines else None
            if enroll_status == 'setup_required':
                result['status'] = 'setup_required'
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 0)
            if enroll_status not in ALLOWED_ENROLL_STATUSES or enroll_code != 0:
                result['status'] = 'project_enroll_failed'
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)
            team_id = os.environ.get('VERCEL_TEAM_ID', '')
            try:
                project_id, enrolled_rid, enrolled_team, _esh = extract_enroll_binding(
                    enroll_lines, sha, rid, team_id)
            except Failure as exc:
                result['status'] = str(exc)
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)
            enrolled_project_id = project_id
            result['stages']['project_enroll_binding'] = {
                'status': 'project_enrolled',
                'project_id': project_id,
                'repository_id': enrolled_rid,
                'team_id': enrolled_team,
                'source_sha': sha,
            }
            write_artifact(
                artifact_dir, 'project-enroll-binding.json',
                result['stages']['project_enroll_binding'])

            repository_context(source)
            try:
                delivery_report = db_delivery.run_for_merge(
                    source,
                    expected_sha=sha,
                    artifact_dir=Path(artifact_dir),
                    vercel_project_id=project_id,
                    repository_id=rid,
                    quality_evidence=quality,
                )
            except db_delivery.DeliveryError as exc:
                result['status'] = 'database_delivery_failed'
                result['stages']['database_delivery'] = {
                    'status': 'delivery_failed',
                    'error': str(exc),
                }
                result['database'] = database_gap_report(
                    preflight_report, result['stages']['database_delivery'])
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)
            result['stages']['database_delivery'] = {
                'status': delivery_report.get('status'),
                'config_status': delivery_report.get('config_status'),
                'namespace': delivery_report.get('namespace'),
                'delivery_binding_sha256': delivery_report.get('delivery_binding_sha256'),
                'provision_created': delivery_report.get('provision_created'),
                'migrations_applied': delivery_report.get('migrations_applied'),
                'local_runtime_proof': bool(delivery_report.get('local_runtime_proof')),
            }
            result['database'] = database_gap_report(preflight_report, delivery_report)
            if delivery_report.get('status') == 'setup_required':
                result['status'] = 'setup_required'
                result['required_secret_names'] = list(
                    delivery_report.get('required_secret_names')
                    or DATABASE_DELIVERY_SECRETS)
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 0)
            if delivery_report.get('status') not in DELIVERY_SUCCESS_STATUSES:
                result['status'] = 'database_delivery_failed'
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)
            delivery_binding_digest = delivery_report.get('delivery_binding_sha256')
            if (not isinstance(delivery_binding_digest, str)
                    or not re.fullmatch(r'[0-9a-f]{64}', delivery_binding_digest)):
                result['status'] = 'database_delivery_binding_missing'
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)
            launch_env['ONBOARDING_DELIVERY_BINDING_SHA256'] = delivery_binding_digest
        else:
            try:
                delivery_report = db_delivery.run_for_merge(
                    source, expected_sha=sha, artifact_dir=Path(artifact_dir),
                    repository_id=rid)
            except db_delivery.DeliveryError as exc:
                result['status'] = 'database_delivery_failed'
                result['stages']['database_delivery'] = {
                    'status': 'delivery_failed',
                    'error': str(exc),
                }
                result['database'] = database_gap_report(
                    preflight_report, result['stages']['database_delivery'])
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)
            result['stages']['database_delivery'] = {
                'status': delivery_report.get('status'),
                'config_status': delivery_report.get('config_status'),
                'namespace': delivery_report.get('namespace'),
            }
            result['database'] = database_gap_report(preflight_report, delivery_report)
            if delivery_report.get('status') == 'setup_required':
                result['status'] = 'setup_required'
                result['required_secret_names'] = list(
                    delivery_report.get('required_secret_names')
                    or DATABASE_DELIVERY_SECRETS)
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 0)
            if delivery_report.get('status') not in DELIVERY_SKIP_STATUSES:
                result['status'] = 'database_delivery_failed'
                result['stages']['provider_writes'] = 'blocked'
                return finalize(artifact_dir, result, 1)

        if enrolled_project_id:
            # Pin the marked enrolled project; do not re-open unmarked recovery.
            launch_args = [
                '--operation', 'deploy',
                '--bound-project-id', enrolled_project_id,
            ]
        else:
            launch_args = ['--operation', 'deploy', *recovery_args]
        shell_timeout = remaining_timeout(orch_deadline, clock, SHELL_SUBPROCESS_CAP_SEC)
        code, lines, _out, _err = run_python_script(
            'launch-app.py', source, binary, launch_args, launch_env, artifact_dir,
            timeout=shell_timeout, capture_name='launch-app-stdout.json', stage='shell_deploy')
        result['stages']['shell_deploy'] = lines[-1] if lines else {'status': 'no_output', 'returncode': code}
        shell_status = (lines[-1] or {}).get('status') if lines else None
        if shell_status == 'setup_required':
            result['status'] = 'setup_required'
            return finalize(artifact_dir, result, 0)
        if shell_status not in ALLOWED_SHELL_STATUSES:
            result['status'] = 'shell_deploy_failed'
            return finalize(artifact_dir, result, 1)
        if shell_status == 'page_and_health_verified' and code != 0:
            result['status'] = 'shell_deploy_failed'
            return finalize(artifact_dir, result, 1)
        if shell_status == 'ready_but_verification_protected' and code not in (1,):
            # Launch authenticates READY exact SHA/project/deployment then exits 1.
            result['status'] = 'shell_deploy_failed'
            return finalize(artifact_dir, result, 1)
        _status, project_id, deployment_id, _source_sha = extract_shell_binding(lines, sha)
        if enrolled_project_id and project_id != enrolled_project_id:
            result['status'] = 'deploy_project_drift'
            result['stages']['shell_binding'] = {
                'status': 'deploy_project_drift',
                'enrolled_project_id': enrolled_project_id,
                'deployed_project_id': project_id,
            }
            return finalize(artifact_dir, result, 1)
        result['stages']['shell_binding'] = {
            'status': shell_status,
            'project_id': project_id,
            'deployment_id': deployment_id,
            'source_sha': sha,
        }
        if delivery_binding_digest:
            result['stages']['shell_binding']['delivery_binding_sha256'] = (
                delivery_binding_digest)
        write_artifact(artifact_dir, 'shell-binding.json', result['stages']['shell_binding'])

        domain_env = {
            'APP_HOSTNAME': config['hostname'],
            'CLOUDFLARE_ZONE_NAME': config['cloudflare_zone_name'],
            'ONBOARDING_MERGE_POLICY': '1',
        }
        apply_args = [
            '--operation', 'apply',
            '--authorization-mode', 'merge_policy',
            '--artifact-dir', artifact_dir,
            '--approved-plan-digest', '',
            '--expected-project-id', project_id,
            '--expected-deployment-id', deployment_id,
        ]
        apply_code = 1
        apply_lines = []
        for attempt in range(DOMAIN_REPLAN_BOUND):
            repository_context(source)
            cfg_mod.load_onboarding_config(
                source, require_tracked_clean=True, expected_sha=sha)
            apply_timeout = remaining_timeout(orch_deadline, clock, DOMAIN_SUBPROCESS_CAP_SEC)
            apply_code, apply_lines, _out, _apply_err = run_python_script(
                'custom-domain.py', source, binary, apply_args, domain_env, artifact_dir,
                timeout=apply_timeout,
                capture_name=f'domain-apply-attempt-{attempt}.json',
                stage='hostname_dns_apply')
            if not apply_lines:
                break
            status = apply_lines[-1].get('status')
            if status in HARD_FAIL_DOMAIN_STATUSES:
                result['status'] = status
                result['stages']['hostname_dns_apply'] = apply_lines[-1]
                return finalize(artifact_dir, result, 1)
            if status in ('apply_complete', 'already_configured'):
                break
            if status == 'policy_extension_required':
                result['status'] = 'policy_extension_required'
                result['stages']['hostname_dns'] = apply_lines[-1]
                return finalize(artifact_dir, result, 1)
            if status in ('cname_recommendation_changed', 'requires_additional_approval'):
                continue
            break
        result['stages']['hostname_dns_apply'] = (
            apply_lines[-1] if apply_lines else {'status': 'no_output', 'returncode': apply_code})
        if apply_code != 0 and (not apply_lines or apply_lines[-1].get('status') not in (
                'already_configured', 'apply_complete')):
            if apply_lines and apply_lines[-1].get('status') == 'setup_required':
                result['status'] = 'setup_required'
                return finalize(artifact_dir, result, 0)
            result['status'] = 'hostname_dns_apply_failed'
            return finalize(artifact_dir, result, 1)

        verify_args = [
            '--operation', 'verify',
            '--authorization-mode', 'merge_policy',
            '--artifact-dir', artifact_dir,
            '--expected-project-id', project_id,
            '--expected-deployment-id', deployment_id,
        ]
        verify_deadline = clock() + verify_window_sec
        verify_code = 1
        verify_lines = []
        round_idx = 0
        while True:
            if clock() >= orch_deadline or clock() >= verify_deadline:
                if verify_lines and verify_lines[-1].get('status') in PENDING_VERIFY_STATUSES:
                    result['status'] = 'hostname_dns_verification_pending'
                    result['infrastructure_dns_ready'] = False
                    result['stages']['hostname_dns_verify'] = verify_lines[-1]
                    result['verify_window_exhausted'] = True
                    return finalize(artifact_dir, result, 1)
                break
            verify_timeout = remaining_timeout(
                min(orch_deadline, verify_deadline), clock, DOMAIN_SUBPROCESS_CAP_SEC)
            verify_code, verify_lines, _o, _verify_err = run_python_script(
                'custom-domain.py', source, binary, verify_args, domain_env, artifact_dir,
                timeout=verify_timeout,
                capture_name=f'domain-verify-attempt-{round_idx}.json',
                stage='hostname_dns_verify')
            round_idx += 1
            if verify_lines and verify_lines[-1].get('status') in HARD_FAIL_DOMAIN_STATUSES:
                result['status'] = verify_lines[-1]['status']
                result['stages']['hostname_dns_verify'] = verify_lines[-1]
                result['infrastructure_dns_ready'] = False
                return finalize(artifact_dir, result, 1)
            if verify_lines and verify_lines[-1].get('status') == 'verification_passed':
                break
            if verify_lines and verify_lines[-1].get('status') == 'ready_but_verification_protected':
                break
            pending = False
            if verify_lines:
                status = verify_lines[-1].get('status', '')
                pending = status in PENDING_VERIFY_STATUSES or bool(
                    verify_lines[-1].get('pending_checks'))
            if not pending:
                break
            # Retry only pending DNS/TLS/HTTP; preserve protected/conflict/auth failures.
            sleeper(VERIFY_POLL_SEC)
        result['stages']['hostname_dns_verify'] = (
            verify_lines[-1] if verify_lines else {'status': 'no_output', 'returncode': verify_code})

        verify_status = (verify_lines[-1] or {}).get('status') if verify_lines else None
        result['database'] = database_gap_report(preflight_report, delivery_report)
        if verify_status == 'verification_passed':
            result['status'] = 'infrastructure_dns_verified_database_incomplete'
            result['infrastructure_dns_ready'] = True
            return finalize(artifact_dir, result, 0)
        if verify_status == 'ready_but_verification_protected':
            result['status'] = 'infrastructure_dns_protected_database_incomplete'
            result['infrastructure_dns_ready'] = False
            return finalize(artifact_dir, result, 1)
        if verify_status in PENDING_VERIFY_STATUSES:
            result['status'] = 'hostname_dns_verification_pending'
            result['infrastructure_dns_ready'] = False
            return finalize(artifact_dir, result, 1)
        result['status'] = 'hostname_dns_verification_failed'
        result['infrastructure_dns_ready'] = False
        return finalize(artifact_dir, result, 1)
    except Failure as exc:
        result['status'] = str(exc)
        result['database'] = database_gap_report()
        result['full_app_ready'] = False
        return finalize(artifact_dir, result, 1)
    except Exception:
        result['status'] = 'unexpected_failure'
        result['database'] = database_gap_report()
        result['full_app_ready'] = False
        return finalize(artifact_dir, result, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--vercel-bin', required=True)
    parser.add_argument('--artifact-dir', default='.')
    args = parser.parse_args()
    return run(Path(args.source).resolve(), args.vercel_bin, args.artifact_dir)


if __name__ == '__main__':
    sys.exit(main())

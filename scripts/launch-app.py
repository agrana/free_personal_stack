#!/usr/bin/env python3
"""Secrets-only shell launcher. Provider mutations are CLI-only; discovery is GET-only.

An interrupted create without a marker fails closed. Review its emitted project ID,
then manually dispatch with expected_project_id and confirm_repository_id to adopt
that exact unmarked project. A conflicting marker can never be overridden.

Default-branch push is authorized only under a merge-reviewed onboarding.config.json
with auto_apply=true and a required hostname (ONBOARDING_MERGE_POLICY=1 from the
merge-onboarding orchestrator). Manual workflow_dispatch remains the maintainer path.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REQUIRED = ('VERCEL_API_TOKEN', 'VERCEL_TEAM_ID', 'SUPABASE_URL', 'SUPABASE_ANON_KEY')
MARKER = 'TEMPLATE_REPOSITORY_ID_'
UPSTREAM_ID = '1051601680'

class Failure(Exception):
    pass

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def emit(**fields):
    text = json.dumps(fields, sort_keys=True)
    print(text, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write('```json\n' + text + '\n```\n')

def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r'[A-Za-z0-9]+', value):
        raise Failure('identifier_invalid')
    return value

def safe_url(value):
    if not isinstance(value, str):
        raise Failure('url_invalid')
    if not value.startswith('https://'):
        value = 'https://' + value
    if not re.fullmatch(r'https://[a-z0-9][a-z0-9-]*\.vercel\.app', value):
        raise Failure('url_invalid')
    return value

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def github(path):
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + os.environ.get('GITHUB_TOKEN', ''),
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise Failure('github_response_bound')
        data = json.loads(body)
        if not isinstance(data, dict):
            raise Failure('github_shape')
        return data
    except (OSError, ValueError):
        raise Failure('github_discovery_failed') from None

def command(args, source, value=None, cli=False):
    # No credentials other than VERCEL_TOKEN reach the CLI; Git receives none.
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'HOME', 'TMPDIR', 'CI', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    if cli:
        env['VERCEL_TOKEN'] = os.environ['VERCEL_API_TOKEN']
        env['VERCEL_TELEMETRY_DISABLED'] = '1'
    try:
        result = subprocess.run(args, cwd=source, env=env, input=value, text=True,
                                capture_output=True, timeout=900 if cli else 30)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('command_transport') from None
    if result.returncode:
        raise Failure('cli_failed' if cli else 'source_check_failed')
    return result.stdout.strip()

def cli(binary, source, team, *args, value=None, structured=True):
    output = command([binary, *args, '--scope', team], source, value, True)
    if not structured:
        return None
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError:
        raise Failure('cli_json_invalid') from None

def merge_policy_context(source, sha):
    """Push mutations require merge-reviewed auto_apply policy + hostname."""
    if os.environ.get('ONBOARDING_MERGE_POLICY') != '1':
        raise Failure('event_not_authorized')
    cfg = load('onboarding-config')
    try:
        config = cfg.load_onboarding_config(
            source, require_tracked_clean=True, expected_sha=sha)
    except cfg.ConfigError as exc:
        raise Failure(str(exc)) from None
    if not config['auto_apply']:
        raise Failure('onboarding_auto_apply_disabled')
    if not config['hostname'] or not config['cloudflare_zone_name']:
        raise Failure('hostname_required_before_provisioning')
    return config


def context(source):
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    rid = os.environ.get('GITHUB_REPOSITORY_ID', '')
    sha = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Failure('repository_invalid')
    if not re.fullmatch(r'[1-9][0-9]*', rid) or rid == UPSTREAM_ID:
        raise Failure('template_or_repository_invalid')
    slug = repo.split('/')[1].lower()
    if len(slug) > 100 or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', slug):
        raise Failure('slug_invalid')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise Failure('source_sha_invalid')
    metadata = github('/repositories/' + rid)
    if (str(metadata.get('id')) != rid or metadata.get('full_name') != repo
            or metadata.get('is_template') is not False or metadata.get('fork') is not False):
        raise Failure('repository_identity_mismatch')
    branch = metadata.get('default_branch')
    if not isinstance(branch, str) or not branch:
        raise Failure('default_branch_invalid')
    event = os.environ.get('GITHUB_EVENT_NAME')
    if event == 'workflow_dispatch':
        policy = None
    elif event == 'push':
        policy = merge_policy_context(source, sha)
    else:
        raise Failure('event_not_authorized')
    if os.environ.get('GITHUB_REF') != 'refs/heads/' + branch:
        raise Failure('event_not_authorized')
    remote = github('/repos/' + repo + '/commits/' + urllib.parse.quote(branch, safe=''))
    if remote.get('sha') != sha or command(['git', 'rev-parse', 'HEAD'], source) != sha:
        raise Failure('source_sha_mismatch')
    if command(['git', 'status', '--porcelain', '--untracked-files=all'], source):
        raise Failure('source_dirty')
    return rid, slug, sha, policy

def get_page(url):
    try:
        with urllib.request.build_opener(NoRedirect()).open(url, timeout=30) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise Failure('response_bound')
            return response.status, body
    except urllib.error.HTTPError as exc:
        location = urllib.parse.urlsplit(exc.headers.get('Location', ''))
        if (exc.code in (302, 303, 307, 308) and location.scheme == 'https'
                and location.netloc == 'vercel.com' and location.path == '/sso-api'):
            return 401, b''
        return exc.code, b''
    except OSError:
        raise Failure('verification_transport') from None

def verify_page(url, sha):
    status, page = get_page(url)
    if status in (401, 403):
        return False
    if status != 200 or not re.search(br'<(?:html|!doctype html)\b', page, re.I):
        raise Failure('homepage_invalid')
    status, body = get_page(url + '/api/health')
    if status in (401, 403):
        return False
    try:
        health = json.loads(body)
    except ValueError:
        raise Failure('health_json_invalid') from None
    if status != 200 or health != {'status': 'ready', 'service': 'app', 'revision': sha}:
        raise Failure('health_revision_invalid')
    return True

def run(source, binary, operation='deploy', expected_project=None, confirm_repository=None):
    if any(not os.environ.get(k, '').strip() or os.environ[k].strip() ==
           'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE' for k in REQUIRED):
        emit(status='setup_required', required_secret_names=list(REQUIRED))
        return 0
    rid, name, sha, policy = context(source)
    marker_key = MARKER + rid
    public_config = {key: os.environ[key] for key in ('SUPABASE_URL', 'SUPABASE_ANON_KEY')}
    team = identifier(os.environ['VERCEL_TEAM_ID'], 'team_')
    token = os.environ['VERCEL_API_TOKEN']
    vercel = load('check-vercel-access')
    try:
        load('check-supabase-access').check(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
        # Explicit team: never choose an account or team from an ambiguous inventory.
        teams = list(vercel.items('/v2/teams', 'teams', token))
        if len([t for t in teams if t.get('id') == team]) != 1:
            raise Failure('team_unavailable')
        projects = list(vercel.items('/v9/projects', 'projects', token, {'teamId': team}))
    except Failure:
        raise
    except Exception:
        raise Failure('preflight_failed') from None
    config_sha = hashlib.sha256(json.dumps(public_config, sort_keys=True,
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    def read(path):
        try:
            return vercel.get_json(path + '?' + urllib.parse.urlencode({'teamId': team}), token)
        except Exception:
            raise Failure('provider_readback_failed') from None
    def owned(pid):
        project = read('/v9/projects/' + pid)
        if project.get('id') != pid or project.get('name') != name or project.get('accountId') != team:
            raise Failure('project_scope_mismatch')
        return project
    def marker(pid):
        envs = read('/v9/projects/' + pid + '/env').get('envs')
        if not isinstance(envs, list):
            raise Failure('marker_readback_invalid')
        # Ownership is in the key, never the opaque encrypted API value.
        candidates = [e for e in envs if isinstance(e, dict) and
                      str(e.get('key', '')).startswith(MARKER.rstrip('_'))]
        if any(e.get('key') != marker_key for e in candidates):
            raise Failure('repository_marker_conflict')
        matches = [e for e in candidates if 'production' in e.get('target', [])]
        if not matches:
            return False
        if len(matches) != 1 or matches[0].get('type') != 'encrypted' or matches[0].get('visibility') != 'config':
            raise Failure('repository_marker_conflict')
        return True
    matches = [p for p in projects if p.get('name') == name]
    if len(matches) > 1:
        raise Failure('project_collision')
    manual = os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
    merge_auto = policy is not None and policy.get('auto_apply') is True
    if expected_project is None and merge_auto and policy.get('expected_project_id'):
        expected_project = policy['expected_project_id']
        confirm_repository = policy['confirm_repository_id']
    if expected_project:
        identifier(expected_project, 'prj_')
        # Recovery: manual dispatch, or merge-policy push with exact config pair.
        if operation != 'deploy' or confirm_repository != rid:
            raise Failure('recovery_not_authorized')
        if not manual and not merge_auto:
            raise Failure('recovery_not_authorized')
        if len(matches) != 1 or matches[0].get('id') != expected_project:
            raise Failure('expected_project_mismatch')
    plan = {'repository_id': rid, 'team_id': team, 'project_name': name, 'source_sha': sha,
            'public_configuration_sha256': config_sha,
            'allowed_mutations': ([] if operation == 'verify' else
                ['public_configuration', 'production_deployment'] + ([] if matches else ['create_project']))}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    emit(status='plan', digest=digest, **plan)
    if not matches:
        # First create: manual dispatch, or merge-policy push with required hostname.
        if operation != 'deploy':
            raise Failure('manual_creation_required')
        if not manual and not merge_auto:
            raise Failure('manual_creation_required')
        if merge_auto and not (policy and policy.get('hostname')):
            raise Failure('hostname_required_before_provisioning')
        # Revalidate source and GitHub identity before the first write.
        context(source)
        # project create swallows HTTP 409 in CLI 65.4; api preserves failure.
        # Official create environmentVariables schema lacks Config visibility;
        # do not fabricate it. Interrupted unmarked creation needs exact-ID recovery.
        created = cli(binary, source, team, 'api', '/v10/projects', '-X', 'POST',
                      '--input', '-', value=json.dumps({'name': name,
                      'framework': 'nextjs', 'nodeVersion': '24.x'}))
        pid = identifier(created.get('id'), 'prj_')
        emit(status='project_created_recovery_handle', project_id=pid)
        inspected = cli(binary, source, team, 'project', 'inspect', pid, '--json')
        if inspected.get('id') != pid or inspected.get('name') != name:
            raise Failure('project_scope_mismatch')
        project = owned(pid)
        if marker(pid):
            raise Failure('unexpected_existing_marker')
        needs_marker = True
    else:
        pid = identifier(matches[0].get('id'), 'prj_')
        emit(status='project_readback', project_id=pid)
        project = owned(pid)
        needs_marker = not marker(pid)
        if needs_marker and not expected_project:
            raise Failure('unmarked_project_requires_explicit_recovery')
    def add(key, value, force=False):
        if key not in (marker_key, 'NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY') or (key == marker_key and force):
            raise Failure('environment_key_forbidden')
        owned(pid)
        if key != marker_key and not marker(pid):
            raise Failure('repository_marker_missing')
        cli(binary, source, team, 'env', 'add', key, '--project', pid, '--environment',
            'production', '--type', 'config', '--yes', *(['--force'] if force else []),
            value=value, structured=False)
    if needs_marker:
        if operation == 'verify':
            raise Failure('repository_marker_missing')
        add(marker_key, rid)
        if not marker(pid):
            raise Failure('repository_marker_missing')
    target = project.get('targets', {}).get('production', {}).get('id')
    remote = read('/v13/deployments/' + identifier(target, 'dpl_')) if target else None
    same = (remote and remote.get('readyState') == 'READY' and remote.get('target') == 'production'
            and remote.get('projectId') == pid and remote.get('meta', {}).get('githubCommitSha') == sha
            and str(remote.get('meta', {}).get('githubRepoId')) == rid
            and remote.get('meta', {}).get('templatePublicConfigSha256') == config_sha)
    projected = {}
    if not same:
        if operation == 'verify':
            raise Failure('deployment_revision_not_ready')
        context(source)
        for key in ('SUPABASE_URL', 'SUPABASE_ANON_KEY'):
            add('NEXT_PUBLIC_' + key, public_config[key], True)
        owned(pid)
        if not marker(pid):
            raise Failure('repository_marker_missing')
        context(source)
        projected = cli(binary, source, team, 'deploy', str(source), '--project', pid, '--prod',
            '--yes', '--json', '--meta', 'githubCommitSha=' + sha, '--meta', 'githubRepoId=' + rid,
            '--meta', 'templatePublicConfigSha256=' + config_sha,
            '--env', 'VERCEL_GIT_COMMIT_SHA=' + sha, '--build-env', 'VERCEL_GIT_COMMIT_SHA=' + sha)
        projected = projected.get('deployment', projected)
        target = identifier(projected.get('id'), 'dpl_')
        remote = read('/v13/deployments/' + target)
        if (remote.get('id') != target or safe_url(remote.get('url')) != safe_url(projected.get('url'))):
            raise Failure('deployment_readback_mismatch')
    if (remote.get('id') != target or remote.get('readyState') != 'READY' or
            remote.get('target') != 'production' or remote.get('projectId') != pid or
            remote.get('meta', {}).get('githubCommitSha') != sha or
            str(remote.get('meta', {}).get('githubRepoId')) != rid or
            remote.get('meta', {}).get('templatePublicConfigSha256') != config_sha):
        raise Failure('deployment_readback_mismatch')
    urls = [safe_url(remote.get('url'))]
    aliases = remote.get('alias', [])
    for alias in aliases:
        try:
            candidate = safe_url(alias.get('alias') if isinstance(alias, dict) else alias)
        except Failure:
            continue
        if candidate not in urls:
            urls.append(candidate)
    # productionUrl is only trusted when authenticated remote alias readback binds it.
    for url in urls:
        if verify_page(url, sha):
            emit(status='page_and_health_verified', project_id=pid, deployment_id=target, url=url, source_sha=sha)
            return 0
    emit(status='ready_but_verification_protected', project_id=pid, deployment_id=target, source_sha=sha)
    return 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--vercel-bin', required=True)
    parser.add_argument('--operation', choices=('deploy', 'verify'), default='deploy')
    parser.add_argument('--expected-project-id')
    parser.add_argument('--confirm-repository-id')
    args = parser.parse_args()
    try:
        return run(Path(args.source).resolve(), args.vercel_bin, args.operation,
                   args.expected_project_id or None, args.confirm_repository_id or None)
    except Failure as exc:
        emit(status=str(exc))
    except Exception:
        emit(status='unexpected_failure')
    return 1

if __name__ == '__main__':
    sys.exit(main())

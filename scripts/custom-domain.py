#!/usr/bin/env python3
"""Optional custom-domain attach after secrets-only shell launch.

Plan is read-only. Apply requires an explicit approved plan digest and
recomputes the live plan before the first mutation. Verify checks DNS/TLS/HTTP
against the owned production deployment SHA (not the workflow automation SHA).

Does not deploy source, touch Auth/database, alter zone/nameserver/email
settings, force-move domains, or invent TXT challenges. A post-attach TXT
challenge becomes a staged plan whose digest is produced by the same
canonical build_plan() body used for subsequent apply.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UPSTREAM_ID = '1051601680'
MARKER = 'TEMPLATE_REPOSITORY_ID_'
REQUIRED_SECRETS = ('VERCEL_API_TOKEN', 'VERCEL_TEAM_ID', 'CLOUDFLARE_API_TOKEN')
REQUIRED_VARS = ('APP_HOSTNAME', 'CLOUDFLARE_ZONE_NAME')
PLACEHOLDER = 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
CNAME_TTL = 300
RESERVED_LABELS = frozenset({'www', 'mail'})
HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
# TXT ownership challenges may use underscore labels (e.g. _vercel.app.example.com).
DNS_NAME_RE = re.compile(
    r'^(?=.{1,253}$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z]{2,63}$')
DNS_LABEL_RE = re.compile(r'^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$')
CF_API = 'https://api.cloudflare.com/client/v4'
PUBLIC_RESOLVERS = ('1.1.1.1', '8.8.8.8')
VERIFY_ATTEMPTS = 3
VERIFY_SLEEP_SEC = 2
PRODUCTION_NULL_FIELDS = ('gitBranch', 'customEnvironmentId', 'redirect', 'redirectStatusCode')
BLOCKED_CNAME_LABELS = frozenset({
    'localhost', 'metadata', 'internal',
})
# Authenticated Vercel recommendedCNAME is the trust boundary after syntactic checks.
# Reject IP literals / local-only hosts; do not fetch arbitrary URLs.


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


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r'[A-Za-z0-9]+', value):
        raise Failure('identifier_invalid')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def missing_setup():
    secrets = [k for k in REQUIRED_SECRETS
               if not os.environ.get(k, '').strip() or os.environ[k].strip() == PLACEHOLDER]
    variables = [k for k in REQUIRED_VARS if not os.environ.get(k, '').strip()]
    if not secrets and not variables:
        return None
    return {
        'status': 'setup_required',
        'required_secret_names': secrets or list(REQUIRED_SECRETS),
        'required_variable_names': variables or list(REQUIRED_VARS),
        'checklist': [
            'Set repository Actions secrets: VERCEL_API_TOKEN, VERCEL_TEAM_ID, CLOUDFLARE_API_TOKEN',
            'Set repository Actions variables: APP_HOSTNAME, CLOUDFLARE_ZONE_NAME',
            'Complete secrets-only Launch app so the project has TEMPLATE_REPOSITORY_ID_<rid>',
            'Cloudflare token: Zone Read + DNS Read for plan; DNS Write for apply (zone-restricted)',
            'Vercel token: explicit team/project scope for the owned app project',
        ],
    }


def github(path):
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + os.environ.get('GITHUB_TOKEN', ''),
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
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
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'HOME', 'TMPDIR', 'CI', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    if cli:
        env['VERCEL_TOKEN'] = os.environ['VERCEL_API_TOKEN']
        env['VERCEL_TELEMETRY_DISABLED'] = '1'
    try:
        result = subprocess.run(
            args, cwd=source, env=env, input=value, text=True,
            capture_output=True, timeout=120 if cli else 30)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('command_transport') from None
    if result.returncode:
        raise Failure('cli_failed' if cli else 'source_check_failed')
    return result.stdout.strip()


def vercel_cli(binary, source, team, *args, value=None):
    output = command([binary, *args, '--scope', team], source, value, True)
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError:
        raise Failure('cli_json_invalid') from None


def normalize_hostname(value):
    if not isinstance(value, str):
        raise Failure('hostname_invalid')
    host = value.strip().lower().rstrip('.')
    if '://' in host or '/' in host or '?' in host or '#' in host or '@' in host:
        raise Failure('hostname_invalid')
    if ':' in host or '*' in host or ' ' in host or host.startswith('.'):
        raise Failure('hostname_invalid')
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', host):
        raise Failure('hostname_invalid')
    if not HOSTNAME_RE.fullmatch(host):
        raise Failure('hostname_invalid')
    labels = host.split('.')
    if any(not LABEL_RE.fullmatch(label) for label in labels):
        raise Failure('hostname_invalid')
    return host


def validate_hostname_under_zone(hostname, zone):
    host = normalize_hostname(hostname)
    zone_name = normalize_hostname(zone)
    if host == zone_name:
        raise Failure('apex_hostname_forbidden')
    suffix = '.' + zone_name
    if not host.endswith(suffix):
        raise Failure('hostname_outside_zone')
    relative = host[: -len(suffix)]
    if not relative or '.' in relative:
        # Require exactly one label under the authorized zone (app subdomain only).
        raise Failure('hostname_not_single_subdomain')
    if relative in RESERVED_LABELS:
        raise Failure('reserved_hostname_forbidden')
    return host, zone_name


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
    if (os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + branch):
        raise Failure('event_not_authorized')
    remote = github('/repos/' + repo + '/commits/' + urllib.parse.quote(branch, safe=''))
    if remote.get('sha') != sha or command(['git', 'rev-parse', 'HEAD'], source) != sha:
        raise Failure('automation_sha_mismatch')
    if command(['git', 'status', '--porcelain', '--untracked-files=all'], source):
        raise Failure('source_dirty')
    return rid, slug, sha, repo


def cf_request(method, path, token, payload=None, query=None):
    if not path.startswith('/') or path.startswith('//'):
        raise Failure('cloudflare_route_invalid')
    url = CF_API + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    data = None
    headers = {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/json',
    }
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise Failure('cloudflare_response_bound')
        parsed = json.loads(body)
        if not isinstance(parsed, dict) or parsed.get('success') is not True:
            raise Failure('cloudflare_api_failed')
        return parsed
    except urllib.error.HTTPError as exc:
        raise Failure('cloudflare_http_' + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise Failure('cloudflare_transport') from None


def cf_paginate(path, token, query=None, key='result'):
    query = dict(query or {})
    query.setdefault('per_page', 50)
    page = 1
    seen = set()
    while page <= 20:
        query['page'] = page
        data = cf_request('GET', path, token, query=query)
        items = data.get(key)
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            raise Failure('cloudflare_inventory_invalid')
        yield from items
        info = data.get('result_info') or {}
        if not isinstance(info, dict):
            raise Failure('cloudflare_pagination_invalid')
        total_pages = info.get('total_pages')
        if total_pages is None:
            if len(items) < int(query['per_page']):
                return
            page += 1
            continue
        if not isinstance(total_pages, int) or total_pages < 1:
            raise Failure('cloudflare_pagination_invalid')
        marker = str(page) + ':' + str(total_pages)
        if marker in seen:
            raise Failure('cloudflare_pagination_invalid')
        seen.add(marker)
        if page >= total_pages:
            return
        page += 1
    raise Failure('cloudflare_inventory_bound')


def resolve_zone(token, zone_name):
    matches = []
    for zone in cf_paginate('/zones', token, {'name': zone_name, 'status': 'active'}):
        if zone.get('name') == zone_name and zone.get('status') == 'active':
            matches.append(zone)
    if not matches:
        # Fail closed when name filter is ignored or zone absent/inactive.
        all_named = [z for z in cf_paginate('/zones', token, {'name': zone_name})
                     if z.get('name') == zone_name]
        if all_named:
            raise Failure('zone_not_active')
        raise Failure('zone_not_found')
    if len(matches) != 1:
        raise Failure('zone_ambiguous')
    zone = matches[0]
    zone_id = zone.get('id')
    account = zone.get('account') if isinstance(zone.get('account'), dict) else {}
    account_id = account.get('id')
    if not isinstance(zone_id, str) or not zone_id:
        raise Failure('zone_id_invalid')
    if not isinstance(account_id, str) or not account_id:
        raise Failure('zone_account_invalid')
    raw_ns = zone.get('name_servers')
    if not isinstance(raw_ns, list) or not raw_ns:
        raise Failure('zone_nameservers_missing')
    nameservers = []
    for item in raw_ns:
        if not isinstance(item, str):
            raise Failure('zone_nameservers_invalid')
        nameservers.append(normalize_hostname(item))
    nameservers = sorted(set(nameservers))
    return zone_id, account_id, nameservers


def dns_records_for_name(token, zone_id, name):
    records = list(cf_paginate(
        '/zones/' + zone_id + '/dns_records', token, {'name': name}))
    # Exact name only; Cloudflare may return relative matches — enforce equality.
    exact = []
    for record in records:
        record_name = str(record.get('name', '')).rstrip('.').lower()
        if record_name == name:
            exact.append(record)
    return exact


def zone_dns_inventory(token, zone_id):
    # Bounded full-zone read for preservation hashing only; never emit raw unrelated values.
    return list(cf_paginate('/zones/' + zone_id + '/dns_records', token))


def relevant_record_view(record):
    return {
        'id': record.get('id'),
        'type': record.get('type'),
        'name': str(record.get('name', '')).rstrip('.').lower(),
        'content': record.get('content'),
        'proxied': record.get('proxied'),
        'ttl': record.get('ttl'),
    }


def sort_record_views(views):
    return sorted(
        views,
        key=lambda r: (
            str(r.get('type') or ''),
            str(r.get('name') or ''),
            str(r.get('content') or ''),
            str(r.get('id') or ''),
        ),
    )


def parse_rank(rank):
    # bool is a subclass of int; reject it explicitly. Reject non-finite / fractional ranks.
    if isinstance(rank, bool) or not isinstance(rank, (int, float)):
        raise Failure('cname_recommendation_malformed')
    if isinstance(rank, float):
        if not math.isfinite(rank) or not rank.is_integer():
            raise Failure('cname_recommendation_malformed')
        rank = int(rank)
    if not isinstance(rank, int) or rank < 1:
        raise Failure('cname_recommendation_malformed')
    return rank


def validate_recommended_cname_host(host):
    """Syntactic trust boundary on authenticated provider recommendation (no URL fetch)."""
    if not isinstance(host, str):
        raise Failure('cname_recommendation_untrusted')
    value = host.strip().lower().rstrip('.')
    if not value or '://' in value or '/' in value or '@' in value or ':' in value:
        raise Failure('cname_recommendation_untrusted')
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', value):
        raise Failure('cname_recommendation_untrusted')
    if ':' in value:  # IPv6 literal guard (also caught above by hostname rules)
        raise Failure('cname_recommendation_untrusted')
    if not HOSTNAME_RE.fullmatch(value):
        raise Failure('cname_recommendation_untrusted')
    labels = value.split('.')
    if any(not LABEL_RE.fullmatch(label) for label in labels):
        raise Failure('cname_recommendation_untrusted')
    if labels[0] in BLOCKED_CNAME_LABELS or value in BLOCKED_CNAME_LABELS:
        raise Failure('cname_recommendation_untrusted')
    # Allow provider hosts under public suffixes; block only unambiguous local/test suffixes.
    # Note: '*.example' TLD-style test hosts are rejected via BLOCKED_CNAME_SUFFIXES carefully —
    # synthetic tests use vercel-dns.example.com which ends with .example.com not .example.
    for suffix in ('.localhost', '.local', '.internal', '.invalid', '.test', '.localdomain'):
        if value == suffix[1:] or value.endswith(suffix):
            raise Failure('cname_recommendation_untrusted')
    return value


def pick_recommended_cname(config):
    recommendations = config.get('recommendedCNAME')
    if not isinstance(recommendations, list) or not recommendations:
        raise Failure('cname_recommendation_missing')
    ranked = []
    for item in recommendations:
        if not isinstance(item, dict):
            raise Failure('cname_recommendation_malformed')
        rank = parse_rank(item.get('rank'))
        host = validate_recommended_cname_host(item.get('value'))
        ranked.append((rank, host))
    preferred = [value for rank, value in ranked if rank == 1]
    if len(preferred) != 1:
        raise Failure('cname_recommendation_rank_invalid')
    return preferred[0]


def project_marker(envs, marker_key):
    if not isinstance(envs, list):
        raise Failure('marker_readback_invalid')
    candidates = [e for e in envs if isinstance(e, dict) and
                  str(e.get('key', '')).startswith(MARKER.rstrip('_'))]
    if any(e.get('key') != marker_key for e in candidates):
        raise Failure('repository_marker_conflict')
    matches = [e for e in candidates if 'production' in (e.get('target') or [])]
    if not matches:
        return False
    if len(matches) != 1 or matches[0].get('type') != 'encrypted' or matches[0].get('visibility') != 'config':
        raise Failure('repository_marker_conflict')
    return True


def vercel_items(vercel, route, key, token, query=None):
    """Strict paginated GET for team projects/domains (OpenAPI requires pagination).

    Does not treat a missing pagination object as a complete empty inventory.
    Uses a custom-domain-local adapter so legacy check-vercel-access fixtures stay intact.
    """
    query = dict(query or {})
    query['limit'] = 100
    seen = set()
    collected = []
    try:
        for _ in range(20):
            data = vercel.get_json(route + '?' + urllib.parse.urlencode(query), token)
            if not isinstance(data, dict):
                raise Failure('vercel_inventory_incomplete')
            page = data.get(key)
            if not isinstance(page, list) or not all(isinstance(x, dict) for x in page):
                raise Failure('vercel_inventory_incomplete')
            pagination = data.get('pagination')
            if not isinstance(pagination, dict):
                raise Failure('vercel_inventory_incomplete')
            # OpenAPI Pagination requires count/next/prev; next may be null at end.
            if not all(field in pagination for field in ('count', 'next', 'prev')):
                raise Failure('vercel_inventory_incomplete')
            if not isinstance(pagination['count'], (int, float)) or isinstance(pagination['count'], bool):
                raise Failure('vercel_inventory_incomplete')
            collected.extend(page)
            cursor = pagination.get('next')
            if cursor is None:
                return collected
            if not isinstance(cursor, (str, int)) or isinstance(cursor, bool):
                raise Failure('vercel_inventory_incomplete')
            cursor_key = str(cursor)
            if cursor_key in seen:
                raise Failure('vercel_inventory_incomplete')
            seen.add(cursor_key)
            query['until'] = cursor
        raise Failure('vercel_inventory_incomplete')
    except Failure:
        raise
    except Exception as exc:
        message = str(exc)
        if any(s in message for s in (
                'Invalid inventory page', 'Inventory exceeds page bound',
                'Invalid pagination', 'Invalid inventory cursor',
                'Invalid API', 'Vercel GET failed', 'API response exceeds')):
            raise Failure('vercel_inventory_incomplete') from None
        raise Failure('vercel_inventory_failed') from None


def get_project_domains(vercel, token, team, project_id):
    return vercel_items(
        vercel, '/v9/projects/' + project_id + '/domains', 'domains', token,
        {'teamId': team, 'limit': 100})


def domain_elsewhere(vercel, token, team, hostname, project_id):
    """Team-scoped collision inventory only.

    GET /v1/domains/{domain}/project-domains requires an apex owned by the
    authenticated Vercel account and is not a global hostname lookup. It is not
    used here: Cloudflare-hosted apexes are commonly not Vercel-owned, and
    missing projectDomains must never mean success. Evidence is selected-team
    projects+domains only; other accounts remain opaque. addProjectDomain still
    fail-closes on provider collision (HTTP 409).
    """
    projects = vercel_items(vercel, '/v9/projects', 'projects', token, {'teamId': team})
    conflicts = []
    for project in projects:
        pid = project.get('id')
        if not isinstance(pid, str):
            raise Failure('project_inventory_invalid')
        for domain in get_project_domains(vercel, token, team, pid):
            if domain.get('name') == hostname and pid != project_id:
                conflicts.append({'project_id': pid, 'name': domain.get('name')})
    if conflicts:
        raise Failure('domain_bound_to_other_project')


def production_deployment(vercel, token, team, project, rid):
    targets = project.get('targets') if isinstance(project.get('targets'), dict) else {}
    production = targets.get('production') if isinstance(targets.get('production'), dict) else {}
    target = production.get('id')
    if not isinstance(target, str) or not target:
        raise Failure('production_deployment_missing')
    identifier(target, 'dpl_')
    remote = vercel.get_json(
        '/v13/deployments/' + target + '?' + urllib.parse.urlencode({'teamId': team}),
        token)
    meta = remote.get('meta') if isinstance(remote.get('meta'), dict) else {}
    deploy_sha = meta.get('githubCommitSha')
    if (remote.get('id') != target or remote.get('readyState') != 'READY'
            or remote.get('target') != 'production'
            or remote.get('projectId') != project.get('id')
            or not isinstance(deploy_sha, str)
            or not re.fullmatch(r'[0-9a-f]{40}', deploy_sha)
            or str(meta.get('githubRepoId')) != rid):
        # Legacy deployments without githubRepoId require Launch enrollment/redeploy.
        raise Failure('production_deployment_invalid')
    return target, deploy_sha, remote


def confirm_commit_in_repo(repo, sha):
    commit = github('/repos/' + repo + '/commits/' + sha)
    if not isinstance(commit.get('sha'), str) or commit.get('sha') != sha:
        raise Failure('deployment_sha_not_in_repository')


def normalize_dns_name(value):
    if not isinstance(value, str):
        raise Failure('dns_name_invalid')
    name = value.strip().lower().rstrip('.')
    if '://' in name or '/' in name or '?' in name or '#' in name or '@' in name:
        raise Failure('dns_name_invalid')
    if ':' in name or '*' in name or ' ' in name or name.startswith('.'):
        raise Failure('dns_name_invalid')
    if not DNS_NAME_RE.fullmatch(name):
        raise Failure('dns_name_invalid')
    if any(not DNS_LABEL_RE.fullmatch(label) for label in name.split('.')):
        raise Failure('dns_name_invalid')
    return name


def challenge_in_scope(challenge, hostname, zone_name):
    if not isinstance(challenge, dict):
        return False
    if challenge.get('type') != 'TXT':
        return False
    domain = challenge.get('domain')
    value = challenge.get('value')
    if not isinstance(domain, str) or not isinstance(value, str) or not value.strip():
        return False
    try:
        name = normalize_dns_name(domain)
    except Failure:
        return False
    if name == zone_name or name == 'www.' + zone_name or name == 'mail.' + zone_name:
        return False
    if name == hostname or name.endswith('.' + hostname):
        return True
    return False


def challenge_views(challenges):
    views = []
    if not isinstance(challenges, list):
        return views
    for challenge in challenges:
        if not isinstance(challenge, dict):
            continue
        domain = challenge.get('domain')
        try:
            name = normalize_dns_name(domain) if isinstance(domain, str) else None
        except Failure:
            name = str(domain).lower() if domain is not None else ''
        views.append({
            'domain': name,
            'type': challenge.get('type'),
            'value': challenge.get('value'),
            'reason': challenge.get('reason'),
        })
    return sorted(
        views,
        key=lambda c: (
            str(c.get('domain') or ''),
            str(c.get('type') or ''),
            str(c.get('value') or ''),
            str(c.get('reason') or ''),
        ),
    )


def domain_routing_view(project_domain):
    if not project_domain:
        return None
    return {
        'name': project_domain.get('name'),
        'projectId': project_domain.get('projectId'),
        'verified': project_domain.get('verified'),
        'gitBranch': project_domain.get('gitBranch'),
        'customEnvironmentId': project_domain.get('customEnvironmentId'),
        'redirect': project_domain.get('redirect'),
        'redirectStatusCode': project_domain.get('redirectStatusCode'),
        'verification': challenge_views(project_domain.get('verification') or []),
    }


def assert_production_domain_routing(project_domain):
    if project_domain is None:
        return
    for key in ('name', 'projectId', 'verified'):
        if key not in project_domain:
            raise Failure('project_domain_incomplete')
    for key in PRODUCTION_NULL_FIELDS:
        if project_domain.get(key) is not None:
            raise Failure('project_domain_not_production_routing')


def digest_for(plan_body):
    return hashlib.sha256(
        json.dumps(plan_body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    ).hexdigest()


def preservation_hash(zone_records, project_domain):
    baseline = {
        'zone_dns': sort_record_views([relevant_record_view(r) for r in zone_records]),
        'project_domain': domain_routing_view(project_domain),
    }
    return hashlib.sha256(
        json.dumps(baseline, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest(), baseline


def PLAN_BODY_KEYS():
    return (
        'repository_id', 'team_id', 'project_id', 'project_name', 'hostname',
        'zone_name', 'zone_id', 'zone_account_id', 'zone_name_servers',
        'automation_source_sha', 'app_deployment_id', 'app_deployment_sha',
        'cname_target', 'cname_ttl', 'existing_project_domain',
        'project_domain_verified', 'project_domain_routing',
        'dns_record_count_for_hostname', 'preservation_baseline_sha256',
        'already_satisfied', 'proposed_mutations', 'allowed_mutations',
        'aliases_retained', 'deployment_protection_retained',
        'conflict_preflight_scope',
    )


def plan_body_from_public(plan):
    return {k: plan[k] for k in PLAN_BODY_KEYS() if k in plan}


def build_plan(source, binary, artifact_dir):
    setup = missing_setup()
    if setup:
        emit(**setup)
        write_artifact(artifact_dir, 'custom-domain-setup-required.json', setup)
        return 0, setup

    rid, slug, automation_sha, repo = context(source)
    hostname, zone_name = validate_hostname_under_zone(
        os.environ['APP_HOSTNAME'], os.environ['CLOUDFLARE_ZONE_NAME'])
    team = identifier(os.environ['VERCEL_TEAM_ID'], 'team_')
    token = os.environ['VERCEL_API_TOKEN']
    cf_token = os.environ['CLOUDFLARE_API_TOKEN']
    vercel = load('check-vercel-access')

    teams = vercel_items(vercel, '/v2/teams', 'teams', token)
    if len([t for t in teams if t.get('id') == team]) != 1:
        raise Failure('team_unavailable')
    projects = vercel_items(vercel, '/v9/projects', 'projects', token, {'teamId': team})
    matches = [p for p in projects if p.get('name') == slug]
    if len(matches) > 1:
        raise Failure('project_collision')
    if not matches:
        raise Failure('project_missing')

    candidate = matches[0]
    pid = identifier(candidate.get('id'), 'prj_')
    project = vercel.get_json(
        '/v9/projects/' + pid + '?' + urllib.parse.urlencode({'teamId': team}), token)
    if project.get('id') != pid or project.get('name') != slug or project.get('accountId') != team:
        raise Failure('project_scope_mismatch')
    envs = vercel.get_json(
        '/v9/projects/' + pid + '/env?' + urllib.parse.urlencode({'teamId': team}),
        token).get('envs')
    marker_key = MARKER + rid
    marked = project_marker(envs, marker_key)
    if not marked:
        blocker = {
            'status': 'plan',
            'ownership': 'missing_marker',
            'repository_id': rid,
            'team_id': team,
            'project_name': slug,
            'discovered_candidate_project_id': pid,
            'automation_source_sha': automation_sha,
            'hostname': hostname,
            'zone_name': zone_name,
            'proposed_mutations': [],
            'allowed_mutations': [],
            'recovery': {
                'action': 'launch_app_marker_enrollment',
                'workflow': 'Launch app',
                'operation': 'deploy',
                'expected_project_id': 'set to the independently reviewed project id',
                'confirm_repository_id': rid,
                'note': ('Do not adopt by name. Review the candidate in Vercel, then run '
                         'Launch app recovery with exact expected_project_id + confirm_repository_id.'),
            },
            'blocker': 'unmarked_project_requires_explicit_recovery',
        }
        digest = digest_for({k: blocker[k] for k in (
            'repository_id', 'team_id', 'project_name', 'hostname', 'zone_name',
            'automation_source_sha', 'ownership', 'proposed_mutations')})
        blocker['digest'] = digest
        emit(**blocker)
        write_artifact(artifact_dir, 'custom-domain-plan.json', blocker)
        return 0, blocker

    deployment_id, app_sha, remote = production_deployment(vercel, token, team, project, rid)
    confirm_commit_in_repo(repo, app_sha)
    zone_id, account_id, zone_ns = resolve_zone(cf_token, zone_name)
    records = dns_records_for_name(cf_token, zone_id, hostname)
    domain_elsewhere(vercel, token, team, hostname, pid)

    existing_domains = get_project_domains(vercel, token, team, pid)
    project_domain = next((d for d in existing_domains if d.get('name') == hostname), None)
    if project_domain is not None:
        if project_domain.get('projectId') != pid:
            raise Failure('project_domain_scope_mismatch')
        assert_production_domain_routing(project_domain)

    config = vercel.get_json(
        '/v6/domains/' + urllib.parse.quote(hostname, safe='')
        + '/config?' + urllib.parse.urlencode({'teamId': team}),
        token)
    cname_target = pick_recommended_cname(config)

    mutations = []
    conflicts = []
    already = []
    if project_domain is None:
        mutations.append({'action': 'vercel_add_project_domain', 'name': hostname, 'project_id': pid})
    elif project_domain.get('verified') is True:
        already.append('vercel_domain_verified')
    else:
        challenges = project_domain.get('verification') or []
        if not isinstance(challenges, list):
            raise Failure('verification_challenge_invalid')
        txt = [c for c in challenges if challenge_in_scope(c, hostname, zone_name)]
        out_of_scope = [c for c in challenges if isinstance(c, dict) and c.get('type') == 'TXT'
                        and c not in txt]
        if out_of_scope:
            raise Failure('verification_challenge_out_of_scope')
        # Canonical challenge order so API array shuffle does not invalidate digests.
        txt = sorted(
            txt,
            key=lambda c: (
                normalize_dns_name(c['domain']),
                str(c.get('value') or ''),
                str(c.get('reason') or ''),
            ),
        )
        need_verify = False
        if txt:
            for challenge in txt:
                challenge_name = normalize_dns_name(challenge['domain'])
                existing_txt = dns_records_for_name(cf_token, zone_id, challenge_name)
                matching = [r for r in existing_txt
                            if r.get('type') == 'TXT' and r.get('content') in (
                                challenge['value'], '"' + challenge['value'] + '"')]
                approved_values = {
                    c['value'] for c in txt
                    if normalize_dns_name(c['domain']) == challenge_name
                }
                conflicting = [
                    r for r in existing_txt
                    if r.get('type') != 'TXT' or not any(
                        txt_content_matches(r.get('content'), value)
                        for value in approved_values)
                ]
                if conflicting:
                    conflicts.append({'kind': 'txt_conflict', 'name': challenge_name})
                elif matching:
                    already.append('txt_challenge_present')
                    need_verify = True
                else:
                    mutations.append({
                        'action': 'cloudflare_create_txt',
                        'name': challenge_name,
                        'value': challenge['value'],
                        'zone_id': zone_id,
                        'reason': challenge.get('reason'),
                    })
                    need_verify = True
        else:
            need_verify = True
        if need_verify:
            mutations.append({
                'action': 'vercel_verify_project_domain',
                'name': hostname,
                'project_id': pid,
            })

    matching_cname = [r for r in records
                      if r.get('type') == 'CNAME'
                      and str(r.get('content', '')).rstrip('.').lower() == cname_target
                      and r.get('proxied') is False]
    other = [r for r in records if r not in matching_cname]
    if other:
        conflicts.append({
            'kind': 'dns_conflict',
            'types': sorted({str(r.get('type')) for r in other}),
            'count': len(other),
        })
    elif matching_cname:
        already.append('cname_present')
    else:
        mutations.append({
            'action': 'cloudflare_create_cname',
            'name': hostname,
            'content': cname_target,
            'ttl': CNAME_TTL,
            'proxied': False,
            'zone_id': zone_id,
        })

    if conflicts:
        raise Failure('plan_conflict_existing_records')

    # Deduplicate verify mutations while preserving order of first occurrence.
    deduped = []
    seen_mut = set()
    for mutation in mutations:
        key = json.dumps(mutation, sort_keys=True, separators=(',', ':'))
        if key in seen_mut:
            continue
        seen_mut.add(key)
        deduped.append(mutation)
    mutations = deduped

    zone_records = zone_dns_inventory(cf_token, zone_id)
    baseline_hash, _baseline = preservation_hash(zone_records, project_domain)
    plan_body = {
        'repository_id': rid,
        'team_id': team,
        'project_id': pid,
        'project_name': slug,
        'hostname': hostname,
        'zone_name': zone_name,
        'zone_id': zone_id,
        'zone_account_id': account_id,
        'zone_name_servers': zone_ns,
        'automation_source_sha': automation_sha,
        'app_deployment_id': deployment_id,
        'app_deployment_sha': app_sha,
        'cname_target': cname_target,
        'cname_ttl': CNAME_TTL,
        'existing_project_domain': bool(project_domain),
        'project_domain_verified': bool(project_domain and project_domain.get('verified') is True),
        'project_domain_routing': domain_routing_view(project_domain),
        'dns_record_count_for_hostname': len(records),
        'preservation_baseline_sha256': baseline_hash,
        'already_satisfied': sorted(set(already)),
        'proposed_mutations': mutations,
        'allowed_mutations': [m['action'] for m in mutations],
        'aliases_retained': True,
        'deployment_protection_retained': True,
        'conflict_preflight_scope': 'selected_team_projects_and_domains_only',
    }
    digest = digest_for(plan_body)
    public_plan = {
        'status': 'plan',
        'digest': digest,
        **plan_body,
        'artifact': 'custom-domain-plan.json',
        'evidence_note': (
            'Domain collision preflight covers the selected Vercel team inventory only; '
            'other accounts are opaque. Provider attach still fail-closes on collision.'
        ),
    }
    if not mutations:
        public_plan['idempotent'] = True
        public_plan['note'] = 'already_configured_verify_only'
    emit(**public_plan)
    write_artifact(artifact_dir, 'custom-domain-plan.json', public_plan)
    return 0, public_plan


def revalidate_owned(vercel, token, team, pid, slug, rid):
    project = vercel.get_json(
        '/v9/projects/' + pid + '?' + urllib.parse.urlencode({'teamId': team}), token)
    if project.get('id') != pid or project.get('name') != slug or project.get('accountId') != team:
        raise Failure('project_scope_mismatch')
    envs = vercel.get_json(
        '/v9/projects/' + pid + '/env?' + urllib.parse.urlencode({'teamId': team}),
        token).get('envs')
    if not project_marker(envs, MARKER + rid):
        raise Failure('repository_marker_missing')
    return project


def txt_content_matches(content, value):
    return content in (value, '"' + value + '"')


def revalidate_apply_state(source, plan, completed, vercel, token, cf_token, before_first_write=False):
    """Recheck ownership, production target, recommendation, routing, challenges, DNS.

    Accounts for exact completed writes from this apply. Remaining API-level
    read/write races between a successful check and the subsequent write cannot
    be eliminated without provider transactions; concurrency only serializes
    same-app workflows.
    """
    rid, slug, automation_sha, repo = context(source)
    if (rid != plan['repository_id'] or slug != plan['project_name']
            or automation_sha != plan['automation_source_sha']):
        raise Failure('source_changed_replanning_required')
    team = plan['team_id']
    pid = plan['project_id']
    hostname = plan['hostname']
    zone_id = plan['zone_id']
    project = revalidate_owned(vercel, token, team, pid, slug, rid)
    deployment_id, app_sha, _remote = production_deployment(vercel, token, team, project, rid)
    if deployment_id != plan['app_deployment_id'] or app_sha != plan['app_deployment_sha']:
        raise Failure('deployment_changed_replanning_required')
    confirm_commit_in_repo(repo, app_sha)
    config = vercel.get_json(
        '/v6/domains/' + urllib.parse.quote(hostname, safe='')
        + '/config?' + urllib.parse.urlencode({'teamId': team}),
        token)
    if pick_recommended_cname(config) != plan['cname_target']:
        raise Failure('cname_recommendation_changed')

    domains = get_project_domains(vercel, token, team, pid)
    domain = next((d for d in domains if d.get('name') == hostname), None)
    completed_attach = any(c.get('action') == 'vercel_add_project_domain' for c in completed)
    completed_cname = any(c.get('action') == 'cloudflare_create_cname' for c in completed)
    completed_txt = [c for c in completed if c.get('action') == 'cloudflare_create_txt']
    completed_verify = any(c.get('action') == 'vercel_verify_project_domain' for c in completed)

    expected_domain = plan['existing_project_domain'] or completed_attach
    if expected_domain:
        if domain is None:
            raise Failure('project_domain_missing')
        if domain.get('projectId') != pid:
            raise Failure('project_domain_scope_mismatch')
        assert_production_domain_routing(domain)
        if completed_verify and domain.get('verified') is not True:
            raise Failure('domain_verify_readback_mismatch')
    elif domain is not None:
        raise Failure('project_domain_appeared_unexpectedly')

    if domain is not None and domain.get('verified') is not True and not completed_verify:
        # Live challenge set must match the approved plan baseline while unverified.
        # verified=True is the only allowed challenge-consumption transition.
        # Attach-in-progress plans have no routing/TXT baseline yet; staged apply does.
        approved_routing = plan.get('project_domain_routing')
        has_txt_mutations = any(
            m.get('action') == 'cloudflare_create_txt'
            for m in (plan.get('proposed_mutations') or []))
        if approved_routing is not None or has_txt_mutations:
            live_challenges = domain.get('verification') or []
            if not isinstance(live_challenges, list):
                raise Failure('verification_challenge_invalid')
            if approved_routing is not None:
                approved_challenges = approved_routing.get('verification') or []
            else:
                approved_challenges = challenge_views([
                    {
                        'type': 'TXT',
                        'domain': m['name'],
                        'value': m['value'],
                        'reason': m.get('reason'),
                    }
                    for m in (plan.get('proposed_mutations') or [])
                    if m.get('action') == 'cloudflare_create_txt'
                ])
            live_views = challenge_views(live_challenges)
            if live_views != approved_challenges:
                raise Failure('verification_challenge_changed')
            for challenge in live_challenges:
                if isinstance(challenge, dict) and challenge.get('type') == 'TXT':
                    if not challenge_in_scope(challenge, hostname, plan['zone_name']):
                        raise Failure('verification_challenge_out_of_scope')

    records = dns_records_for_name(cf_token, zone_id, hostname)
    expected_cname = plan['cname_target']
    matching_cname = [
        r for r in records
        if r.get('type') == 'CNAME'
        and str(r.get('content', '')).rstrip('.').lower() == expected_cname
        and r.get('proxied') is False
    ]
    require_cname = (
        completed_cname
        or 'cname_present' in (plan.get('already_satisfied') or [])
    )
    if require_cname:
        if matching_cname:
            pass
        elif any(r.get('type') == 'CNAME' for r in records) or records:
            raise Failure('dns_conflict_before_write')
        else:
            raise Failure('dns_cname_lost_after_write')

    for mutation in plan.get('proposed_mutations') or []:
        if mutation.get('action') != 'cloudflare_create_txt':
            continue
        if not any(c.get('action') == 'cloudflare_create_txt'
                   and c.get('name') == mutation['name']
                   and c.get('value') == mutation['value'] for c in completed):
            continue
        current = dns_records_for_name(cf_token, zone_id, mutation['name'])
        matching = [r for r in current if r.get('type') == 'TXT'
                    and txt_content_matches(r.get('content'), mutation['value'])]
        if not matching:
            raise Failure('dns_txt_lost_after_write')

    if before_first_write:
        zone_records = zone_dns_inventory(cf_token, zone_id)
        baseline_hash, _ = preservation_hash(zone_records, domain)
        if baseline_hash != plan['preservation_baseline_sha256']:
            raise Failure('dns_or_domain_baseline_changed')
        if domain is not None:
            live_routing = domain_routing_view(domain)
            planned_routing = plan.get('project_domain_routing')
            if live_routing != planned_routing:
                raise Failure('project_domain_routing_changed')

    return project, domain, app_sha


def apply_plan(source, binary, artifact_dir, approved_digest):
    if not approved_digest or not re.fullmatch(r'[0-9a-f]{64}', approved_digest):
        raise Failure('approved_plan_digest_required')
    code, plan = build_plan(source, binary, artifact_dir)
    if plan.get('status') != 'plan':
        return code
    if plan.get('ownership') == 'missing_marker':
        raise Failure('unmarked_project_requires_explicit_recovery')
    if plan.get('digest') != approved_digest:
        raise Failure('approved_plan_digest_mismatch')
    mutations = plan.get('proposed_mutations') or []
    if not mutations:
        emit(status='already_configured', digest=plan['digest'],
             project_id=plan['project_id'], hostname=plan['hostname'],
             app_deployment_sha=plan['app_deployment_sha'])
        return 0

    rid = plan['repository_id']
    team = plan['team_id']
    pid = plan['project_id']
    slug = plan['project_name']
    hostname = plan['hostname']
    zone_id = plan['zone_id']
    token = os.environ['VERCEL_API_TOKEN']
    cf_token = os.environ['CLOUDFLARE_API_TOKEN']
    vercel = load('check-vercel-access')

    completed = []
    try:
        revalidate_apply_state(
            source, plan, completed, vercel, token, cf_token, before_first_write=True)

        for mutation in mutations:
            action = mutation['action']
            revalidate_apply_state(
                source, plan, completed, vercel, token, cf_token, before_first_write=False)
            if action == 'vercel_add_project_domain':
                if any(c.get('action') == action for c in completed):
                    continue
                domain_elsewhere(vercel, token, team, hostname, pid)
                try:
                    created = vercel_cli(
                        binary, source, team, 'api',
                        '/v10/projects/' + pid + '/domains',
                        '-X', 'POST', '--input', '-',
                        value=json.dumps({'name': hostname}))
                except Failure as exc:
                    if str(exc) == 'cli_failed':
                        # Fail closed on provider collision (OpenAPI 409) or other attach errors.
                        try:
                            domain_elsewhere(vercel, token, team, hostname, pid)
                        except Failure:
                            raise
                        raise Failure('domain_attach_failed_closed') from None
                    raise
                if (created.get('name') != hostname or created.get('projectId') != pid
                        or 'verified' not in created):
                    raise Failure('domain_attach_readback_mismatch')
                assert_production_domain_routing(created)
                completed.append({
                    'action': action, 'name': hostname, 'verified': created.get('verified'),
                })
                # Finish approved CNAME writes first; TXT challenges need a new digest.
                for follow in list(mutations):
                    if follow['action'] != 'cloudflare_create_cname':
                        continue
                    if any(c.get('action') == 'cloudflare_create_cname' for c in completed):
                        break
                    revalidate_apply_state(
                        source, plan, completed, vercel, token, cf_token, before_first_write=False)
                    current = dns_records_for_name(cf_token, zone_id, hostname)
                    matching = [r for r in current
                                if r.get('type') == 'CNAME'
                                and str(r.get('content', '')).rstrip('.').lower() == follow['content']
                                and r.get('proxied') is False]
                    if matching:
                        completed.append({
                            'action': 'cloudflare_create_cname',
                            'id': matching[0].get('id'),
                            'idempotent': True,
                        })
                    elif current:
                        raise Failure('dns_conflict_before_write')
                    else:
                        created_dns = cf_request(
                            'POST', '/zones/' + zone_id + '/dns_records', cf_token, {
                                'type': 'CNAME',
                                'name': hostname,
                                'content': follow['content'],
                                'ttl': follow['ttl'],
                                'proxied': False,
                            }).get('result')
                        if not isinstance(created_dns, dict):
                            raise Failure('dns_create_readback_invalid')
                        if (created_dns.get('type') != 'CNAME'
                                or str(created_dns.get('name', '')).rstrip('.').lower() != hostname
                                or str(created_dns.get('content', '')).rstrip('.').lower()
                                != follow['content']
                                or created_dns.get('proxied') is not False):
                            raise Failure('dns_create_readback_mismatch')
                        completed.append({
                            'action': 'cloudflare_create_cname',
                            'id': created_dns.get('id'),
                            'content': follow['content'],
                        })
                if created.get('verified') is not True:
                    # Canonical staged approval: same build_plan() body/digest as next apply.
                    _code, live = build_plan(source, binary, artifact_dir)
                    if live.get('status') != 'plan' or live.get('ownership') == 'missing_marker':
                        raise Failure('postattach_plan_invalid')
                    if not live.get('proposed_mutations'):
                        raise Failure('domain_attach_unverified_without_challenge')
                    # Diagnostics stay outside the hashed plan body.
                    staged = {
                        **live,
                        'status': 'requires_additional_approval',
                        'completed_mutations': completed,
                        'previous_approved_digest': approved_digest,
                        'artifact': 'custom-domain-staged-plan.json',
                        'approval_note': (
                            'Staged digest is the canonical build_plan digest; '
                            'dispatch apply with this digest without replan if unchanged.'
                        ),
                    }
                    body = plan_body_from_public(live)
                    if digest_for(body) != live['digest']:
                        raise Failure('staged_digest_invariant_broken')
                    write_artifact(artifact_dir, 'custom-domain-staged-plan.json', staged)
                    emit(**staged)
                    return 1
                continue
            elif action == 'cloudflare_create_cname':
                if any(c.get('action') == 'cloudflare_create_cname' for c in completed):
                    continue
                current = dns_records_for_name(cf_token, zone_id, hostname)
                matching = [r for r in current
                            if r.get('type') == 'CNAME'
                            and str(r.get('content', '')).rstrip('.').lower() == mutation['content']
                            and r.get('proxied') is False]
                if matching:
                    completed.append({'action': action, 'id': matching[0].get('id'), 'idempotent': True})
                    continue
                if current:
                    raise Failure('dns_conflict_before_write')
                created = cf_request('POST', '/zones/' + zone_id + '/dns_records', cf_token, {
                    'type': 'CNAME',
                    'name': hostname,
                    'content': mutation['content'],
                    'ttl': mutation['ttl'],
                    'proxied': False,
                }).get('result')
                if not isinstance(created, dict):
                    raise Failure('dns_create_readback_invalid')
                if (created.get('type') != 'CNAME'
                        or str(created.get('name', '')).rstrip('.').lower() != hostname
                        or str(created.get('content', '')).rstrip('.').lower() != mutation['content']
                        or created.get('proxied') is not False):
                    raise Failure('dns_create_readback_mismatch')
                completed.append({'action': action, 'id': created.get('id'), 'content': mutation['content']})
            elif action == 'cloudflare_create_txt':
                name = mutation['name']
                value = mutation['value']
                current = dns_records_for_name(cf_token, zone_id, name)
                matching = [r for r in current
                            if r.get('type') == 'TXT' and txt_content_matches(r.get('content'), value)]
                if matching:
                    completed.append({'action': action, 'id': matching[0].get('id'),
                                      'name': name, 'value': value, 'idempotent': True})
                    continue
                approved_challenges = (plan.get('project_domain_routing') or {}).get('verification', [])
                approved_values = {
                    c['value'] for c in approved_challenges
                    if c.get('type') == 'TXT'
                    and normalize_dns_name(c['domain']) == name
                }
                if any(r.get('type') != 'TXT' or not any(
                        txt_content_matches(r.get('content'), approved_value)
                        for approved_value in approved_values) for r in current):
                    raise Failure('txt_conflict_before_write')
                if not challenge_in_scope(
                        {'type': 'TXT', 'domain': name, 'value': value, 'reason': 'apply'},
                        hostname, plan['zone_name']):
                    raise Failure('verification_challenge_out_of_scope')
                created = cf_request('POST', '/zones/' + zone_id + '/dns_records', cf_token, {
                    'type': 'TXT',
                    'name': name,
                    'content': value,
                    'ttl': CNAME_TTL,
                }).get('result')
                if not isinstance(created, dict) or created.get('type') != 'TXT':
                    raise Failure('txt_create_readback_mismatch')
                completed.append({'action': action, 'id': created.get('id'),
                                  'name': name, 'value': value})
            elif action == 'vercel_verify_project_domain':
                if any(c.get('action') == action for c in completed):
                    continue
                verified = vercel_cli(
                    binary, source, team, 'api',
                    '/v9/projects/' + pid + '/domains/' + urllib.parse.quote(hostname, safe='')
                    + '/verify',
                    '-X', 'POST')
                if (verified.get('name') != hostname or verified.get('projectId') != pid
                        or verified.get('verified') is not True):
                    raise Failure('domain_verify_incomplete')
                assert_production_domain_routing(verified)
                completed.append({'action': action, 'verified': True})
            else:
                raise Failure('mutation_unsupported')

        # Final readiness: staged pending paths return earlier. apply_complete needs
        # attachment + production routing + verified + matching non-proxied CNAME.
        _project, domain, app_sha = revalidate_apply_state(
            source, plan, completed, vercel, token, cf_token, before_first_write=False)
        if domain is None:
            raise Failure('domain_not_attached_at_completion')
        assert_production_domain_routing(domain)
        if domain.get('verified') is not True:
            raise Failure('domain_not_verified_at_completion')
        final_records = dns_records_for_name(cf_token, zone_id, hostname)
        final_cname = [
            r for r in final_records
            if r.get('type') == 'CNAME'
            and str(r.get('content', '')).rstrip('.').lower() == plan['cname_target']
            and r.get('proxied') is False
        ]
        if not final_cname:
            raise Failure('dns_cname_required_at_completion')
    except Failure as exc:
        emit(status='apply_partial_checkpoint', error=str(exc),
             completed_mutations=completed, digest=plan['digest'],
             note='Replan after discovering exact owned state; no automatic cleanup')
        return 1

    emit(status='apply_complete', digest=plan['digest'], project_id=pid,
         hostname=hostname, completed_mutations=completed,
         app_deployment_sha=app_sha, automation_source_sha=plan['automation_source_sha'],
         rollback_documentation={
             'created': completed,
             'preserve': ['preexisting_correct_records', 'aliases', 'deployment_protection',
                          'apex', 'mail', 'unrelated_dns'],
             'note': 'Manual rollback only for app-owned records created above; no destructive automation',
             'race_limitation': (
                 'Provider APIs are not transactional; a change between the last revalidation '
                 'and a write can still race. Same-app concurrency reduces but does not eliminate that.'
             ),
         })
    return 0


def get_page(url):
    try:
        with urllib.request.build_opener(NoRedirect()).open(url, timeout=30) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise Failure('response_bound')
            return response.status, body, dict(response.headers)
    except urllib.error.HTTPError as exc:
        location = urllib.parse.urlsplit(exc.headers.get('Location', ''))
        if (exc.code in (302, 303, 307, 308) and location.scheme == 'https'
                and location.netloc == 'vercel.com' and location.path == '/sso-api'):
            return 401, b'', {}
        return exc.code, b'', {}
    except OSError:
        raise Failure('verification_transport') from None


def ensure_dig():
    try:
        result = subprocess.run(
            ['dig', '-v'], capture_output=True, text=True, timeout=10,
            env={k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR')})
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('dig_unavailable') from None
    if result.returncode != 0 and not (result.stderr or result.stdout):
        raise Failure('dig_unavailable')


def dig_run(args):
    ensure_dig()
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=15,
            env={k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR')})
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('dns_lookup_failed') from None
    if result.returncode != 0:
        raise Failure('dns_lookup_failed')
    return result.stdout


def resolve_cname(name, server=None):
    """Recursive (+short) CNAME lookup for public resolvers — not authoritative."""
    args = ['dig', '+short', 'CNAME', name]
    if server:
        args.insert(1, '@' + server)
    output = dig_run(args)
    values = [line.strip().rstrip('.').lower() for line in output.splitlines() if line.strip()]
    # Ignore lines that are comments or statuses.
    return [v for v in values if HOSTNAME_RE.fullmatch(v) or DNS_NAME_RE.fullmatch(v)]


def parse_authoritative_cname(output):
    status_match = re.search(r'\bstatus:\s*([A-Z]+)\b', output)
    flags_match = re.search(r'\bflags:\s*([a-z0-9 ]+)\b', output)
    if not status_match or not flags_match:
        raise Failure('dns_authoritative_parse_failed')
    status = status_match.group(1)
    flags = flags_match.group(1).split()
    if status != 'NOERROR':
        raise Failure('dns_authoritative_status_' + status.lower())
    if 'aa' not in flags:
        raise Failure('dns_authoritative_not_aa')
    values = []
    in_answer = False
    for line in output.splitlines():
        if line.startswith(';; ANSWER SECTION'):
            in_answer = True
            continue
        if in_answer:
            if line.startswith(';;') or not line.strip():
                break
            parts = line.split()
            if len(parts) >= 5 and parts[3] == 'CNAME':
                values.append(parts[4].rstrip('.').lower())
    return values


def resolve_cname_authoritative(name, nameserver):
    ns = normalize_hostname(nameserver)
    output = dig_run([
        'dig', '@' + ns, '+norecurse', '+time=5', '+tries=1', 'CNAME', name,
    ])
    return parse_authoritative_cname(output)


def check_authoritative_cname(hostname, nameservers, expected):
    if not nameservers:
        return 'failed', ['zone_nameservers_missing']
    failures = []
    passed = 0
    pending = 0
    configured = list(nameservers[:5])
    # Bound NS queries; every configured authority must match for full passed.
    for ns in configured:
        try:
            values = resolve_cname_authoritative(hostname, ns)
            if expected in values:
                passed += 1
            elif values:
                failures.append('authoritative_cname_mismatch')
            else:
                pending += 1
        except Failure as exc:
            failures.append(str(exc))
    if passed == len(configured) and not failures and not pending:
        return 'passed', []
    if 'authoritative_cname_mismatch' in failures:
        return 'failed', failures
    if passed and (pending or failures):
        return 'pending', failures + ['authoritative_cname_incomplete']
    if pending and not failures:
        return 'pending', []
    if failures:
        return 'failed', failures
    return 'pending', ['authoritative_cname_incomplete']


def check_certificate(hostname):
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=30) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as tls:
                cert = tls.getpeercert()
    except OSError:
        raise Failure('tls_handshake_failed') from None
    if not cert:
        raise Failure('tls_certificate_missing')
    san = []
    for kind, value in cert.get('subjectAltName', ()):
        if kind == 'DNS':
            san.append(value.lower().rstrip('.'))
    if hostname not in san and '*.' + '.'.join(hostname.split('.')[1:]) not in san:
        raise Failure('tls_san_mismatch')
    return {'san_matched': True, 'not_after': cert.get('notAfter')}


def provider_readiness(plan, vercel, token, team, pid, hostname):
    checks = {
        'provider_domain_attached': 'pending',
        'provider_production_routing': 'pending',
        'provider_domain_verified': 'pending',
        'configuration_mutations_outstanding': 'pending',
    }
    if plan.get('proposed_mutations'):
        checks['configuration_mutations_outstanding'] = 'failed'
    else:
        checks['configuration_mutations_outstanding'] = 'passed'
    domains = get_project_domains(vercel, token, team, pid)
    domain = next((d for d in domains if d.get('name') == hostname), None)
    if domain is None:
        checks['provider_domain_attached'] = 'failed'
        checks['provider_production_routing'] = 'failed'
        checks['provider_domain_verified'] = 'failed'
        return checks, domain, 'not_configured'
    checks['provider_domain_attached'] = 'passed'
    try:
        assert_production_domain_routing(domain)
        checks['provider_production_routing'] = 'passed'
    except Failure:
        checks['provider_production_routing'] = 'failed'
        return checks, domain, 'routing_conflict'
    if domain.get('verified') is True:
        checks['provider_domain_verified'] = 'passed'
    else:
        checks['provider_domain_verified'] = 'failed'
        return checks, domain, 'pending_verification'
    if checks['configuration_mutations_outstanding'] != 'passed':
        return checks, domain, 'pending_configuration'
    return checks, domain, 'ready'


def verification_result_finalize(artifact_dir, result):
    """Persist + emit verify evidence for every exit path (never omit the artifact)."""
    result = dict(result)
    result.setdefault('artifact', 'custom-domain-verify.json')
    if 'checks' in result:
        result.setdefault(
            'pending_checks', [k for k, v in result['checks'].items() if v == 'pending'])
        result.setdefault(
            'failed_checks', [k for k, v in result['checks'].items() if v == 'failed'])
        result.setdefault('failure_reasons', sorted(set(result.get('failure_reasons') or [])))
    write_artifact(artifact_dir, 'custom-domain-verify.json', result)
    emit(**result)
    return 0 if result.get('status') == 'verification_passed' else 1


def verify_domain(source, binary, artifact_dir):
    checks = None
    failures = []
    plan = None
    hostname = None
    app_sha = None
    try:
        code, plan = build_plan(source, binary, artifact_dir)
        if plan.get('status') != 'plan':
            return code
        if plan.get('ownership') == 'missing_marker':
            raise Failure('unmarked_project_requires_explicit_recovery')
        hostname = plan['hostname']
        expected = plan['cname_target']
        app_sha = plan['app_deployment_sha']
        team = plan['team_id']
        pid = plan['project_id']
        token = os.environ['VERCEL_API_TOKEN']
        vercel = load('check-vercel-access')
        nameservers = plan.get('zone_name_servers') or []

        checks = {
            'provider_domain_attached': 'pending',
            'provider_production_routing': 'pending',
            'provider_domain_verified': 'pending',
            'configuration_mutations_outstanding': 'pending',
            'authoritative_cname': 'pending',
            'resolver_1_1_1_1': 'pending',
            'resolver_8_8_8_8': 'pending',
            'tls': 'pending',
            'http_home': 'pending',
            'http_health': 'pending',
            'production_sha_stable': 'pending',
        }

        provider_checks, _domain, readiness = provider_readiness(
            plan, vercel, token, team, pid, hostname)
        checks.update(provider_checks)
        if readiness != 'ready':
            for key, value in provider_checks.items():
                if value == 'failed':
                    failures.append(key)
            return verification_result_finalize(artifact_dir, {
                'status': (
                    'verification_not_configured' if readiness == 'not_configured'
                    else 'verification_pending' if readiness.startswith('pending')
                    else 'verification_routing_conflict' if readiness == 'routing_conflict'
                    else 'verification_incomplete'
                ),
                'readiness': readiness,
                'hostname': hostname,
                'project_id': pid,
                'app_deployment_sha': app_sha,
                'automation_source_sha': plan['automation_source_sha'],
                'checks': checks,
                'failure_reasons': sorted(set(failures)),
            })

        dns_keys = ('authoritative_cname', 'resolver_1_1_1_1', 'resolver_8_8_8_8')
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                auth_state, auth_failures = check_authoritative_cname(
                    hostname, nameservers, expected)
                checks['authoritative_cname'] = auth_state
                failures.extend(auth_failures)
                for label, server in (('resolver_1_1_1_1', PUBLIC_RESOLVERS[0]),
                                      ('resolver_8_8_8_8', PUBLIC_RESOLVERS[1])):
                    values = resolve_cname(hostname, server)
                    if expected in values:
                        checks[label] = 'passed'
                    else:
                        checks[label] = 'pending' if not values else 'failed'
                        if values:
                            failures.append(label + '_mismatch')
                if all(checks[k] == 'passed' for k in dns_keys):
                    break
            except Failure as exc:
                failures.append(str(exc))
                if str(exc) == 'dig_unavailable':
                    checks['authoritative_cname'] = 'failed'
                    break
            if attempt + 1 < VERIFY_ATTEMPTS:
                time.sleep(VERIFY_SLEEP_SEC)

        try:
            checks['tls'] = 'passed' if check_certificate(hostname) else 'failed'
        except Failure as exc:
            checks['tls'] = 'failed'
            failures.append(str(exc))

        url = 'https://' + hostname
        try:
            status, page, _headers = get_page(url)
        except Failure as exc:
            checks['http_home'] = 'failed'
            failures.append(str(exc))
            status, page = None, b''
        if status in (401, 403):
            return verification_result_finalize(artifact_dir, {
                'status': 'ready_but_verification_protected',
                'readiness': 'protected',
                'hostname': hostname,
                'project_id': plan['project_id'],
                'app_deployment_sha': app_sha,
                'automation_source_sha': plan['automation_source_sha'],
                'checks': checks,
                'failure_reasons': sorted(set(failures)),
            })
        if status is not None:
            if status != 200 or not re.search(br'<(?:html|!doctype html)\b', page, re.I):
                checks['http_home'] = 'failed'
                failures.append('homepage_invalid')
            else:
                checks['http_home'] = 'passed'

        try:
            status, body, _ = get_page(url + '/api/health')
        except Failure as exc:
            checks['http_health'] = 'failed'
            failures.append(str(exc))
            status, body = None, b''
        if status in (401, 403):
            return verification_result_finalize(artifact_dir, {
                'status': 'ready_but_verification_protected',
                'readiness': 'protected',
                'hostname': hostname,
                'project_id': plan['project_id'],
                'app_deployment_sha': app_sha,
                'automation_source_sha': plan['automation_source_sha'],
                'checks': checks,
                'failure_reasons': sorted(set(failures)),
            })
        if status is not None:
            try:
                health = json.loads(body)
            except ValueError:
                health = None
            if status != 200 or health != {
                    'status': 'ready', 'service': 'app', 'revision': app_sha}:
                checks['http_health'] = 'failed'
                failures.append('health_revision_invalid')
            else:
                checks['http_health'] = 'passed'

        # Final provider + ownership recheck immediately before completion.
        try:
            project = revalidate_owned(
                vercel, token, team, pid, plan['project_name'], plan['repository_id'])
            final_provider, _final_domain, final_readiness = provider_readiness(
                plan, vercel, token, team, pid, hostname)
            for key, value in final_provider.items():
                checks[key] = value
                if value == 'failed':
                    failures.append(key)
            if final_readiness != 'ready':
                failures.append('provider_readiness_' + final_readiness)
            _dpl, live_sha, _remote = production_deployment(
                vercel, token, team, project, plan['repository_id'])
            if live_sha != app_sha:
                checks['production_sha_stable'] = 'failed'
                failures.append('production_sha_changed_during_verify')
            else:
                checks['production_sha_stable'] = 'passed'
        except Failure as exc:
            checks['production_sha_stable'] = 'failed'
            failures.append(str(exc))

        pending = [k for k, v in checks.items() if v == 'pending']
        failed = [k for k, v in checks.items() if v == 'failed']
        passed = not pending and not failed
        return verification_result_finalize(artifact_dir, {
            'status': 'verification_passed' if passed else 'verification_incomplete',
            'readiness': 'ready' if passed else 'incomplete',
            'hostname': hostname,
            'project_id': plan['project_id'],
            'app_deployment_sha': app_sha,
            'automation_source_sha': plan['automation_source_sha'],
            'checks': checks,
            'pending_checks': pending,
            'failed_checks': failed,
            'failure_reasons': sorted(set(failures)),
            'documented_checks': [
                'provider_domain_attached', 'provider_production_routing',
                'provider_domain_verified', 'configuration_mutations_outstanding',
                'authoritative_cname_aa', 'public_resolver_1_1_1_1', 'public_resolver_8_8_8_8',
                'tls_san', 'http_home', 'http_health_production_sha',
                'production_sha_stable', 'final_provider_readiness',
            ],
        })
    except Failure as exc:
        # Plan/setup failures before checks: minimal artifact, never fake check rows.
        if checks is None:
            return verification_result_finalize(artifact_dir, {
                'status': str(exc),
                'readiness': 'error',
                'hostname': hostname or (plan or {}).get('hostname'),
                'project_id': (plan or {}).get('project_id'),
                'app_deployment_sha': app_sha or (plan or {}).get('app_deployment_sha'),
                'automation_source_sha': (plan or {}).get('automation_source_sha'),
                'failure_reasons': [str(exc)],
            })
        for key, value in list(checks.items()):
            if value == 'pending':
                # Leave untouched pending unless the failure maps to a known check.
                pass
        if str(exc) == 'verification_transport':
            if checks.get('http_home') == 'pending':
                checks['http_home'] = 'failed'
            if checks.get('http_health') == 'pending':
                checks['http_health'] = 'failed'
        failures.append(str(exc))
        return verification_result_finalize(artifact_dir, {
            'status': 'verification_incomplete',
            'readiness': 'incomplete',
            'hostname': hostname or (plan or {}).get('hostname'),
            'project_id': (plan or {}).get('project_id'),
            'app_deployment_sha': app_sha or (plan or {}).get('app_deployment_sha'),
            'automation_source_sha': (plan or {}).get('automation_source_sha'),
            'checks': checks,
            'failure_reasons': sorted(set(failures)),
        })


def run(source, binary, operation, approved_digest, artifact_dir):
    artifact_dir = str(Path(artifact_dir).resolve())
    try:
        if operation == 'plan':
            code, _plan = build_plan(source, binary, artifact_dir)
            return code
        if operation == 'apply':
            return apply_plan(source, binary, artifact_dir, approved_digest)
        if operation == 'verify':
            return verify_domain(source, binary, artifact_dir)
        raise Failure('operation_invalid')
    except Failure as exc:
        emit(status=str(exc))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--vercel-bin', required=True)
    parser.add_argument('--operation', choices=('plan', 'apply', 'verify'), required=True)
    parser.add_argument('--approved-plan-digest', default='')
    parser.add_argument('--artifact-dir', default='.')
    args = parser.parse_args()
    try:
        return run(Path(args.source).resolve(), args.vercel_bin, args.operation,
                   (args.approved_plan_digest or '').strip(), args.artifact_dir)
    except Exception:
        emit(status='unexpected_failure')
        return 1


if __name__ == '__main__':
    sys.exit(main())

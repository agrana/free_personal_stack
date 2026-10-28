"""Offline custom-domain contracts: fake providers, real CLI subprocess boundary."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('custom_domain', ROOT / 'scripts/custom-domain.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

STUB = '''#!/usr/bin/env python3
import json, os, pathlib, sys, urllib.parse
p = pathlib.Path(__file__).with_suffix('.json')
s = json.loads(p.read_text())
a = sys.argv[1:]
assert os.environ.get('VERCEL_TOKEN') == 'offline-token'
assert 'CLOUDFLARE_API_TOKEN' not in os.environ
assert 'VERCEL_API_TOKEN' not in os.environ
assert '--token' not in a and '--scope' in a
value = sys.stdin.read()
s['calls'].append([a, value])
if a[0] == 'api' and a[1].endswith('/domains') and '-X' in a and a[a.index('-X')+1] == 'POST':
    assert a[a.index('--input')+1] == '-'
    body = json.loads(value)
    assert body == {'name': s['hostname']}
    s['project_domain'] = {
        'name': body['name'], 'projectId': s['project']['id'], 'apexName': 'example.com',
        'verified': s.get('attach_verified', False),
        'verification': s.get('attach_verification', []),
        'gitBranch': None, 'customEnvironmentId': None, 'redirect': None, 'redirectStatusCode': None,
    }
    print(json.dumps(s['project_domain']))
elif a[0] == 'api' and '/verify' in a[1] and '-X' in a:
    assert s['project_domain']['name'] in a[1]
    s['project_domain']['verified'] = True
    s['project_domain']['verification'] = []
    s['project_domain']['gitBranch'] = None
    s['project_domain']['customEnvironmentId'] = None
    s['project_domain']['redirect'] = None
    s['project_domain']['redirectStatusCode'] = None
    print(json.dumps({
        'name': s['project_domain']['name'], 'projectId': s['project']['id'],
        'apexName': 'example.com', 'verified': True,
        'gitBranch': None, 'customEnvironmentId': None, 'redirect': None, 'redirectStatusCode': None,
    }))
else:
    raise AssertionError(a)
p.write_text(json.dumps(s))
'''


class CustomDomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        (self.source / 'app.txt').write_text('independent application')
        subprocess.run(['git', 'add', '.'], cwd=self.source, check=True)
        subprocess.run(
            ['git', '-c', 'user.name=Offline', '-c', 'user.email=offline@example.invalid',
             'commit', '-qm', 'independent'],
            cwd=self.source, check=True)
        self.automation_sha = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip()
        self.app_sha = 'b' * 40
        self.binary = self.base / 'vercel'
        self.binary.write_text(STUB)
        self.binary.chmod(0o755)
        self.statefile = self.binary.with_suffix('.json')
        self.artifacts = self.base / 'artifacts'
        self.artifacts.mkdir()
        self.hostname = 'app.example.com'
        self.zone = 'example.com'
        self.cname = 'cname.vercel-dns.example.com'
        self.save({
            'calls': [],
            'hostname': self.hostname,
            'project': {
                'id': 'prj_Offline',
                'name': 'example-app',
                'accountId': 'team_Offline',
                'targets': {'production': {'id': 'dpl_Offline'}},
            },
            'envs': [{
                'key': 'TEMPLATE_REPOSITORY_ID_2000000001',
                'target': ['production'],
                'type': 'encrypted',
                'visibility': 'config',
            }],
            'remote': {
                'id': 'dpl_Offline',
                'url': 'offline.vercel.app',
                'projectId': 'prj_Offline',
                'target': 'production',
                'readyState': 'READY',
                'meta': {
                    'githubCommitSha': self.app_sha,
                    'githubRepoId': '2000000001',
                },
                'alias': ['production.vercel.app'],
            },
            'project_domain': None,
            'domains_by_project': {'prj_Offline': []},
            'extra_projects': [],
            'domain_config': {
                'acceptedChallenges': ['dns-01'],
                'configuredBy': None,
                'misconfigured': True,
                'recommendedCNAME': [{'rank': 1, 'value': self.cname}],
                'recommendedIPv4': [],
            },
            'zones': [{
                'id': 'zone_Offline',
                'name': self.zone,
                'status': 'active',
                'account': {'id': 'acct_Offline'},
                'name_servers': ['ns1.example-ns.com', 'ns2.example-ns.com'],
            }],
            'dns': [],
            'cf_writes': [],
            'attach_verified': True,
            'attach_verification': [],
            'inventory_pages': None,
        })
        self.env = {
            'GITHUB_REPOSITORY': 'Owner/Example-App',
            'GITHUB_REPOSITORY_ID': '2000000001',
            'GITHUB_SHA': self.automation_sha,
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'VERCEL_API_TOKEN': 'offline-token',
            'VERCEL_TEAM_ID': 'team_Offline',
            'CLOUDFLARE_API_TOKEN': 'cf_offline_token',
            'APP_HOSTNAME': self.hostname,
            'CLOUDFLARE_ZONE_NAME': self.zone,
            'GITHUB_TOKEN': 'offline-github',
        }
        self.vercel = app.load('check-vercel-access')
        self.patches = [
            patch.dict(os.environ, self.env),
            patch.object(app, 'github', side_effect=self.github),
            patch.object(app, 'load', side_effect=lambda name: self.vercel),
            patch.object(self.vercel, 'items', side_effect=self.items),
            patch.object(self.vercel, 'get_json', side_effect=self.read),
            patch.object(app, 'cf_request', side_effect=self.cf_request),
            patch.object(app, 'time'),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        app.time.sleep = lambda *_a, **_k: None

    def save(self, state):
        self.statefile.write_text(json.dumps(state))

    def state(self):
        return json.loads(self.statefile.read_text())

    def github(self, path):
        if path.startswith('/repositories/'):
            return {
                'id': int(os.environ['GITHUB_REPOSITORY_ID']),
                'full_name': os.environ['GITHUB_REPOSITORY'],
                'is_template': False,
                'fork': False,
                'default_branch': 'main',
            }
        if path.endswith('/commits/main'):
            return {'sha': self.automation_sha}
        if '/commits/' + self.app_sha in path:
            return {'sha': self.app_sha}
        if '/commits/' in path:
            return {'sha': path.rsplit('/', 1)[-1]}
        raise AssertionError(path)

    def _page(self, key, items, next_cursor=None):
        return {
            key: items,
            'pagination': {
                'count': len(items),
                'next': next_cursor,
                'prev': None,
            },
        }

    def _domains_for(self, pid):
        s = self.state()
        domains = list(s['domains_by_project'].get(pid, []))
        if s.get('project_domain') and s.get('project') and pid == s['project']['id']:
            domains = [d for d in domains if d.get('name') != s['project_domain']['name']]
            domains.append(s['project_domain'])
        return domains

    def items(self, route, key, token, query=None):
        # Legacy helper path retained for unrelated check-vercel-access callers.
        s = self.state()
        if key == 'teams':
            return iter([{'id': 'team_Offline'}])
        if key == 'projects':
            projects = [s['project']] if s['project'] else []
            projects = projects + list(s.get('extra_projects') or [])
            return iter(projects)
        if key == 'domains':
            pid = route.split('/')[3]
            return iter(self._domains_for(pid))
        raise AssertionError((route, key))

    def read(self, path, token):
        s = self.state()
        if path.startswith('/v2/teams'):
            mode = s.get('inventory_mode', {}).get('teams')
            if mode == 'missing_pagination':
                return {'teams': [{'id': 'team_Offline'}]}
            if mode == 'wrong_shape':
                return {'teams': 'nope', 'pagination': {'count': 1, 'next': None, 'prev': None}}
            return self._page('teams', [{'id': 'team_Offline'}])
        if path.startswith('/v9/projects?') or path.startswith('/v9/projects&'):
            mode = s.get('inventory_mode', {}).get('projects')
            pages = (s.get('inventory_pages') or {}).get('projects')
            if mode == 'missing_pagination':
                projects = [s['project']] if s['project'] else []
                projects = projects + list(s.get('extra_projects') or [])
                return {'projects': projects}
            if mode == 'wrong_shape':
                return {'projects': {'id': 'x'}, 'pagination': {'count': 1, 'next': None, 'prev': None}}
            if mode == 'duplicate_cursor' or pages is not None:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                until = (query.get('until') or [None])[0]
                page_list = pages or [
                    [s['project']] if s['project'] else [],
                    list(s.get('extra_projects') or []),
                ]
                if until is None:
                    nxt = 1 if len(page_list) > 1 else None
                    if mode == 'duplicate_cursor':
                        nxt = 1
                    return self._page('projects', page_list[0], nxt)
                index = int(until)
                if mode == 'duplicate_cursor':
                    return self._page('projects', page_list[0], 1)
                if index < 0 or index >= len(page_list):
                    raise self.vercel.CheckFailure('Invalid inventory cursor')
                nxt = index + 1 if index + 1 < len(page_list) else None
                return self._page('projects', page_list[index], nxt)
            projects = [s['project']] if s['project'] else []
            projects = projects + list(s.get('extra_projects') or [])
            return self._page('projects', projects)
        if path.startswith('/v9/projects/') and '/env' in path:
            return {'envs': s['envs']}
        if '/domains?' in path or path.rstrip('/').endswith('/domains'):
            mode = s.get('inventory_mode', {}).get('domains')
            pid = path.split('/')[3]
            domains = self._domains_for(pid)
            if mode == 'missing_pagination':
                return {'domains': domains}
            if mode == 'wrong_shape':
                return {'domains': None, 'pagination': {'count': 0, 'next': None, 'prev': None}}
            pages = (s.get('inventory_pages') or {}).get('domains', {}).get(pid)
            if pages is not None:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                until = (query.get('until') or [None])[0]
                if until is None:
                    nxt = 1 if len(pages) > 1 else None
                    return self._page('domains', pages[0], nxt)
                index = int(until)
                if index < 0 or index >= len(pages):
                    raise self.vercel.CheckFailure('Invalid inventory cursor')
                nxt = index + 1 if index + 1 < len(pages) else None
                return self._page('domains', pages[index], nxt)
            return self._page('domains', domains)
        if path.startswith('/v9/projects/') and '/domains' not in path:
            return s['project']
        if path.startswith('/v13/deployments/'):
            return s['remote']
        if path.startswith('/v6/domains/') and path.endswith('/config') or '/config?' in path:
            return s['domain_config']
        if '/project-domains' in path:
            raise AssertionError('apex project-domains lookup must not be used for subdomains')
        raise AssertionError(path)

    def cf_request(self, method, path, token, payload=None, query=None):
        assert token == 'cf_offline_token'
        s = self.state()
        if method == 'GET' and path == '/zones':
            name = (query or {}).get('name')
            zones = [z for z in s['zones'] if name is None or z.get('name') == name]
            if (query or {}).get('status') == 'active':
                zones = [z for z in zones if z.get('status') == 'active']
            return {
                'success': True,
                'result': zones,
                'result_info': {'page': 1, 'per_page': 50, 'total_pages': 1, 'total_count': len(zones)},
            }
        if method == 'GET' and path.endswith('/dns_records'):
            name = (query or {}).get('name')
            records = [r for r in s['dns'] if name is None or r.get('name') == name]
            return {
                'success': True,
                'result': records,
                'result_info': {'page': 1, 'per_page': 50, 'total_pages': 1, 'total_count': len(records)},
            }
        if method == 'POST' and path.endswith('/dns_records'):
            assert payload['proxied'] is False if payload.get('type') == 'CNAME' else True
            created = {
                'id': 'dns_' + str(len(s['cf_writes']) + 1),
                'type': payload['type'],
                'name': payload['name'],
                'content': payload['content'],
                'proxied': payload.get('proxied', False),
                'ttl': payload.get('ttl', 300),
            }
            s['dns'].append(created)
            s['cf_writes'].append(payload)
            self.save(s)
            return {'success': True, 'result': created}
        raise AssertionError((method, path, payload, query))

    def run_op(self, operation, digest='', capture=True):
        kwargs = dict(
            source=self.source,
            binary=str(self.binary),
            operation=operation,
            approved_digest=digest,
            artifact_dir=str(self.artifacts),
        )
        if capture:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = app.run(**kwargs)
            lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
            return code, lines
        return app.run(**kwargs)

    def plan(self):
        code, lines = self.run_op('plan')
        self.assertEqual(code, 0)
        self.assertTrue(lines)
        self.assertEqual(lines[-1]['status'], 'plan')
        return lines[-1]

    def production_domain(self, **overrides):
        base = {
            'name': self.hostname, 'projectId': 'prj_Offline', 'apexName': 'example.com',
            'verified': True,
            'gitBranch': None, 'customEnvironmentId': None,
            'redirect': None, 'redirectStatusCode': None,
            'verification': [],
        }
        base.update(overrides)
        return base

    def test_workflow_manual_only_and_shared_concurrency(self):
        wf = (ROOT / '.github/workflows/custom-domain.yml').read_text()
        self.assertIn('workflow_dispatch', wf)
        self.assertNotRegex(wf.split('jobs:', 1)[0], r'(?m)^\s+push:\s*$')
        self.assertNotRegex(wf.split('jobs:', 1)[0], r'(?m)^\s+pull_request:\s*$')
        self.assertRegex(wf, r'contents:\s*read')
        self.assertIn('app-provider-${{ github.repository_id }}', wf)
        self.assertIn('vercel@65.4.0', wf)
        self.assertIn('node-version: \'24\'', wf)
        self.assertIn('vars.APP_HOSTNAME', wf)
        self.assertIn('vars.CLOUDFLARE_ZONE_NAME', wf)
        self.assertIn('secrets.CLOUDFLARE_API_TOKEN', wf)
        self.assertIn('--approved-plan-digest "$APPROVED_PLAN_DIGEST"', wf)
        launch = (ROOT / '.github/workflows/launch-app.yml').read_text()
        self.assertIn('app-provider-${{ github.repository_id }}', launch)
        self.assertNotIn('alfonsograna.com', wf)
        self.assertIn("github.repository_id != '1051601680'", wf)
        docs = (ROOT / 'docs/custom-domain.md').read_text()
        self.assertIn('app.example.com', docs)
        self.assertNotRegex(docs, r'agrana|alfonsograna\.com')
        self.assertIn('team_…', docs)
        self.assertIn('build_plan()', docs)
        self.assertIn('selected team', docs.lower())

    def test_setup_required_no_provider_writes(self):
        for key in ('VERCEL_API_TOKEN', 'CLOUDFLARE_API_TOKEN', 'APP_HOSTNAME'):
            old = os.environ[key]
            os.environ[key] = '' if key != 'VERCEL_API_TOKEN' else 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
            code, lines = self.run_op('plan')
            self.assertEqual(code, 0)
            self.assertEqual(lines[-1]['status'], 'setup_required')
            self.assertEqual(self.state()['calls'], [])
            self.assertEqual(self.state()['cf_writes'], [])
            os.environ[key] = old

    def test_plan_emits_digest_and_artifact_without_writes(self):
        plan = self.plan()
        self.assertRegex(plan['digest'], r'^[0-9a-f]{64}$')
        self.assertEqual(plan['cname_target'], self.cname)
        self.assertEqual(plan['app_deployment_sha'], self.app_sha)
        self.assertEqual(plan['automation_source_sha'], self.automation_sha)
        self.assertNotEqual(plan['app_deployment_sha'], plan['automation_source_sha'])
        self.assertIn('vercel_add_project_domain', plan['allowed_mutations'])
        self.assertIn('cloudflare_create_cname', plan['allowed_mutations'])
        self.assertEqual(plan['conflict_preflight_scope'], 'selected_team_projects_and_domains_only')
        self.assertEqual(self.state()['calls'], [])
        self.assertEqual(self.state()['cf_writes'], [])
        artifact = json.loads((self.artifacts / 'custom-domain-plan.json').read_text())
        self.assertEqual(artifact['digest'], plan['digest'])
        # Preservation is hash-only; raw unrelated DNS must not appear in the artifact.
        self.assertNotIn('zone_dns', json.dumps(artifact))
        self.assertIn('preservation_baseline_sha256', artifact)

    def test_apply_requires_digest_and_rejects_stale(self):
        plan = self.plan()
        code, lines = self.run_op('apply', digest='')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'approved_plan_digest_required')
        self.assertEqual(self.state()['calls'], [])
        code, lines = self.run_op('apply', digest='a' * 64)
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'approved_plan_digest_mismatch')
        self.assertEqual(self.state()['calls'], [])
        s = self.state()
        s['domain_config']['recommendedCNAME'] = [{'rank': 1, 'value': 'other.vercel-dns.example.com'}]
        self.save(s)
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertIn(lines[-1]['status'], {
            'approved_plan_digest_mismatch', 'cname_recommendation_changed'})

    def test_apply_creates_domain_and_cname_idempotent(self):
        plan = self.plan()
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['status'], 'apply_complete')
        self.assertEqual(len(self.state()['cf_writes']), 1)
        self.assertEqual(self.state()['cf_writes'][0]['type'], 'CNAME')
        self.assertEqual(self.state()['cf_writes'][0]['proxied'], False)
        self.assertTrue(self.state()['calls'])
        plan2 = self.plan()
        self.assertEqual(plan2.get('proposed_mutations'), [])
        code, lines = self.run_op('apply', digest=plan2['digest'])
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['status'], 'already_configured')
        self.assertEqual(len(self.state()['cf_writes']), 1)

    def test_conflicting_dns_and_other_team_project_fail_closed(self):
        s = self.state()
        s['dns'] = [{'id': 'dns_x', 'type': 'A', 'name': self.hostname, 'content': '203.0.113.10',
                     'proxied': False, 'ttl': 300}]
        self.save(s)
        code, lines = self.run_op('plan')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'plan_conflict_existing_records')
        s = self.state()
        s['dns'] = []
        s['extra_projects'] = [{'id': 'prj_Other', 'name': 'other-app', 'accountId': 'team_Offline'}]
        s['domains_by_project'] = {
            'prj_Offline': [],
            'prj_Other': [{'name': self.hostname, 'projectId': 'prj_Other', 'verified': True}],
        }
        self.save(s)
        code, lines = self.run_op('plan')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'domain_bound_to_other_project')

    def test_inventory_missing_collection_and_page2_conflict(self):
        # Missing domains collection must fail closed.
        def bad_read(path, token):
            if '/domains?' in path or path.rstrip('/').endswith('/domains'):
                raise self.vercel.CheckFailure('Invalid inventory page')
            return self.read(path, token)

        with patch.object(self.vercel, 'get_json', side_effect=bad_read):
            code, lines = self.run_op('plan')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'vercel_inventory_incomplete')
        self.assertEqual(self.state()['cf_writes'], [])

        # Page-2 foreign project domain conflict via multi-project inventory.
        s = self.state()
        s['extra_projects'] = [
            {'id': 'prj_Page2', 'name': 'page-two', 'accountId': 'team_Offline'},
        ]
        s['domains_by_project'] = {
            'prj_Offline': [],
            'prj_Page2': [{
                'name': self.hostname, 'projectId': 'prj_Page2', 'verified': True,
                'gitBranch': None, 'redirect': None,
            }],
        }
        s['inventory_pages'] = {
            'projects': [
                [s['project']],
                [{'id': 'prj_Page2', 'name': 'page-two', 'accountId': 'team_Offline'}],
            ],
        }
        self.save(s)
        code, lines = self.run_op('plan')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'domain_bound_to_other_project')
        self.assertEqual(self.state()['cf_writes'], [])

    def test_hostname_zone_validation(self):
        for host, err in [
            ('https://app.example.com', 'hostname_invalid'),
            ('app.example.com/path', 'hostname_invalid'),
            ('*.example.com', 'hostname_invalid'),
            ('example.com', 'apex_hostname_forbidden'),
            ('www.example.com', 'reserved_hostname_forbidden'),
            ('mail.example.com', 'reserved_hostname_forbidden'),
            ('app.other.example.com', 'hostname_not_single_subdomain'),
            ('app.example.com.evil.example.com', 'hostname_not_single_subdomain'),
            ('app.examples.com', 'hostname_outside_zone'),
        ]:
            os.environ['APP_HOSTNAME'] = host
            os.environ['CLOUDFLARE_ZONE_NAME'] = self.zone
            code, lines = self.run_op('plan')
            self.assertEqual(code, 1, host)
            self.assertEqual(lines[-1]['status'], err, host)
        os.environ['APP_HOSTNAME'] = self.hostname

    def test_malformed_cname_recommendation_rejected(self):
        for config in (
            {'recommendedCNAME': []},
            {'recommendedCNAME': [{'rank': 1, 'value': ''}]},
            {'recommendedCNAME': [{'rank': 2, 'value': self.cname}]},
            {'recommendedCNAME': [{'rank': 1, 'value': 'not a host'}]},
            {'recommendedCNAME': 'cname.vercel-dns.example.com'},
            {'recommendedCNAME': [{'rank': True, 'value': self.cname}]},
            {'recommendedCNAME': [{'rank': 1.9, 'value': self.cname}]},
            {'recommendedCNAME': [{'rank': float('nan'), 'value': self.cname}]},
            {'recommendedCNAME': [{'rank': 0, 'value': self.cname}]},
            {'recommendedCNAME': [{'rank': 1, 'value': '127.0.0.1'}]},
            {'recommendedCNAME': [{'rank': 1, 'value': 'localhost'}]},
            {'recommendedCNAME': [{'rank': 1, 'value': 'evil.localhost'}]},
            {'recommendedCNAME': [{'rank': 1, 'value': 'http://cname.vercel-dns.example.com'}]},
        ):
            s = self.state()
            base = {
                'acceptedChallenges': ['dns-01'],
                'configuredBy': None,
                'misconfigured': True,
                'recommendedIPv4': [],
            }
            base.update(config)
            s['domain_config'] = base
            self.save(s)
            code, lines = self.run_op('plan')
            self.assertEqual(code, 1, config)
            self.assertIn('cname_recommendation', lines[-1]['status'], config)

    def test_missing_marker_plan_blocker_no_apply(self):
        s = self.state()
        s['envs'] = []
        self.save(s)
        plan = self.plan()
        self.assertEqual(plan['ownership'], 'missing_marker')
        self.assertEqual(plan['proposed_mutations'], [])
        self.assertEqual(plan['discovered_candidate_project_id'], 'prj_Offline')
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'unmarked_project_requires_explicit_recovery')
        self.assertEqual(self.state()['calls'], [])

    def test_legacy_deployment_without_github_repo_id_rejected(self):
        s = self.state()
        s['remote']['meta'] = {'githubCommitSha': self.app_sha}
        self.save(s)
        code, lines = self.run_op('plan')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'production_deployment_invalid')

    def test_wrong_team_repo_event_and_upstream_rejected(self):
        for key, value, err in [
            ('VERCEL_TEAM_ID', 'team_Wrong', 'team_unavailable'),
            ('GITHUB_REPOSITORY_ID', app.UPSTREAM_ID, 'template_or_repository_invalid'),
            ('GITHUB_EVENT_NAME', 'push', 'event_not_authorized'),
            ('GITHUB_REF', 'refs/heads/feature', 'event_not_authorized'),
        ]:
            old = os.environ[key]
            os.environ[key] = value
            code, lines = self.run_op('plan')
            self.assertEqual(code, 1)
            self.assertEqual(lines[-1]['status'], err)
            os.environ[key] = old
        self.assertEqual(self.state()['calls'], [])

    def test_staged_txt_digest_applies_directly_without_replan(self):
        s = self.state()
        s['attach_verified'] = False
        s['attach_verification'] = [{
            'type': 'TXT',
            'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=synthetic-challenge',
            'reason': 'pending_domain_verification',
        }]
        self.save(s)
        plan = self.plan()
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        staged = lines[-1]
        self.assertEqual(staged['status'], 'requires_additional_approval')
        self.assertIn('completed_mutations', staged)
        self.assertTrue(any(m['action'] == 'cloudflare_create_txt' for m in staged['proposed_mutations']))
        # Staged digest must equal a fresh canonical plan digest for the live state.
        fresh = self.plan()
        self.assertEqual(staged['digest'], fresh['digest'])
        body = app.plan_body_from_public(fresh)
        self.assertEqual(app.digest_for(body), staged['digest'])
        self.assertEqual(len([w for w in self.state()['cf_writes'] if w['type'] == 'CNAME']), 1)
        self.assertEqual(len([w for w in self.state()['cf_writes'] if w['type'] == 'TXT']), 0)
        # Direct subsequent apply with staged digest succeeds unchanged (no silent replan).
        code, lines = self.run_op('apply', digest=staged['digest'])
        self.assertEqual(code, 0, lines[-1])
        self.assertEqual(lines[-1]['status'], 'apply_complete')
        self.assertEqual(len([w for w in self.state()['cf_writes'] if w['type'] == 'TXT']), 1)

    def test_staged_challenge_rotation_invalidates_digest(self):
        s = self.state()
        s['attach_verified'] = False
        s['attach_verification'] = [{
            'type': 'TXT',
            'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=first',
            'reason': 'pending_domain_verification',
        }]
        self.save(s)
        plan = self.plan()
        code, lines = self.run_op('apply', digest=plan['digest'])
        staged = lines[-1]
        self.assertEqual(staged['status'], 'requires_additional_approval')
        s = self.state()
        rotated = [{
            'type': 'TXT',
            'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=rotated',
            'reason': 'pending_domain_verification',
        }]
        s['project_domain']['verification'] = rotated
        s['attach_verification'] = rotated
        self.save(s)
        code, lines = self.run_op('apply', digest=staged['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'approved_plan_digest_mismatch')
        self.assertEqual(len([w for w in self.state()['cf_writes'] if w['type'] == 'TXT']), 0)

    def test_multiple_challenges_and_existing_txt_noop(self):
        s = self.state()
        challenges = [
            {
                'type': 'TXT', 'domain': '_vercel.app.example.com',
                'value': 'vc-domain-verify=one', 'reason': 'a',
            },
            {
                'type': 'TXT', 'domain': '_acme.app.example.com',
                'value': 'vc-domain-verify=two', 'reason': 'b',
            },
        ]
        s['project_domain'] = self.production_domain(verified=False, verification=challenges)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [
            {
                'id': 'dns_txt1', 'type': 'TXT', 'name': '_vercel.app.example.com',
                'content': 'vc-domain-verify=one', 'proxied': False, 'ttl': 300,
            },
            {
                'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
                'content': self.cname, 'proxied': False, 'ttl': 300,
            },
        ]
        self.save(s)
        plan = self.plan()
        actions = [m['action'] for m in plan['proposed_mutations']]
        self.assertIn('cloudflare_create_txt', actions)
        self.assertIn('vercel_verify_project_domain', actions)
        self.assertIn('txt_challenge_present', plan['already_satisfied'])
        # Existing matching TXT must not be rewritten; only missing challenge is created.
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 0)
        txt_writes = [w for w in self.state()['cf_writes'] if w['type'] == 'TXT']
        self.assertEqual(len(txt_writes), 1)
        self.assertEqual(txt_writes[0]['name'], '_acme.app.example.com')

    def test_production_routing_conflicts_and_noop(self):
        for overrides, err in (
            ({'gitBranch': 'feature/x'}, 'project_domain_not_production_routing'),
            ({'redirect': 'other.example.com', 'redirectStatusCode': 307},
             'project_domain_not_production_routing'),
            ({'customEnvironmentId': 'env_x'}, 'project_domain_not_production_routing'),
        ):
            s = self.state()
            s['project_domain'] = self.production_domain(**overrides)
            s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
            s['dns'] = [{
                'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
                'content': self.cname, 'proxied': False, 'ttl': 300,
            }]
            self.save(s)
            code, lines = self.run_op('plan')
            self.assertEqual(code, 1, overrides)
            self.assertEqual(lines[-1]['status'], err, overrides)

        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        plan = self.plan()
        self.assertEqual(plan['proposed_mutations'], [])
        self.assertTrue(plan.get('idempotent'))
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['status'], 'already_configured')

    def test_routing_drift_after_plan_blocks_writes(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[{
            'type': 'TXT', 'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=x', 'reason': 'pending',
        }])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        plan = self.plan()
        s = self.state()
        s['project_domain']['gitBranch'] = 'feature/drift'
        self.save(s)
        code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertIn(lines[-1]['status'], {
            'approved_plan_digest_mismatch', 'project_domain_not_production_routing',
            'project_domain_routing_changed', 'dns_or_domain_baseline_changed',
        })
        self.assertEqual(self.state()['cf_writes'], [])

    def test_deployment_flip_before_first_write_partial_checkpoint(self):
        plan = self.plan()
        original = app.revalidate_apply_state
        flips = {'n': 0}

        def flipper(*args, **kwargs):
            flips['n'] += 1
            if flips['n'] == 1 and kwargs.get('before_first_write'):
                s = self.state()
                s['remote'] = dict(s['remote'])
                s['remote']['id'] = 'dpl_Flipped'
                s['remote']['meta'] = dict(s['remote']['meta'])
                s['remote']['meta']['githubCommitSha'] = 'c' * 40
                s['project'] = dict(s['project'])
                s['project']['targets'] = {'production': {'id': 'dpl_Flipped'}}
                self.save(s)

                def github_flip(path):
                    if path.startswith('/repositories/'):
                        return self.github(path)
                    if path.endswith('/commits/main'):
                        return {'sha': self.automation_sha}
                    if '/commits/' + ('c' * 40) in path:
                        return {'sha': 'c' * 40}
                    return self.github(path)

                with patch.object(app, 'github', side_effect=github_flip):
                    return original(*args, **kwargs)
            return original(*args, **kwargs)

        with patch.object(app, 'revalidate_apply_state', side_effect=flipper):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'apply_partial_checkpoint')
        self.assertEqual(lines[-1]['error'], 'deployment_changed_replanning_required')
        self.assertEqual(self.state()['cf_writes'], [])
        self.assertEqual(self.state()['calls'], [])

    def test_challenge_rotate_between_mutations_stops_before_txt_write(self):
        s = self.state()
        s['attach_verified'] = False
        s['attach_verification'] = [{
            'type': 'TXT',
            'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=first',
            'reason': 'pending_domain_verification',
        }]
        self.save(s)
        plan = self.plan()
        code, lines = self.run_op('apply', digest=plan['digest'])
        staged = lines[-1]
        self.assertEqual(staged['status'], 'requires_additional_approval')
        # Mid second apply: after preflight, rotate challenge before TXT write.
        original = app.revalidate_apply_state
        seen = {'n': 0}

        def rotator(*args, **kwargs):
            seen['n'] += 1
            # Rotate before the revalidation that precedes the TXT write.
            if seen['n'] == 2:
                st = self.state()
                st['project_domain']['verification'] = [{
                    'type': 'TXT',
                    'domain': '_vercel.app.example.com',
                    'value': 'vc-domain-verify=rotated',
                    'reason': 'pending_domain_verification',
                }]
                self.save(st)
            return original(*args, **kwargs)

        with patch.object(app, 'revalidate_apply_state', side_effect=rotator):
            code, lines = self.run_op('apply', digest=staged['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'apply_partial_checkpoint')
        self.assertEqual(lines[-1]['error'], 'verification_challenge_changed')
        self.assertEqual(len([w for w in self.state()['cf_writes'] if w['type'] == 'TXT']), 0)

    def test_out_of_scope_txt_rejected(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[{
            'type': 'TXT', 'domain': 'example.com', 'value': 'steal-apex', 'reason': 'x',
        }])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        self.save(s)
        code, lines = self.run_op('plan')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'verification_challenge_out_of_scope')

    def test_authoritative_dns_requires_aa_and_both_public_resolvers(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        dig_calls = []

        def fake_dig_run(args):
            dig_calls.append(list(args))
            if '+short' in args:
                # Public resolver path.
                server = next((a[1:] for a in args if a.startswith('@')), None)
                if server == '1.1.1.1':
                    return self.cname + '.\n'
                if server == '8.8.8.8':
                    return self.cname + '.\n'
                return ''
            # Authoritative path must use @ns +norecurse.
            self.assertIn('+norecurse', args)
            self.assertTrue(any(a.startswith('@') for a in args))
            ns = next(a[1:] for a in args if a.startswith('@'))
            return (
                f';; flags: qr aa rd; status: NOERROR\n'
                f';; ANSWER SECTION:\n'
                f'{self.hostname}. 300 IN CNAME {self.cname}.\n'
            )

        def fake_cert(hostname):
            return {'san_matched': True, 'not_after': 'never'}

        def fake_page(url):
            if url.endswith('/api/health'):
                return 200, json.dumps({
                    'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                }).encode(), {}
            return 200, b'<!doctype html><html>ok</html>', {}

        with patch.object(app, 'dig_run', side_effect=fake_dig_run), \
                patch.object(app, 'ensure_dig', return_value=None), \
                patch.object(app, 'check_certificate', side_effect=fake_cert), \
                patch.object(app, 'get_page', side_effect=fake_page):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['status'], 'verification_passed')
        self.assertTrue(any('+norecurse' in c for c in dig_calls))
        self.assertTrue(any('@ns1.example-ns.com' in c or '@ns2.example-ns.com' in c for c in dig_calls))

    def test_dns_pending_until_both_public_resolvers_pass(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        attempt = {'n': 0}

        def fake_auth(hostname, nameservers, expected):
            return 'passed', []

        def fake_resolve(name, server=None):
            attempt['n'] += 1
            # First poll: only 1.1.1.1 has the answer.
            if attempt['n'] <= 2:
                if server == '1.1.1.1':
                    return [self.cname]
                return []
            return [self.cname]

        with patch.object(app, 'check_authoritative_cname', side_effect=fake_auth), \
                patch.object(app, 'resolve_cname', side_effect=fake_resolve), \
                patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                patch.object(app, 'get_page', side_effect=lambda url: (
                    (200, json.dumps({
                        'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                    }).encode(), {}) if url.endswith('/api/health')
                    else (200, b'<!doctype html><html>ok</html>', {})
                )):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['checks']['resolver_1_1_1_1'], 'passed')
        self.assertEqual(lines[-1]['checks']['resolver_8_8_8_8'], 'passed')
        self.assertGreaterEqual(attempt['n'], 3)

    def test_authoritative_ns_failure_and_disagreement(self):
        self.assertEqual(
            app.check_authoritative_cname(self.hostname, [], self.cname)[0], 'failed')

        def dig_fail(args):
            raise app.Failure('dns_lookup_failed')

        with patch.object(app, 'dig_run', side_effect=dig_fail), \
                patch.object(app, 'ensure_dig', return_value=None):
            state, _ = app.check_authoritative_cname(
                self.hostname, ['ns1.example-ns.com'], self.cname)
            self.assertEqual(state, 'failed')

        def dig_mismatch(args):
            return (
                ';; flags: qr aa; status: NOERROR\n'
                ';; ANSWER SECTION:\n'
                f'{self.hostname}. 300 IN CNAME other.example.com.\n'
            )

        with patch.object(app, 'dig_run', side_effect=dig_mismatch), \
                patch.object(app, 'ensure_dig', return_value=None):
            state, reasons = app.check_authoritative_cname(
                self.hostname, ['ns1.example-ns.com'], self.cname)
            self.assertEqual(state, 'failed')
            self.assertIn('authoritative_cname_mismatch', reasons)

    def test_verify_rejects_unverified_and_missing_domain(self):
        s = self.state()
        s['project_domain'] = None
        s['domains_by_project'] = {'prj_Offline': []}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        # Missing domain still has attach mutation outstanding from plan.
        code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertIn(lines[-1]['status'], {
            'verification_not_configured', 'verification_pending', 'verification_incomplete'})
        self.assertTrue((self.artifacts / 'custom-domain-verify.json').is_file())

        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        self.save(s)
        code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertNotEqual(lines[-1]['status'], 'verification_passed')
        self.assertIn(lines[-1].get('readiness'), {
            'pending_verification', 'pending_configuration', 'incomplete', 'not_configured'})

    def test_verify_branch_redirect_conflict_not_ready(self):
        s = self.state()
        s['project_domain'] = self.production_domain(gitBranch='feature/x')
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        # Plan fails closed on routing, or verify reports routing conflict.
        self.assertIn(lines[-1]['status'], {
            'project_domain_not_production_routing', 'verification_routing_conflict',
            'verification_incomplete'})

    def test_verify_checks_deployment_sha_not_automation_sha(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)

        def fake_auth(hostname, nameservers, expected):
            return 'passed', []

        def fake_resolve(name, server=None):
            return [self.cname]

        def fake_cert(hostname):
            return {'san_matched': True, 'not_after': 'never'}

        def fake_page(url):
            if url.endswith('/api/health'):
                return 200, json.dumps({
                    'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                }).encode(), {}
            return 200, b'<!doctype html><html>ok</html>', {}

        with patch.object(app, 'check_authoritative_cname', side_effect=fake_auth), \
                patch.object(app, 'resolve_cname', side_effect=fake_resolve), \
                patch.object(app, 'check_certificate', side_effect=fake_cert), \
                patch.object(app, 'get_page', side_effect=fake_page):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['status'], 'verification_passed')
        self.assertEqual(lines[-1]['app_deployment_sha'], self.app_sha)
        self.assertEqual(lines[-1]['automation_source_sha'], self.automation_sha)

    def test_verify_sha_change_during_probe_blocks_success(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        original_prod = app.production_deployment
        calls = {'n': 0}

        def flipping_prod(*args, **kwargs):
            calls['n'] += 1
            target, sha, remote = original_prod(*args, **kwargs)
            if calls['n'] >= 2:
                return target, 'd' * 40, remote
            return target, sha, remote

        with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                patch.object(app, 'get_page', side_effect=lambda url: (
                    (200, json.dumps({
                        'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                    }).encode(), {}) if url.endswith('/api/health')
                    else (200, b'<!doctype html><html>ok</html>', {})
                )), \
                patch.object(app, 'production_deployment', side_effect=flipping_prod), \
                patch.object(app, 'confirm_commit_in_repo', return_value=None):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'verification_incomplete')
        self.assertEqual(lines[-1]['checks']['production_sha_stable'], 'failed')

    def test_verify_protected_persists_artifact(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                patch.object(app, 'check_certificate', side_effect=app.Failure('tls_handshake_failed')), \
                patch.object(app, 'get_page', return_value=(401, b'', {})):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'ready_but_verification_protected')
        artifact = json.loads((self.artifacts / 'custom-domain-verify.json').read_text())
        self.assertEqual(artifact['status'], 'ready_but_verification_protected')

    def test_preservation_hash_includes_unrelated_zone_records(self):
        s = self.state()
        s['dns'] = [
            {'id': 'dns_mx', 'type': 'MX', 'name': self.zone, 'content': 'mail.example.com',
             'proxied': False, 'ttl': 300},
            {'id': 'dns_apex', 'type': 'A', 'name': self.zone, 'content': '203.0.113.10',
             'proxied': False, 'ttl': 300},
        ]
        self.save(s)
        plan = self.plan()
        digest1 = plan['preservation_baseline_sha256']
        s = self.state()
        s['dns'].append({
            'id': 'dns_other', 'type': 'TXT', 'name': 'other.example.com',
            'content': 'unrelated', 'proxied': False, 'ttl': 300,
        })
        self.save(s)
        plan2 = self.plan()
        self.assertNotEqual(digest1, plan2['preservation_baseline_sha256'])

    def test_challenge_order_canonicalization_stable_digest(self):
        challenges_a = [
            {'type': 'TXT', 'domain': '_b.app.example.com', 'value': 'b', 'reason': 'r'},
            {'type': 'TXT', 'domain': '_a.app.example.com', 'value': 'a', 'reason': 'r'},
        ]
        challenges_b = list(reversed(challenges_a))
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=challenges_a)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        plan_a = self.plan()
        s = self.state()
        s['project_domain']['verification'] = challenges_b
        self.save(s)
        plan_b = self.plan()
        self.assertEqual(plan_a['digest'], plan_b['digest'])
        self.assertEqual(
            plan_a['project_domain_routing']['verification'],
            plan_b['project_domain_routing']['verification'])

    def test_no_secret_leak_in_emitted_status(self):
        code, lines = self.run_op('plan')
        blob = json.dumps(lines)
        self.assertNotIn('offline-token', blob)
        self.assertNotIn('cf_offline_token', blob)
        self.assertNotIn('offline-github', blob)

    def test_onboarding_requires_verified_hostname(self):
        for name in ('AGENTS.md', 'README.md', 'docs/secrets-only-launch.md',
                     'docs/custom-domain.md', 'docs/new-app-runbook.md'):
            with self.subTest(document=name):
                text = (ROOT / name).read_text().lower()
                self.assertIn('onboarding is complete only after', text)
                for requirement in ('dns', 'https', 'revision', 'hostname'):
                    self.assertIn(requirement, text)
                for obsolete in ('optional custom domain', 'optional post-launch custom domain',
                                 'optional advanced cloudflare dns', 'custom domain (optional)'):
                    self.assertNotIn(obsolete, text)
        self.assertIn('## Required hostname and DNS', (ROOT / 'AGENTS.md').read_text())

    def test_operator_contract_requires_github_database_and_migrations(self):
        for name in ('AGENTS.md', 'README.md', 'docs/secrets-only-launch.md',
                     'docs/custom-domain.md', 'docs/new-app-runbook.md'):
            with self.subTest(document=name):
                text = (ROOT / name).read_text().lower()
                for requirement in ('database', 'migration', 'rls', 'end-to-end-onboarding.md'):
                    self.assertIn(requirement, text)
        agents = (ROOT / 'AGENTS.md').read_text()
        self.assertIn('## Verification through GitHub Actions', agents)
        self.assertIn('Before provisioning an application project', agents)
        self.assertNotIn('Database integration requires Podman', agents)
        self.assertNotIn('npm ci', agents)
        self.assertIn('implementation gap', agents)
        self.assertTrue((ROOT / 'docs/maintainer-verification.md').is_file())
        status = (ROOT / 'docs/end-to-end-onboarding.md').read_text()
        self.assertIn('current migration workflow', agents)
        self.assertIn('explicitly disables live migrations', status)
        quality = (ROOT / '.github/workflows/app-quality.yml').read_text()
        self.assertIn('sudo apt-get install -y podman', quality)

    def test_docs_linked_from_launch_and_agents(self):
        self.assertIn('custom-domain.md', (ROOT / 'docs/secrets-only-launch.md').read_text())
        self.assertIn('custom-domain.md', (ROOT / 'AGENTS.md').read_text())
        self.assertIn('custom-domain.md', (ROOT / 'README.md').read_text())
        self.assertIn('custom_domain_test.py', (ROOT / 'scripts/run-all-tests.py').read_text())

    def test_preexisting_cname_loss_after_txt_blocks_apply(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[{
            'type': 'TXT', 'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=x', 'reason': 'pending',
        }])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        plan = self.plan()
        self.assertIn('cname_present', plan['already_satisfied'])
        orig_cf = self.cf_request

        def drop_cname_after_txt(method, path, token, payload=None, query=None):
            out = orig_cf(method, path, token, payload, query)
            if method == 'POST' and payload and payload.get('type') == 'TXT':
                st = self.state()
                st['dns'] = [r for r in st['dns'] if r.get('type') != 'CNAME']
                self.save(st)
            return out

        with patch.object(app, 'cf_request', side_effect=drop_cname_after_txt):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'apply_partial_checkpoint')
        self.assertIn(lines[-1]['error'], {
            'dns_cname_lost_after_write', 'dns_conflict_before_write',
            'dns_cname_required_at_completion',
        })
        self.assertFalse(any('/verify' in str(c[0]) for c in self.state()['calls']))

    def test_verified_false_at_final_recheck_blocks_apply_complete(self):
        plan = self.plan()
        original = app.revalidate_apply_state
        calls = {'n': 0}

        def flip_verified(*args, **kwargs):
            calls['n'] += 1
            # Final readiness revalidate is the last call after attach+cname completed.
            if calls['n'] >= 4:
                st = self.state()
                if st.get('project_domain'):
                    st['project_domain']['verified'] = False
                    self.save(st)
            return original(*args, **kwargs)

        with patch.object(app, 'revalidate_apply_state', side_effect=flip_verified):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'apply_partial_checkpoint')
        self.assertEqual(lines[-1]['error'], 'domain_not_verified_at_completion')

    def test_challenge_rotate_after_txt_before_verify_blocks(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[{
            'type': 'TXT', 'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=first', 'reason': 'pending',
        }])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        plan = self.plan()
        orig_cf = self.cf_request

        def rotate_after_txt(method, path, token, payload=None, query=None):
            out = orig_cf(method, path, token, payload, query)
            if method == 'POST' and payload and payload.get('type') == 'TXT':
                st = self.state()
                st['project_domain']['verification'] = [{
                    'type': 'TXT', 'domain': '_vercel.app.example.com',
                    'value': 'vc-domain-verify=rotated', 'reason': 'pending',
                }]
                self.save(st)
            return out

        with patch.object(app, 'cf_request', side_effect=rotate_after_txt):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'apply_partial_checkpoint')
        self.assertEqual(lines[-1]['error'], 'verification_challenge_changed')
        self.assertFalse(any('/verify' in str(c[0]) for c in self.state()['calls']))

    def test_rotate_existing_noop_challenge_blocks_verify(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[{
            'type': 'TXT', 'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=noop', 'reason': 'pending',
        }])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [
            {
                'id': 'dns_txt', 'type': 'TXT', 'name': '_vercel.app.example.com',
                'content': 'vc-domain-verify=noop', 'proxied': False, 'ttl': 300,
            },
            {
                'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
                'content': self.cname, 'proxied': False, 'ttl': 300,
            },
        ]
        self.save(s)
        plan = self.plan()
        self.assertIn('txt_challenge_present', plan['already_satisfied'])
        self.assertEqual(
            [m['action'] for m in plan['proposed_mutations']],
            ['vercel_verify_project_domain'])
        original = app.revalidate_apply_state
        seen = {'n': 0}

        def rotator(*args, **kwargs):
            seen['n'] += 1
            if seen['n'] == 2:
                st = self.state()
                st['project_domain']['verification'] = [{
                    'type': 'TXT', 'domain': '_vercel.app.example.com',
                    'value': 'vc-domain-verify=rotated-noop', 'reason': 'pending',
                }]
                self.save(st)
            return original(*args, **kwargs)

        with patch.object(app, 'revalidate_apply_state', side_effect=rotator):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['error'], 'verification_challenge_changed')
        self.assertFalse(any('/verify' in str(c[0]) for c in self.state()['calls']))

    def test_same_name_challenges_apply_and_resume_without_duplicate_txt(self):
        for existing_count in (0, 1, 2):
            with self.subTest(existing_approved_values=existing_count):
                s = self.state()
                challenges = [
                    {'type': 'TXT', 'domain': '_vercel.app.example.com',
                     'value': 'vc-domain-verify=' + suffix, 'reason': 'pending'}
                    for suffix in ('one', 'two')
                ]
                s['project_domain'] = self.production_domain(
                    verified=False, verification=challenges)
                s['dns'] = [{'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
                             'content': self.cname, 'proxied': False, 'ttl': 300}]
                for i, challenge in enumerate(challenges[:existing_count]):
                    s['dns'].append({'id': 'dns_existing_' + str(i), 'type': 'TXT',
                                     'name': challenge['domain'],
                                     'content': challenge['value'], 'ttl': 300})
                s['cf_writes'] = []
                self.save(s)
                plan = self.plan()
                code, lines = self.run_op('apply', plan['digest'])
                self.assertEqual(code, 0, lines)
                self.assertEqual(lines[-1]['status'], 'apply_complete')
                final = self.state()
                txt = [r for r in final['dns'] if r['type'] == 'TXT']
                self.assertEqual({r['content'] for r in txt},
                                 {c['value'] for c in challenges})
                self.assertEqual(len(txt), 2)
                self.assertEqual(len(final['cf_writes']), 2 - existing_count)

    def test_multiple_challenges_same_dns_name_distinct_values(self):
        challenges = [
            {
                'type': 'TXT', 'domain': '_vercel.app.example.com',
                'value': 'vc-domain-verify=one', 'reason': 'a',
            },
            {
                'type': 'TXT', 'domain': '_vercel.app.example.com',
                'value': 'vc-domain-verify=two', 'reason': 'b',
            },
        ]
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=challenges)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        plan = self.plan()
        txt_muts = [m for m in plan['proposed_mutations'] if m['action'] == 'cloudflare_create_txt']
        self.assertEqual(len(txt_muts), 2)
        self.assertEqual({m['value'] for m in txt_muts}, {
            'vc-domain-verify=one', 'vc-domain-verify=two',
        })
        # Rotate only one value after first TXT write.
        orig_cf = self.cf_request
        wrote = {'n': 0}

        def rotate_one(method, path, token, payload=None, query=None):
            out = orig_cf(method, path, token, payload, query)
            if method == 'POST' and payload and payload.get('type') == 'TXT':
                wrote['n'] += 1
                if wrote['n'] == 1:
                    st = self.state()
                    st['project_domain']['verification'] = [
                        challenges[0],
                        {
                            'type': 'TXT', 'domain': '_vercel.app.example.com',
                            'value': 'vc-domain-verify=rotated-two', 'reason': 'b',
                        },
                    ]
                    self.save(st)
            return out

        with patch.object(app, 'cf_request', side_effect=rotate_one):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['error'], 'verification_challenge_changed')

    def test_verified_natural_transition_allows_idempotent_verify(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=False, verification=[{
            'type': 'TXT', 'domain': '_vercel.app.example.com',
            'value': 'vc-domain-verify=ok', 'reason': 'pending',
        }])
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [
            {
                'id': 'dns_txt', 'type': 'TXT', 'name': '_vercel.app.example.com',
                'content': 'vc-domain-verify=ok', 'proxied': False, 'ttl': 300,
            },
            {
                'id': 'dns_c', 'type': 'CNAME', 'name': self.hostname,
                'content': self.cname, 'proxied': False, 'ttl': 300,
            },
        ]
        self.save(s)
        plan = self.plan()
        original = app.revalidate_apply_state
        seen = {'n': 0}

        def consume(*args, **kwargs):
            seen['n'] += 1
            # After baseline revalidation, provider consumes challenges before verify.
            if seen['n'] == 2:
                st = self.state()
                st['project_domain']['verified'] = True
                st['project_domain']['verification'] = []
                self.save(st)
            return original(*args, **kwargs)

        with patch.object(app, 'revalidate_apply_state', side_effect=consume):
            code, lines = self.run_op('apply', digest=plan['digest'])
        self.assertEqual(code, 0, lines[-1])
        self.assertEqual(lines[-1]['status'], 'apply_complete')

    def test_verify_final_provider_routing_drift_after_http(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)

        def page(url):
            if url.endswith('/api/health'):
                return 200, json.dumps({
                    'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                }).encode(), {}
            st = self.state()
            st['project_domain']['gitBranch'] = 'feature/drift'
            self.save(st)
            return 200, b'<!doctype html><html>ok</html>', {}

        with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                patch.object(app, 'get_page', side_effect=page):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'verification_incomplete')
        self.assertEqual(lines[-1]['checks']['provider_production_routing'], 'failed')
        self.assertTrue((self.artifacts / 'custom-domain-verify.json').is_file())

    def test_verify_final_verified_false_after_http(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)

        def page(url):
            if url.endswith('/api/health'):
                st = self.state()
                st['project_domain']['verified'] = False
                self.save(st)
                return 200, json.dumps({
                    'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                }).encode(), {}
            return 200, b'<!doctype html><html>ok</html>', {}

        with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                patch.object(app, 'get_page', side_effect=page):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['checks']['provider_domain_verified'], 'failed')
        self.assertNotEqual(lines[-1]['status'], 'verification_passed')

    def test_verify_transport_persists_artifact_home_and_health(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        for which in ('home', 'health'):
            def page(url, target=which):
                if target == 'home' and not url.endswith('/api/health'):
                    raise app.Failure('verification_transport')
                if target == 'health' and url.endswith('/api/health'):
                    raise app.Failure('verification_transport')
                if url.endswith('/api/health'):
                    return 200, json.dumps({
                        'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                    }).encode(), {}
                return 200, b'<!doctype html><html>ok</html>', {}

            with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                    patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                    patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                    patch.object(app, 'get_page', side_effect=page):
                code, lines = self.run_op('verify')
            self.assertEqual(code, 1, which)
            artifact = self.artifacts / 'custom-domain-verify.json'
            self.assertTrue(artifact.is_file(), which)
            body = json.loads(artifact.read_text())
            self.assertIn('verification_transport', body.get('failure_reasons', [])
                          + [body.get('status')], which)
            blob = json.dumps(body) + json.dumps(lines)
            self.assertNotIn('offline-token', blob)
            self.assertNotIn('cf_offline_token', blob)

    def test_verify_final_ownership_failure_persists_artifact(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                patch.object(app, 'get_page', side_effect=lambda url: (
                    (200, json.dumps({
                        'status': 'ready', 'service': 'app', 'revision': self.app_sha,
                    }).encode(), {}) if url.endswith('/api/health')
                    else (200, b'<!doctype html><html>ok</html>', {})
                )), \
                patch.object(
                    app, 'revalidate_owned',
                    side_effect=app.Failure('repository_marker_missing')):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertTrue((self.artifacts / 'custom-domain-verify.json').is_file())
        body = json.loads((self.artifacts / 'custom-domain-verify.json').read_text())
        self.assertIn('repository_marker_missing', body.get('failure_reasons', []))
        self.assertEqual(body['checks']['production_sha_stable'], 'failed')
        self.assertNotIn('offline-token', json.dumps(body))

    def test_verify_malformed_health_persists_artifact(self):
        s = self.state()
        s['project_domain'] = self.production_domain(verified=True)
        s['domains_by_project'] = {'prj_Offline': [s['project_domain']]}
        s['dns'] = [{
            'id': 'dns_1', 'type': 'CNAME', 'name': self.hostname,
            'content': self.cname, 'proxied': False, 'ttl': 300,
        }]
        self.save(s)
        with patch.object(app, 'check_authoritative_cname', return_value=('passed', [])), \
                patch.object(app, 'resolve_cname', return_value=[self.cname]), \
                patch.object(app, 'check_certificate', return_value={'san_matched': True}), \
                patch.object(app, 'get_page', side_effect=lambda url: (
                    (200, b'not-json', {}) if url.endswith('/api/health')
                    else (200, b'<!doctype html><html>ok</html>', {})
                )):
            code, lines = self.run_op('verify')
        self.assertEqual(code, 1)
        self.assertTrue((self.artifacts / 'custom-domain-verify.json').is_file())
        self.assertEqual(lines[-1]['checks']['http_health'], 'failed')

    def test_authoritative_partial_match_unreachable_and_mismatch(self):
        def dig_match_then_fail(args):
            if '@ns1.example-ns.com' in args:
                return (
                    ';; flags: qr aa; status: NOERROR\n'
                    f';; ANSWER SECTION:\n{self.hostname}. 300 IN CNAME {self.cname}.\n'
                )
            raise app.Failure('dns_lookup_failed')

        with patch.object(app, 'dig_run', side_effect=dig_match_then_fail), \
                patch.object(app, 'ensure_dig', return_value=None):
            state, reasons = app.check_authoritative_cname(
                self.hostname, ['ns1.example-ns.com', 'ns2.example-ns.com'], self.cname)
        self.assertEqual(state, 'pending')
        self.assertIn('dns_lookup_failed', reasons)
        self.assertIn('authoritative_cname_incomplete', reasons)

        def dig_mismatch_then_match(args):
            if '@ns1.example-ns.com' in args:
                return (
                    ';; flags: qr aa; status: NOERROR\n'
                    f';; ANSWER SECTION:\n{self.hostname}. 300 IN CNAME other.example.com.\n'
                )
            return (
                ';; flags: qr aa; status: NOERROR\n'
                f';; ANSWER SECTION:\n{self.hostname}. 300 IN CNAME {self.cname}.\n'
            )

        with patch.object(app, 'dig_run', side_effect=dig_mismatch_then_match), \
                patch.object(app, 'ensure_dig', return_value=None):
            state, reasons = app.check_authoritative_cname(
                self.hostname, ['ns1.example-ns.com', 'ns2.example-ns.com'], self.cname)
        self.assertEqual(state, 'failed')
        self.assertIn('authoritative_cname_mismatch', reasons)

        def dig_all_match(args):
            return (
                ';; flags: qr aa; status: NOERROR\n'
                f';; ANSWER SECTION:\n{self.hostname}. 300 IN CNAME {self.cname}.\n'
            )

        with patch.object(app, 'dig_run', side_effect=dig_all_match), \
                patch.object(app, 'ensure_dig', return_value=None):
            state, reasons = app.check_authoritative_cname(
                self.hostname, ['ns1.example-ns.com', 'ns2.example-ns.com'], self.cname)
        self.assertEqual(state, 'passed')
        self.assertEqual(reasons, [])

    def test_strict_pagination_missing_wrong_duplicate_no_mutation(self):
        for mode in ('missing_pagination', 'wrong_shape', 'duplicate_cursor'):
            s = self.state()
            s['inventory_mode'] = {'projects': mode, 'domains': mode, 'teams': mode}
            if mode == 'duplicate_cursor':
                s['inventory_pages'] = {
                    'projects': [
                        [s['project']],
                        [{'id': 'prj_Other', 'name': 'other', 'accountId': 'team_Offline'}],
                    ],
                }
            self.save(s)
            code, lines = self.run_op('plan')
            self.assertEqual(code, 1, mode)
            self.assertEqual(lines[-1]['status'], 'vercel_inventory_incomplete', mode)
            self.assertEqual(self.state()['cf_writes'], [])
            self.assertEqual(self.state()['calls'], [])


if __name__ == '__main__':
    unittest.main()

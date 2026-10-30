"""Offline merge-driven onboarding: real git histories, stubbed providers/CLI."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

cfg_spec = importlib.util.spec_from_file_location(
    'onboarding_config', ROOT / 'scripts/onboarding-config.py')
cfg = importlib.util.module_from_spec(cfg_spec)
cfg_spec.loader.exec_module(cfg)

orch_spec = importlib.util.spec_from_file_location(
    'merge_onboarding', ROOT / 'scripts/merge-onboarding.py')
orch = importlib.util.module_from_spec(orch_spec)
orch_spec.loader.exec_module(orch)

launch_spec = importlib.util.spec_from_file_location(
    'launcher', ROOT / 'scripts/launch-app.py')
launch = importlib.util.module_from_spec(launch_spec)
launch_spec.loader.exec_module(launch)

domain_spec = importlib.util.spec_from_file_location(
    'custom_domain', ROOT / 'scripts/custom-domain.py')
domain = importlib.util.module_from_spec(domain_spec)
domain_spec.loader.exec_module(domain)


def write_config(source: Path, **overrides):
    payload = {
        'schema_version': 1,
        'hostname': None,
        'cloudflare_zone_name': None,
        'auto_apply': False,
        'expected_project_id': None,
        'confirm_repository_id': None,
    }
    payload.update(overrides)
    path = source / 'onboarding.config.json'
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def git_commit(source: Path, message: str = 'config'):
    subprocess.run(['git', 'add', '-A'], cwd=source, check=True)
    subprocess.run(
        ['git', '-c', 'user.name=Offline', '-c', 'user.email=offline@example.invalid',
         'commit', '-qm', message],
        cwd=source, check=True)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()


class OnboardingConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'app'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_config(self.source)
        self.sha = git_commit(self.source, 'seed')

    def test_default_template_disabled(self):
        # The reusable example stays disabled; generated applications enable their
        # tracked root policy through a PR without editing inherited test code.
        template = json.loads((ROOT / 'examples/onboarding.config.example.json').read_text())
        self.assertIs(template['auto_apply'], False)
        self.assertIsNone(template['hostname'])
        self.assertIsNone(template['cloudflare_zone_name'])
        validated = cfg.validate_mapping(template)
        self.assertFalse(validated['auto_apply'])
        cfg.validate_mapping(json.loads((ROOT / 'onboarding.config.json').read_text()))

    def test_rejects_unknown_keys_types_reserved_and_symlinks(self):
        with self.assertRaisesRegex(cfg.ConfigError, 'config_unknown_key'):
            cfg.validate_mapping({
                **json.loads((self.source / 'onboarding.config.json').read_text()),
                'extra': True,
            })
        with self.assertRaisesRegex(cfg.ConfigError, 'config_auto_apply_not_bool'):
            cfg.validate_mapping({
                'schema_version': 1, 'hostname': None, 'cloudflare_zone_name': None,
                'auto_apply': 1, 'expected_project_id': None, 'confirm_repository_id': None,
            })
        with self.assertRaisesRegex(cfg.ConfigError, 'config_duplicate_key'):
            cfg.parse_mapping('{"schema_version":1,"schema_version":1,"hostname":null,'
                              '"cloudflare_zone_name":null,"auto_apply":false,'
                              '"expected_project_id":null,"confirm_repository_id":null}')
        for host in ('example.com', 'www.example.com', 'mail.example.com', 'a.b.example.com'):
            with self.assertRaises(cfg.ConfigError):
                cfg.validate_hostname_under_zone(host, 'example.com')
        write_config(self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
                     auto_apply=True)
        git_commit(self.source, 'enabled')
        target = self.source / 'onboarding.config.json'
        link = self.source / 'link-config.json'
        # Symlink at the required path is forbidden.
        target.unlink()
        os.symlink('link-config.json', target)
        link.write_text('{}')
        with self.assertRaisesRegex(cfg.ConfigError, 'config_symlink_forbidden'):
            cfg.config_path(self.source)

    def test_tracked_clean_head_binding(self):
        write_config(self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
                     auto_apply=True)
        sha = git_commit(self.source, 'enabled')
        loaded = cfg.load_onboarding_config(self.source, expected_sha=sha)
        self.assertTrue(loaded['auto_apply'])
        (self.source / 'onboarding.config.json').write_text('{"tampered":true}\n')
        with self.assertRaisesRegex(cfg.ConfigError, 'config_source_dirty|config_working_tree'):
            cfg.load_onboarding_config(self.source, expected_sha=sha)

    def test_hostname_required_for_auto_apply(self):
        with self.assertRaisesRegex(cfg.ConfigError, 'hostname_required_for_auto_apply'):
            cfg.validate_mapping({
                'schema_version': 1, 'hostname': None, 'cloudflare_zone_name': None,
                'auto_apply': True, 'expected_project_id': None, 'confirm_repository_id': None,
            })
        with self.assertRaisesRegex(cfg.ConfigError, 'recovery_pair_required'):
            cfg.validate_mapping({
                'schema_version': 1, 'hostname': 'app.example.com',
                'cloudflare_zone_name': 'example.com', 'auto_apply': True,
                'expected_project_id': 'prj_X', 'confirm_repository_id': None,
            })


class MergeOnboardingOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_config(self.source)
        (self.source / 'app.txt').write_text('app')
        self.sha = git_commit(self.source, 'seed')
        self.artifacts = self.base / 'artifacts'
        self.artifacts.mkdir()
        self.binary = self.base / 'vercel'
        self.binary.write_text('#!/bin/true\n')
        self.binary.chmod(0o755)
        self.env = {
            'GITHUB_REPOSITORY': 'Owner/Example-App',
            'GITHUB_REPOSITORY_ID': '2000000001',
            'GITHUB_SHA': self.sha,
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'push',
            'GITHUB_TOKEN': 'offline-github',
            'VERCEL_API_TOKEN': 'offline-token',
            'VERCEL_TEAM_ID': 'team_Offline',
            'SUPABASE_URL': 'https://offline.supabase.co',
            'SUPABASE_ANON_KEY': 'sb_publishable_offline',
            'CLOUDFLARE_API_TOKEN': 'cf_offline_token',
        }
        self.patches = [
            patch.dict(os.environ, self.env, clear=False),
            patch.object(orch, 'github', side_effect=self.github),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def head_repository(self):
        return {
            'id': int(os.environ['GITHUB_REPOSITORY_ID']),
            'full_name': os.environ['GITHUB_REPOSITORY'],
        }

    def quality_run(self, **overrides):
        base = {
            'id': 100,
            'run_attempt': 1,
            'workflow_id': 395268222,
            'head_sha': self.sha,
            'path': '.github/workflows/app-quality.yml',
            'event': 'push',
            'head_branch': 'main',
            'status': 'completed',
            'conclusion': 'success',
            'head_repository': self.head_repository(),
        }
        base.update(overrides)
        return base

    def github(self, path, token=None):
        if path.startswith('/repositories/'):
            return {
                'id': int(os.environ['GITHUB_REPOSITORY_ID']),
                'full_name': os.environ['GITHUB_REPOSITORY'],
                'is_template': False, 'fork': False, 'default_branch': 'main',
            }
        if '/commits/' in path:
            return {'sha': self.sha}
        if '/actions/workflows/' in path:
            return {'workflow_runs': self.quality_runs}
        raise AssertionError(path)

    def enable_config(self, **extra):
        write_config(
            self.source,
            hostname='app.example.com',
            cloudflare_zone_name='example.com',
            auto_apply=True,
            **extra,
        )
        self.sha = git_commit(self.source, 'enable')
        os.environ['GITHUB_SHA'] = self.sha

    def run_orch(self, quality_wait=None):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = orch.run(
                self.source, str(self.binary), self.artifacts, quality_wait=quality_wait)
        lines = []
        for line in buf.getvalue().splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                pass
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        return code, lines, result

    def test_disabled_default_no_writes(self):
        self.quality_runs = []
        code, _lines, result = self.run_orch(quality_wait=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError('quality must not run when disabled')))
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'onboarding_auto_apply_disabled')
        self.assertFalse(result['full_app_ready'])
        self.assertEqual(result['database']['status'], 'database_stages_unimplemented')

    def test_missing_secrets_setup_required(self):
        self.enable_config()
        os.environ['VERCEL_API_TOKEN'] = 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
        code, _lines, result = self.run_orch(
            quality_wait=lambda *a, **k: (_ for _ in ()).throw(AssertionError('no quality')))
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'setup_required')
        self.assertIn('VERCEL_API_TOKEN', result['required_secret_names'])

    def test_event_branch_upstream_fork_template_guards(self):
        self.enable_config()
        for key, value, status in (
            ('GITHUB_EVENT_NAME', 'workflow_dispatch', 'event_not_authorized'),
            ('GITHUB_REF', 'refs/heads/feature', 'event_not_authorized'),
            ('GITHUB_REPOSITORY_ID', orch.UPSTREAM_ID, 'template_or_repository_invalid'),
        ):
            old = os.environ[key]
            os.environ[key] = value
            code, _l, result = self.run_orch(
                quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'})
            self.assertEqual(code, 1, status)
            self.assertEqual(result['status'], status)
            os.environ[key] = old

        def forked(path, token=None):
            if path.startswith('/repositories/'):
                return {
                    'id': 2000000001, 'full_name': 'Owner/Example-App',
                    'is_template': False, 'fork': True, 'default_branch': 'main',
                }
            return self.github(path, token)
        with patch.object(orch, 'github', side_effect=forked):
            code, _l, result = self.run_orch(
                quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'})
            self.assertEqual(result['status'], 'repository_identity_mismatch')

    def _wait(self, **kwargs):
        clock_state = {'t': 0.0}

        def clock():
            return clock_state['t']

        def sleeper(sec):
            clock_state['t'] += float(sec)

        defaults = dict(
            timeout_sec=0.05, poll_sec=0.02, sleeper=sleeper, clock=clock)
        defaults.update(kwargs)
        return orch.wait_for_quality_gate(
            'Owner/Example-App', self.sha, 'main',
            os.environ['GITHUB_REPOSITORY_ID'], os.environ['GITHUB_REPOSITORY'],
            **defaults)

    def test_quality_gate_success_failure_stale_timeout(self):
        self.enable_config()
        self.quality_runs = [self.quality_run(conclusion='failure')]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_failed'):
            self._wait()
        self.quality_runs = [self.quality_run(head_sha='0' * 40)]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_stale'):
            self._wait()
        self.quality_runs = [self.quality_run(status='in_progress', conclusion=None)]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_timeout'):
            self._wait()
        self.quality_runs = [self.quality_run()]
        passed = self._wait(timeout_sec=1)
        self.assertEqual(passed['status'], 'quality_gate_passed')
        for key in (
            'workflow_id', 'run_id', 'run_attempt', 'event', 'head_sha',
            'head_branch', 'repository_id', 'repository_full_name', 'path',
        ):
            self.assertIn(key, passed)
        self.assertEqual(passed['path'], '.github/workflows/app-quality.yml')
        self.assertEqual(passed['selection_policy'], 'newest_matching_run_id_then_run_attempt')

    def test_quality_newest_failed_or_pending_overrides_older_success(self):
        self.enable_config()
        older_success = self.quality_run(id=10, run_attempt=1, conclusion='success')
        newer_failed = self.quality_run(id=20, run_attempt=1, conclusion='failure')
        # Shuffled API order must not matter.
        self.quality_runs = [older_success, newer_failed]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_failed'):
            self._wait()
        self.quality_runs = [newer_failed, older_success]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_failed'):
            self._wait()
        newer_pending = self.quality_run(id=30, run_attempt=2, status='in_progress', conclusion=None)
        self.quality_runs = [newer_pending, older_success, newer_failed]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_timeout'):
            self._wait()
        # Higher run_attempt on same id wins over older success attempt.
        self.quality_runs = [
            self.quality_run(id=40, run_attempt=1, conclusion='success'),
            self.quality_run(id=40, run_attempt=2, conclusion='failure'),
        ]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_failed'):
            self._wait()
        # Newest success among shuffled list is selected.
        self.quality_runs = [
            self.quality_run(id=5, conclusion='success'),
            self.quality_run(id=50, conclusion='success', event='workflow_dispatch'),
            self.quality_run(id=7, conclusion='failure'),
        ]
        passed = self._wait(timeout_sec=1)
        self.assertEqual(passed['run_id'], 50)
        self.assertEqual(passed['event'], 'workflow_dispatch')

    def test_quality_rejects_wrong_path_branch_repo_and_malformed_ids(self):
        self.enable_config()
        self.quality_runs = [self.quality_run(path='app-quality.yml')]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_invalid|quality_gate_missing'):
            self._wait()
        self.quality_runs = [self.quality_run(head_branch='feature')]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_invalid|quality_gate_missing'):
            self._wait()
        # workflow_dispatch on a non-default branch is rejected.
        self.quality_runs = [self.quality_run(
            event='workflow_dispatch', head_branch='hotfix')]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_invalid|quality_gate_missing'):
            self._wait()
        self.quality_runs = [self.quality_run(head_repository={
            'id': 999, 'full_name': 'Other/Repo'})]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_invalid|quality_gate_missing'):
            self._wait()
        self.quality_runs = [self.quality_run(head_repository={
            'id': int(os.environ['GITHUB_REPOSITORY_ID']), 'full_name': 'Wrong/Name'})]
        with self.assertRaisesRegex(orch.Failure, 'quality_gate_invalid|quality_gate_missing'):
            self._wait()
        for bad in (
            self.quality_run(id=0),
            self.quality_run(id=True),
            self.quality_run(run_attempt=-1),
            self.quality_run(workflow_id='395268222'),
            self.quality_run(path=None),
        ):
            self.quality_runs = [bad]
            with self.assertRaisesRegex(orch.Failure, 'quality_gate_invalid|quality_gate_missing'):
                self._wait()

    def test_db_stage_incompleteness_always_reported(self):
        self.enable_config()
        code, _l, result = self.run_orch(
            quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'})
        # Launch/domain stubbed poorly → failure path still includes DB gap.
        self.assertFalse(result['full_app_ready'])
        self.assertEqual(result['database']['status'], 'database_stages_unimplemented')
        self.assertIn('app_scoped_schema_migrations', result['database']['required_missing_stages'])
        self.assertNotEqual(result['status'], 'full_app_ready')
        artifact = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertFalse(artifact['full_app_ready'])

    def test_workflow_push_guards_and_shared_concurrency(self):
        wf = (ROOT / '.github/workflows/merge-onboarding.yml').read_text()
        # A skipped non-default push must not enter workflow-level concurrency
        # and cancel the one pending main deployment.
        self.assertNotIn('\nconcurrency:\n', wf)
        self.assertIn('\n    concurrency:\n', wf)
        self.assertIn('push:', wf)
        self.assertNotIn('pull_request_target', wf)
        self.assertNotIn('workflow_run:', wf)
        self.assertIn('app-provider-${{ github.repository_id }}', wf)
        self.assertIn("github.repository_id != '1051601680'", wf)
        self.assertIn('actions: read', wf)
        self.assertIn('if: always()', wf)
        self.assertIn('merge-onboarding.py', wf)
        launch_wf = (ROOT / '.github/workflows/launch-app.yml').read_text()
        on_block = launch_wf.split('jobs:', 1)[0]
        self.assertNotRegex(on_block, r'(?m)^\s+push:\s*$')
        self.assertIn('workflow_dispatch', on_block)

    def test_docs_describe_merge_not_mandatory_dispatch(self):
        for name in ('README.md', 'AGENTS.md', 'docs/secrets-only-launch.md',
                     'docs/new-app-runbook.md', 'docs/end-to-end-onboarding.md'):
            text = (ROOT / name).read_text().lower()
            self.assertIn('onboarding.config.json', text)
            self.assertIn('auto_apply', text)
            self.assertIn('merge', text)
        agents = (ROOT / 'AGENTS.md').read_text().lower()
        self.assertIn('implementation gap', agents)
        self.assertIn('database', agents)


class LaunchMergePolicyTests(unittest.TestCase):
    """Push create/recovery require merge policy; manual dispatch preserved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_config(self.source)
        (self.source / 'app.txt').write_text('independent application')
        self.sha = git_commit(self.source, 'seed')
        self.binary = self.base / 'vercel'
        self.binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(__file__).with_suffix('.json')
s = json.loads(p.read_text())
a = sys.argv[1:]
assert os.environ.get('VERCEL_TOKEN') == 'offline-token'
value = sys.stdin.read()
s['calls'].append([a, value])
if a[:2] == ['api', '/v10/projects']:
    body = json.loads(value)
    s['project'] = {'id': 'prj_Offline', 'name': body['name'], 'accountId': 'team_Offline'}
    print(json.dumps(s['project']))
elif a[:2] == ['project', 'inspect']:
    print(json.dumps({'id': s['project']['id'], 'name': s['project']['name']}))
elif a[:2] == ['env', 'add']:
    key = a[2]
    s['envs'] = [e for e in s['envs'] if e['key'] != key]
    s['envs'].append({'key': key, 'value': 'opaque', 'target': ['production'],
                      'type': 'encrypted', 'visibility': 'config'})
elif a[0] == 'deploy':
    metas = dict(x.split('=', 1) for i, x in enumerate(a) if x == '--meta' for x in [a[i+1]])
    sha = a[a.index('--env') + 1].split('=', 1)[1]
    s['remote'] = {'id': 'dpl_Offline', 'url': 'offline.vercel.app', 'projectId': 'prj_Offline',
                   'target': 'production', 'readyState': 'READY', 'meta': metas,
                   'alias': ['production.vercel.app']}
    s['project']['targets'] = {'production': {'id': 'dpl_Offline'}}
    print(json.dumps({'id': 'dpl_Offline', 'url': 'offline.vercel.app', 'target': 'production',
                      'readyState': 'READY'}))
else:
    raise AssertionError(a)
p.write_text(json.dumps(s))
''')
        self.binary.chmod(0o755)
        self.statefile = self.binary.with_suffix('.json')
        self.statefile.write_text(json.dumps({'project': None, 'envs': [], 'calls': []}))
        self.env = {
            'GITHUB_REPOSITORY': 'Owner/Example-App',
            'GITHUB_REPOSITORY_ID': '2000000001',
            'GITHUB_SHA': self.sha,
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'VERCEL_API_TOKEN': 'offline-token',
            'VERCEL_TEAM_ID': 'team_Offline',
            'SUPABASE_URL': 'https://offline.supabase.co',
            'SUPABASE_ANON_KEY': 'sb_publishable_offline',
            'GITHUB_TOKEN': 'offline-github',
        }
        self.vercel = launch.load('check-vercel-access')
        self.supabase = launch.load('check-supabase-access')
        self.patches = [
            patch.dict(os.environ, self.env),
            patch.object(launch, 'github', side_effect=self.github),
            patch.object(launch, 'load', side_effect=self.load),
            patch.object(self.vercel, 'items', side_effect=self.items),
            patch.object(self.vercel, 'get_json', side_effect=self.read),
            patch.object(self.supabase, 'check', return_value='passed'),
            patch.object(launch, 'get_page', side_effect=self.page),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def load(self, name):
        if name == 'check-vercel-access':
            return self.vercel
        if name == 'check-supabase-access':
            return self.supabase
        if name == 'onboarding-config':
            return cfg
        return launch.load(name)

    def github(self, path):
        if path.startswith('/repositories/'):
            return {
                'id': int(os.environ['GITHUB_REPOSITORY_ID']),
                'full_name': os.environ['GITHUB_REPOSITORY'],
                'is_template': False, 'fork': False, 'default_branch': 'main',
            }
        return {'sha': self.sha}

    def items(self, route, key, token, query=None):
        state = json.loads(self.statefile.read_text())
        return iter([{'id': 'team_Offline'}] if key == 'teams' else
                    ([state['project']] if state['project'] else []))

    def read(self, path, token):
        state = json.loads(self.statefile.read_text())
        if '/env?' in path:
            return {'envs': state['envs']}
        if path.startswith('/v9/projects/'):
            return state['project']
        if path.startswith('/v13/deployments/'):
            return state['remote']
        raise AssertionError(path)

    def page(self, url):
        if url.endswith('/api/health'):
            return (200, json.dumps({
                'status': 'ready', 'service': 'app', 'revision': self.sha}).encode())
        return (200, b'<!doctype html><html>Shell</html>')

    def run_app(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return launch.run(self.source, str(self.binary), **kw)

    def test_push_without_policy_rejected(self):
        os.environ['GITHUB_EVENT_NAME'] = 'push'
        with self.assertRaisesRegex(launch.Failure, 'event_not_authorized'):
            self.run_app()

    def test_push_with_policy_creates_and_idempotent_rerun(self):
        write_config(self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
                     auto_apply=True)
        self.sha = git_commit(self.source, 'enable')
        os.environ['GITHUB_SHA'] = self.sha
        os.environ['GITHUB_EVENT_NAME'] = 'push'
        os.environ['ONBOARDING_MERGE_POLICY'] = '1'
        self.assertEqual(self.run_app(), 0)
        state = json.loads(self.statefile.read_text())
        self.assertEqual(state['project']['name'], 'example-app')
        deploys = sum(1 for c in state['calls'] if c[0][0] == 'deploy')
        self.assertEqual(self.run_app(), 0)
        state = json.loads(self.statefile.read_text())
        self.assertEqual(sum(1 for c in state['calls'] if c[0][0] == 'deploy'), deploys)

    def test_push_recovery_exact_ids_only(self):
        write_config(
            self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
            auto_apply=True, expected_project_id='prj_Offline',
            confirm_repository_id='2000000001')
        self.sha = git_commit(self.source, 'recover')
        os.environ['GITHUB_SHA'] = self.sha
        os.environ['GITHUB_EVENT_NAME'] = 'push'
        os.environ['ONBOARDING_MERGE_POLICY'] = '1'
        self.statefile.write_text(json.dumps({
            'project': {'id': 'prj_Offline', 'name': 'example-app', 'accountId': 'team_Offline'},
            'envs': [], 'calls': [],
        }))
        self.assertEqual(self.run_app(), 0)
        write_config(
            self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
            auto_apply=True, expected_project_id='prj_Wrong',
            confirm_repository_id='2000000001')
        self.sha = git_commit(self.source, 'bad-recover')
        os.environ['GITHUB_SHA'] = self.sha
        self.statefile.write_text(json.dumps({
            'project': {'id': 'prj_Offline', 'name': 'example-app', 'accountId': 'team_Offline'},
            'envs': [], 'calls': [],
        }))
        with self.assertRaisesRegex(launch.Failure, 'expected_project_mismatch'):
            self.run_app()

    def test_manual_dispatch_preserved(self):
        os.environ['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
        os.environ.pop('ONBOARDING_MERGE_POLICY', None)
        self.assertEqual(self.run_app(), 0)


class CustomDomainMergePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_config(self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
                     auto_apply=True)
        (self.source / 'app.txt').write_text('app')
        self.sha = git_commit(self.source, 'seed')
        self.artifacts = self.base / 'artifacts'
        self.artifacts.mkdir()
        self.hostname = 'app.example.com'
        self.zone = 'example.com'
        self.cname = 'cname.vercel-dns.example.com'
        # Merge mode requires app deployment SHA == quality-approved automation SHA.
        self.app_sha = self.sha
        self.binary = self.base / 'vercel'
        self.binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(__file__).with_suffix('.json')
s = json.loads(p.read_text())
a = sys.argv[1:]
value = sys.stdin.read()
s['calls'].append([a, value])
if a[0] == 'api' and a[1].endswith('/domains') and '-X' in a:
    body = json.loads(value)
    s['project_domain'] = {
        'name': body['name'], 'projectId': s['project']['id'], 'verified': True,
        'gitBranch': None, 'customEnvironmentId': None, 'redirect': None,
        'redirectStatusCode': None, 'verification': [],
    }
    print(json.dumps(s['project_domain']))
elif a[0] == 'api' and '/verify' in a[1]:
    s['project_domain']['verified'] = True
    print(json.dumps(s['project_domain']))
else:
    raise AssertionError(a)
p.write_text(json.dumps(s))
''')
        self.binary.chmod(0o755)
        self.statefile = self.binary.with_suffix('.json')
        self.save({
            'calls': [],
            'project': {
                'id': 'prj_Offline', 'name': 'example-app', 'accountId': 'team_Offline',
                'targets': {'production': {'id': 'dpl_Offline'}},
            },
            'envs': [{
                'key': 'TEMPLATE_REPOSITORY_ID_2000000001', 'target': ['production'],
                'type': 'encrypted', 'visibility': 'config',
            }],
            'remote': {
                'id': 'dpl_Offline', 'url': 'offline.vercel.app', 'projectId': 'prj_Offline',
                'target': 'production', 'readyState': 'READY',
                'meta': {'githubCommitSha': self.app_sha, 'githubRepoId': '2000000001'},
            },
            'project_domain': None,
            'domains_by_project': {'prj_Offline': []},
            'domain_config': {
                'recommendedCNAME': [{'rank': 1, 'value': self.cname}],
            },
            'zones': [{
                'id': 'zone_Offline', 'name': self.zone, 'status': 'active',
                'account': {'id': 'acct_Offline'},
                'name_servers': ['ns1.example-ns.com', 'ns2.example-ns.com'],
            }],
            'dns': [],
            'cf_writes': [],
        })
        self.env = {
            'GITHUB_REPOSITORY': 'Owner/Example-App',
            'GITHUB_REPOSITORY_ID': '2000000001',
            'GITHUB_SHA': self.sha,
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'push',
            'ONBOARDING_MERGE_POLICY': '1',
            'VERCEL_API_TOKEN': 'offline-token',
            'VERCEL_TEAM_ID': 'team_Offline',
            'CLOUDFLARE_API_TOKEN': 'cf_offline_token',
            'APP_HOSTNAME': self.hostname,
            'CLOUDFLARE_ZONE_NAME': self.zone,
            'GITHUB_TOKEN': 'offline-github',
        }
        self.vercel = domain.load('check-vercel-access')
        self.patches = [
            patch.dict(os.environ, self.env),
            patch.object(domain, 'github', side_effect=self.github),
            patch.object(domain, 'load', side_effect=self.load),
            patch.object(self.vercel, 'get_json', side_effect=self.read),
            patch.object(domain, 'cf_request', side_effect=self.cf_request),
            patch.object(domain, 'vercel_items', side_effect=self.vercel_items),
            patch.object(domain, 'time'),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def load(self, name):
        if name == 'check-vercel-access':
            return self.vercel
        if name == 'onboarding-config':
            return cfg
        return domain.load(name)

    def save(self, state):
        self.statefile.write_text(json.dumps(state))

    def state(self):
        return json.loads(self.statefile.read_text())

    def github(self, path):
        if path.startswith('/repositories/'):
            return {
                'id': 2000000001, 'full_name': 'Owner/Example-App',
                'is_template': False, 'fork': False, 'default_branch': 'main',
            }
        if '/commits/' in path:
            tip = path.rsplit('/', 1)[-1]
            if tip in (self.app_sha, self.sha, 'main'):
                return {'sha': self.sha if tip == 'main' else tip}
            return {'sha': tip}
        raise AssertionError(path)

    def vercel_items(self, vercel, route, key, token, query=None):
        s = self.state()
        if key == 'teams':
            return [{'id': 'team_Offline'}]
        if key == 'projects':
            return [s['project']]
        if key == 'domains':
            domain_obj = s.get('project_domain')
            return [domain_obj] if domain_obj else []
        raise AssertionError((route, key))

    def read(self, path, token):
        s = self.state()
        if '/env?' in path:
            return {'envs': s['envs']}
        if path.startswith('/v9/projects/'):
            return s['project']
        if path.startswith('/v13/deployments/'):
            return s['remote']
        if '/config?' in path:
            return s['domain_config']
        raise AssertionError(path)

    def cf_request(self, method, path, token, payload=None, query=None):
        s = self.state()
        if method == 'GET' and path == '/zones':
            return {
                'success': True,
                'result': s['zones'],
                'result_info': {'total_pages': 1, 'page': 1},
            }
        if method == 'GET' and path.endswith('/dns_records'):
            name = (query or {}).get('name')
            records = [r for r in s['dns'] if name is None or r.get('name') == name]
            return {
                'success': True, 'result': records,
                'result_info': {'total_pages': 1, 'page': 1},
            }
        if method == 'POST' and path.endswith('/dns_records'):
            created = {
                'id': 'dns_' + str(len(s['cf_writes']) + 1),
                'type': payload['type'], 'name': payload['name'],
                'content': payload['content'], 'proxied': payload.get('proxied', False),
                'ttl': payload.get('ttl'),
            }
            s['dns'].append(created)
            s['cf_writes'].append(payload)
            self.save(s)
            return {'success': True, 'result': created}
        raise AssertionError((method, path))

    def run_op(self, operation, mode='merge_policy', expected_project_id='prj_Offline',
               expected_deployment_id='dpl_Offline'):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = domain.run(
                self.source, str(self.binary), operation, '', str(self.artifacts), mode,
                expected_project_id=expected_project_id,
                expected_deployment_id=expected_deployment_id)
        lines = []
        for line in buf.getvalue().splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                pass
        return code, lines

    def test_push_rejected_without_merge_mode(self):
        code, lines = self.run_op('plan', mode='manual_digest',
                                  expected_project_id=None, expected_deployment_id=None)
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'event_not_authorized')

    def test_merge_apply_creates_cname_idempotent(self):
        code, lines = self.run_op('apply')
        self.assertEqual(code, 0)
        self.assertIn(lines[-1]['status'], ('apply_complete', 'already_configured'))
        writes = self.state()['cf_writes']
        self.assertEqual(len([w for w in writes if w.get('type') == 'CNAME']), 1)
        self.assertEqual(writes[0]['content'], self.cname)
        self.assertEqual(writes[0]['ttl'], 300)
        self.assertIs(writes[0]['proxied'], False)
        code, lines = self.run_op('apply')
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1]['status'], 'already_configured')
        self.assertEqual(len([w for w in self.state()['cf_writes'] if w.get('type') == 'CNAME']), 1)
        self.assertEqual(lines[-1]['app_deployment_sha'], self.sha)

    def test_merge_rejects_app_sha_different_from_automation(self):
        s = self.state()
        s['remote']['meta']['githubCommitSha'] = 'c' * 40
        self.save(s)
        code, lines = self.run_op('apply')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'merge_app_deployment_sha_mismatch')
        self.assertEqual(self.state()['cf_writes'], [])

    def test_deployment_a_to_b_drift_before_apply(self):
        code, lines = self.run_op('plan')
        self.assertEqual(code, 0)
        s = self.state()
        s['remote'] = {
            'id': 'dpl_RolledBack', 'url': 'offline.vercel.app', 'projectId': 'prj_Offline',
            'target': 'production', 'readyState': 'READY',
            'meta': {'githubCommitSha': self.sha, 'githubRepoId': '2000000001'},
        }
        s['project']['targets'] = {'production': {'id': 'dpl_RolledBack'}}
        self.save(s)
        code, lines = self.run_op('apply', expected_deployment_id='dpl_Offline')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'expected_deployment_mismatch')
        self.assertEqual(self.state()['cf_writes'], [])

    def test_deployment_a_to_b_drift_before_verify(self):
        code, lines = self.run_op('apply')
        self.assertEqual(code, 0)
        s = self.state()
        s['remote'] = {
            'id': 'dpl_Later', 'url': 'offline.vercel.app', 'projectId': 'prj_Offline',
            'target': 'production', 'readyState': 'READY',
            'meta': {'githubCommitSha': self.sha, 'githubRepoId': '2000000001'},
        }
        s['project']['targets'] = {'production': {'id': 'dpl_Later'}}
        self.save(s)
        code, lines = self.run_op('verify', expected_deployment_id='dpl_Offline')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'expected_deployment_mismatch')

    def test_cname_recommendation_change_replans_within_policy(self):
        code, lines = self.run_op('apply')
        self.assertEqual(code, 0)
        new_cname = 'rotated.vercel-dns.example.com'
        s = self.state()
        s['domain_config'] = {'recommendedCNAME': [{'rank': 1, 'value': new_cname}]}
        # Keep existing CNAME so plan sees conflict unless we clear it for replan target.
        s['dns'] = [r for r in s['dns'] if r.get('type') != 'CNAME']
        s['cf_writes'] = []
        self.save(s)
        code, lines = self.run_op('apply')
        self.assertEqual(code, 0)
        self.assertIn(lines[-1]['status'], ('apply_complete', 'already_configured'))
        cnames = [w for w in self.state()['cf_writes'] if w.get('type') == 'CNAME']
        self.assertEqual(len(cnames), 1)
        self.assertEqual(cnames[0]['content'], new_cname)

    def test_out_of_policy_txt_refused(self):
        s = self.state()
        s['project_domain'] = {
            'name': self.hostname, 'projectId': 'prj_Offline', 'verified': False,
            'gitBranch': None, 'customEnvironmentId': None, 'redirect': None,
            'redirectStatusCode': None,
            'verification': [{
                'type': 'TXT', 'domain': 'out.example.com',
                'value': 'vc-domain-verify=x', 'reason': 'pending',
            }],
        }
        self.save(s)
        code, lines = self.run_op('apply')
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'policy_extension_required')
        self.assertEqual(self.state()['cf_writes'], [])

    def test_manual_utilities_still_digest_gated(self):
        os.environ['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
        os.environ.pop('ONBOARDING_MERGE_POLICY', None)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = domain.run(
                self.source, str(self.binary), 'apply', '', str(self.artifacts), 'manual_digest')
        lines = [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]['status'], 'approved_plan_digest_required')


class OrchestratorShellAndTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_config(self.source, hostname='app.example.com', cloudflare_zone_name='example.com',
                     auto_apply=True)
        (self.source / 'app.txt').write_text('app')
        self.sha = git_commit(self.source, 'enable')
        self.artifacts = self.base / 'artifacts'
        self.artifacts.mkdir()
        self.binary = self.base / 'vercel'
        self.binary.write_text('#!/bin/true\n')
        self.binary.chmod(0o755)
        self.env = {
            'GITHUB_REPOSITORY': 'Owner/Example-App',
            'GITHUB_REPOSITORY_ID': '2000000001',
            'GITHUB_SHA': self.sha,
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'push',
            'GITHUB_TOKEN': 'offline-github',
            'VERCEL_API_TOKEN': 'offline-token-secret-value',
            'VERCEL_TEAM_ID': 'team_Offline',
            'SUPABASE_URL': 'https://offline.supabase.co',
            'SUPABASE_ANON_KEY': 'sb_publishable_offline',
            'CLOUDFLARE_API_TOKEN': 'cf_offline_token_secret',
        }
        self.patches = [
            patch.dict(os.environ, self.env, clear=False),
            patch.object(orch, 'github', side_effect=self.github),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def github(self, path, token=None):
        if path.startswith('/repositories/'):
            return {
                'id': 2000000001, 'full_name': 'Owner/Example-App',
                'is_template': False, 'fork': False, 'default_branch': 'main',
            }
        if '/commits/' in path:
            return {'sha': self.sha}
        raise AssertionError(path)

    def test_protected_shell_continues_when_binding_valid(self):
        events = [
            {'status': 'plan', 'digest': 'x'},
            {
                'status': 'ready_but_verification_protected',
                'project_id': 'prj_Offline',
                'deployment_id': 'dpl_Offline',
                'source_sha': self.sha,
            },
        ]
        domain_events = [{'status': 'already_configured', 'project_id': 'prj_Offline',
                          'app_deployment_sha': self.sha}]
        verify_events = [{'status': 'ready_but_verification_protected',
                          'project_id': 'prj_Offline', 'app_deployment_sha': self.sha}]
        calls = {'n': 0}

        def fake_script(script, source, binary, extra_args, env_updates, artifact_dir, **kw):
            calls['n'] += 1
            if script == 'launch-app.py':
                return 1, events, '', ''
            if script == 'custom-domain.py' and '--operation' in extra_args:
                op = extra_args[extra_args.index('--operation') + 1]
                if op == 'apply':
                    self.assertIn('--expected-project-id', extra_args)
                    self.assertIn('prj_Offline', extra_args)
                    self.assertIn('dpl_Offline', extra_args)
                    return 0, domain_events, '', ''
                if op == 'verify':
                    return 1, verify_events, '', ''
            raise AssertionError(script)

        with patch.object(orch, 'run_python_script', side_effect=fake_script):
            with contextlib.redirect_stdout(io.StringIO()):
                code = orch.run(
                    self.source, str(self.binary), self.artifacts,
                    quality_wait=lambda *a, **k: {
                        'status': 'quality_gate_passed', 'head_sha': self.sha,
                        'workflow_id': 1, 'run_id': 2, 'run_attempt': 1, 'event': 'push',
                        'head_branch': 'main', 'repository_id': '2000000001',
                        'repository_full_name': 'Owner/Example-App',
                        'path': '.github/workflows/app-quality.yml',
                        'selection_policy': 'newest_matching_run_id_then_run_attempt',
                    },
                    sleeper=lambda _s: None, clock=lambda: 0.0,
                    verify_window_sec=60, orchestration_lifetime_sec=120)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'infrastructure_dns_protected_database_incomplete')
        self.assertEqual(result['stages']['shell_binding']['deployment_id'], 'dpl_Offline')
        self.assertGreaterEqual(calls['n'], 3)

    def test_shell_nonzero_without_allowed_status_aborts(self):
        def fake_script(script, *a, **kw):
            if script == 'launch-app.py':
                return 1, [{'status': 'deployment_readback_mismatch'}], '', ''
            raise AssertionError('domain must not run')

        with patch.object(orch, 'run_python_script', side_effect=fake_script):
            with contextlib.redirect_stdout(io.StringIO()):
                code = orch.run(
                    self.source, str(self.binary), self.artifacts,
                    quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'},
                    sleeper=lambda _s: None, clock=lambda: 0.0)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'shell_deploy_failed')

    def test_timeout_preserves_sanitized_recovery_handle(self):
        secret = os.environ['VERCEL_API_TOKEN']
        stdout = json.dumps({
            'status': 'project_created_recovery_handle', 'project_id': 'prj_Partial',
        }) + '\n'
        stderr = f'cli exploded token={secret}\n'

        class FakeTimeout(subprocess.TimeoutExpired):
            def __init__(self):
                super().__init__(cmd=['x'], timeout=1, output=stdout.encode(), stderr=stderr.encode())

        def boom(*a, **kw):
            raise FakeTimeout()

        with patch.object(subprocess, 'run', side_effect=boom):
            with self.assertRaisesRegex(orch.Failure, 'stage_timeout'):
                orch.run_python_script(
                    'launch-app.py', self.source, str(self.binary), ['--operation', 'deploy'],
                    {}, self.artifacts, timeout=1, capture_name='launch-timeout.json',
                    stage='shell_deploy')
        payload = json.loads((self.artifacts / 'launch-timeout.json').read_text())
        self.assertTrue(payload['timed_out'])
        self.assertTrue(payload['stage_timeout'])
        self.assertEqual(payload['events'][0]['project_id'], 'prj_Partial')
        blob = json.dumps(payload)
        self.assertNotIn(secret, blob)
        self.assertIn('[redacted]', payload['stderr'])

    def test_verify_window_uses_injected_clock_without_sleeping(self):
        sleeps = []
        clock_state = {'t': 0.0}

        def clock():
            return clock_state['t']

        def sleeper(sec):
            sleeps.append(sec)
            clock_state['t'] += sec

        shell = [{
            'status': 'page_and_health_verified', 'project_id': 'prj_Offline',
            'deployment_id': 'dpl_Offline', 'source_sha': self.sha,
        }]
        apply_lines = [{'status': 'already_configured', 'project_id': 'prj_Offline'}]
        pending = [{
            'status': 'verification_pending', 'pending_checks': ['tls'],
            'project_id': 'prj_Offline',
        }]

        def fake_script(script, source, binary, extra_args, env_updates, artifact_dir, **kw):
            if script == 'launch-app.py':
                return 0, shell, '', ''
            op = extra_args[extra_args.index('--operation') + 1]
            if op == 'apply':
                return 0, apply_lines, '', ''
            return 1, pending, '', ''

        with patch.object(orch, 'run_python_script', side_effect=fake_script):
            with contextlib.redirect_stdout(io.StringIO()):
                code = orch.run(
                    self.source, str(self.binary), self.artifacts,
                    quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'},
                    sleeper=sleeper, clock=clock,
                    verify_window_sec=45, orchestration_lifetime_sec=120)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'hostname_dns_verification_pending')
        self.assertTrue(result.get('verify_window_exhausted'))
        self.assertGreaterEqual(len(sleeps), 1)
        self.assertTrue(all(s == orch.VERIFY_POLL_SEC for s in sleeps))

    def test_app_quality_push_covers_arbitrary_default_branch(self):
        quality = (ROOT / '.github/workflows/app-quality.yml').read_text()
        on_block = quality.split('jobs:', 1)[0]
        self.assertIn("branches: ['**']", on_block)
        self.assertIn('workflow_dispatch', on_block)
        self.assertNotRegex(on_block, r'(?m)^\s*pull_request_target\s*:')
        self.assertNotIn('workflow_run:', on_block)
        self.assertIn('github.event.repository.default_branch', quality)
        merge_wf = (ROOT / '.github/workflows/merge-onboarding.yml').read_text()
        self.assertIn('timeout-minutes: 60', merge_wf)

    def test_docs_database_target_from_secrets_not_config_field(self):
        agents = (ROOT / 'AGENTS.md').read_text()
        self.assertIn('Supabase Secrets', agents)
        self.assertIn('not a nonexistent onboarding.config database field', agents)
        e2e = (ROOT / 'docs/end-to-end-onboarding.md').read_text()
        self.assertIn('not from an `onboarding.config.json` database field', e2e)
        self.assertIn('via Secrets', e2e)

    def test_disabled_delivery_keeps_normal_shell_without_enroll(self):
        order = []
        shell = [{
            'status': 'page_and_health_verified', 'project_id': 'prj_Offline',
            'deployment_id': 'dpl_Offline', 'source_sha': self.sha,
        }]
        apply_lines = [{'status': 'already_configured', 'project_id': 'prj_Offline'}]
        verify_lines = [{'status': 'verification_passed', 'project_id': 'prj_Offline'}]

        polluted = 'f' * 64
        os.environ['ONBOARDING_DELIVERY_BINDING_SHA256'] = polluted

        def fake_script(script, source, binary, extra_args, env_updates, artifact_dir, **kw):
            op = extra_args[extra_args.index('--operation') + 1]
            order.append((script, op))
            if script == 'launch-app.py':
                self.assertEqual(op, 'deploy')
                # Inherited binding must be cleared on disabled/default path.
                self.assertEqual(env_updates.get('ONBOARDING_DELIVERY_BINDING_SHA256'), '')
                return 0, shell, '', ''
            if op == 'apply':
                return 0, apply_lines, '', ''
            return 0, verify_lines, '', ''

        try:
            with patch.object(orch, 'run_python_script', side_effect=fake_script):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = orch.run(
                        self.source, str(self.binary), self.artifacts,
                        quality_wait=lambda *a, **k: {
                            'status': 'quality_gate_passed', 'head_sha': self.sha,
                            'workflow_id': 1, 'run_id': 2, 'run_attempt': 1, 'event': 'push',
                            'head_branch': 'main', 'repository_id': '2000000001',
                            'repository_full_name': 'Owner/Example-App',
                            'path': '.github/workflows/app-quality.yml',
                        },
                        sleeper=lambda _s: None, clock=lambda: 0.0,
                        verify_window_sec=60, orchestration_lifetime_sec=120)
        finally:
            os.environ.pop('ONBOARDING_DELIVERY_BINDING_SHA256', None)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 0)
        self.assertEqual(result['stages']['database_delivery']['status'], 'delivery_disabled')
        self.assertEqual([o for o in order if o[0] == 'launch-app.py'], [('launch-app.py', 'deploy')])
        self.assertEqual(result['database']['status'], 'database_stages_unimplemented')
        self.assertIs(result['database']['database_ready'], False)

    def test_enabled_delivery_enroll_then_delivery_then_deploy_order(self):
        mig = self.source / 'db' / 'migrations'
        mig.mkdir(parents=True)
        (mig / '0001_a.sql').write_text('select 1;\n', encoding='utf-8')
        delivery_cfg = {
            'schema_version': 1,
            'enabled': True,
            'auto_apply': True,
            'namespace': 'merge_app',
            'migrations_path': 'db/migrations',
            'expected_project_ref': 'abcdefghijklmnop',
            'rotate_migrator': False,
        }
        (self.source / 'database-delivery.config.json').write_text(
            json.dumps(delivery_cfg, indent=2) + '\n', encoding='utf-8')
        self.sha = git_commit(self.source, 'enable-delivery')
        os.environ['GITHUB_SHA'] = self.sha
        os.environ['SUPABASE_ACCESS_TOKEN'] = 'offline-access-token'
        os.environ['SUPABASE_URL'] = 'https://abcdefghijklmnop.supabase.co'

        order = []
        digest = 'b' * 64
        enroll_events = [{
            'status': 'project_enrolled',
            'project_id': 'prj_Offline',
            'repository_id': '2000000001',
            'team_id': 'team_Offline',
            'source_sha': self.sha,
            'project_name': 'example-app',
        }]
        shell = [{
            'status': 'page_and_health_verified', 'project_id': 'prj_Offline',
            'deployment_id': 'dpl_Offline', 'source_sha': self.sha,
        }]
        apply_lines = [{'status': 'already_configured', 'project_id': 'prj_Offline'}]
        verify_lines = [{'status': 'verification_passed', 'project_id': 'prj_Offline'}]

        def fake_script(script, source, binary, extra_args, env_updates, artifact_dir, **kw):
            op = extra_args[extra_args.index('--operation') + 1]
            order.append((script, op))
            if script == 'launch-app.py' and op == 'enroll':
                return 0, enroll_events, '', ''
            if script == 'launch-app.py' and op == 'deploy':
                self.assertEqual(env_updates.get('ONBOARDING_DELIVERY_BINDING_SHA256'), digest)
                self.assertIn('--bound-project-id', extra_args)
                self.assertEqual(
                    extra_args[extra_args.index('--bound-project-id') + 1],
                    'prj_Offline',
                )
                self.assertNotIn('--expected-project-id', extra_args)
                return 0, shell, '', ''
            if op == 'apply':
                return 0, apply_lines, '', ''
            return 0, verify_lines, '', ''

        def fake_delivery_run(*_a, **_k):
            order.append(('delivery', 'run_for_merge'))
            return {
                'status': 'delivery_completed_local_runtime_proof',
                'database_ready': False,
                'full_app_ready': False,
                'delivery_binding_sha256': digest,
                'namespace': 'merge_app',
                'provision_created': True,
                'migrations_applied': 1,
                'local_runtime_proof': {'ok': True},
            }

        class FakeDelivery:
            DeliveryError = type('DeliveryError', (Exception,), {})

            @staticmethod
            def run_for_merge(*a, **k):
                return fake_delivery_run(*a, **k)

        real_load = orch.load

        def fake_load(name):
            if name == 'managed_database_delivery':
                return FakeDelivery
            return real_load(name)

        with patch.object(orch, 'load', side_effect=fake_load):
            with patch.object(orch, 'run_python_script', side_effect=fake_script):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = orch.run(
                        self.source, str(self.binary), self.artifacts,
                        quality_wait=lambda *a, **k: {
                            'status': 'quality_gate_passed', 'head_sha': self.sha,
                            'workflow_id': 1, 'run_id': 9, 'run_attempt': 1, 'event': 'push',
                            'head_branch': 'main', 'repository_id': '2000000001',
                            'repository_full_name': 'Owner/Example-App',
                            'path': '.github/workflows/app-quality.yml',
                        },
                        sleeper=lambda _s: None, clock=lambda: 0.0,
                        verify_window_sec=60, orchestration_lifetime_sec=120)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 0)
        self.assertEqual(
            [x for x in order if x[0] in ('launch-app.py', 'delivery')],
            [
                ('launch-app.py', 'enroll'),
                ('delivery', 'run_for_merge'),
                ('launch-app.py', 'deploy'),
            ],
        )
        self.assertEqual(
            result['stages']['database_delivery']['status'],
            'delivery_completed_local_runtime_proof')
        self.assertEqual(
            result['stages']['shell_binding']['delivery_binding_sha256'], digest)
        self.assertEqual(
            result['database']['status'],
            'database_delivery_completed_deployed_verification_pending')
        self.assertEqual(
            result['database']['required_missing_stages'],
            ['deployed_http_jwt_data_path_verification'])
        self.assertIs(result['database']['database_ready'], False)
        self.assertIs(result['database']['full_app_ready'], False)
        self.assertIs(result['full_app_ready'], False)

    def test_delivery_deploy_rejects_project_drift_between_stages(self):
        mig = self.source / 'db' / 'migrations'
        mig.mkdir(parents=True)
        (mig / '0001_a.sql').write_text('select 1;\n', encoding='utf-8')
        (self.source / 'database-delivery.config.json').write_text(json.dumps({
            'schema_version': 1,
            'enabled': True,
            'auto_apply': True,
            'namespace': 'merge_app',
            'migrations_path': 'db/migrations',
            'expected_project_ref': 'abcdefghijklmnop',
            'rotate_migrator': False,
        }, indent=2) + '\n', encoding='utf-8')
        self.sha = git_commit(self.source, 'enable-delivery-drift')
        os.environ['GITHUB_SHA'] = self.sha
        os.environ['SUPABASE_ACCESS_TOKEN'] = 'offline-access-token'
        os.environ['SUPABASE_URL'] = 'https://abcdefghijklmnop.supabase.co'
        digest = 'c' * 64

        def fake_script(script, source, binary, extra_args, env_updates, artifact_dir, **kw):
            op = extra_args[extra_args.index('--operation') + 1]
            if script == 'launch-app.py' and op == 'enroll':
                return 0, [{
                    'status': 'project_enrolled',
                    'project_id': 'prj_Enrolled',
                    'repository_id': '2000000001',
                    'team_id': 'team_Offline',
                    'source_sha': self.sha,
                }], '', ''
            if script == 'launch-app.py' and op == 'deploy':
                self.assertEqual(
                    extra_args[extra_args.index('--bound-project-id') + 1],
                    'prj_Enrolled')
                # Inventory drift: deploy reports a different project.
                return 0, [{
                    'status': 'page_and_health_verified',
                    'project_id': 'prj_Other',
                    'deployment_id': 'dpl_Other',
                    'source_sha': self.sha,
                }], '', ''
            raise AssertionError('dns must not run after project drift')

        class FakeDelivery:
            DeliveryError = type('DeliveryError', (Exception,), {})

            @staticmethod
            def run_for_merge(*_a, **_k):
                return {
                    'status': 'delivery_completed_local_runtime_proof',
                    'delivery_binding_sha256': digest,
                    'namespace': 'merge_app',
                    'provision_created': True,
                    'migrations_applied': 1,
                    'local_runtime_proof': {'ok': True},
                }

        real_load = orch.load

        def fake_load(name):
            if name == 'managed_database_delivery':
                return FakeDelivery
            return real_load(name)

        with patch.object(orch, 'load', side_effect=fake_load):
            with patch.object(orch, 'run_python_script', side_effect=fake_script):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = orch.run(
                        self.source, str(self.binary), self.artifacts,
                        quality_wait=lambda *a, **k: {
                            'status': 'quality_gate_passed', 'head_sha': self.sha,
                            'workflow_id': 1, 'run_id': 9, 'run_attempt': 1, 'event': 'push',
                            'head_branch': 'main', 'repository_id': '2000000001',
                            'repository_full_name': 'Owner/Example-App',
                            'path': '.github/workflows/app-quality.yml',
                        },
                        sleeper=lambda _s: None, clock=lambda: 0.0,
                        verify_window_sec=60, orchestration_lifetime_sec=120)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'deploy_project_drift')
        self.assertEqual(
            result['stages']['shell_binding']['enrolled_project_id'], 'prj_Enrolled')
        self.assertEqual(
            result['stages']['shell_binding']['deployed_project_id'], 'prj_Other')

    def test_delivery_failure_blocks_deploy_and_dns(self):
        mig = self.source / 'db' / 'migrations'
        mig.mkdir(parents=True)
        (mig / '0001_a.sql').write_text('select 1;\n', encoding='utf-8')
        (self.source / 'database-delivery.config.json').write_text(json.dumps({
            'schema_version': 1,
            'enabled': True,
            'auto_apply': True,
            'namespace': 'merge_app',
            'migrations_path': 'db/migrations',
            'expected_project_ref': 'abcdefghijklmnop',
            'rotate_migrator': False,
        }, indent=2) + '\n', encoding='utf-8')
        self.sha = git_commit(self.source, 'enable-delivery-fail')
        os.environ['GITHUB_SHA'] = self.sha
        os.environ['SUPABASE_ACCESS_TOKEN'] = 'offline-access-token'
        os.environ['SUPABASE_URL'] = 'https://abcdefghijklmnop.supabase.co'
        order = []

        def fake_script(script, source, binary, extra_args, env_updates, artifact_dir, **kw):
            op = extra_args[extra_args.index('--operation') + 1]
            order.append((script, op))
            if script == 'launch-app.py' and op == 'enroll':
                return 0, [{
                    'status': 'project_enrolled',
                    'project_id': 'prj_Offline',
                    'repository_id': '2000000001',
                    'team_id': 'team_Offline',
                    'source_sha': self.sha,
                }], '', ''
            raise AssertionError('deploy/dns must not run after delivery failure')

        class FakeDelivery:
            class DeliveryError(Exception):
                pass

            @staticmethod
            def run_for_merge(*a, **k):
                order.append(('delivery', 'failed'))
                return {'status': 'recovery_required', 'namespace': 'merge_app'}

        real_load = orch.load

        def fake_load(name):
            if name == 'managed_database_delivery':
                return FakeDelivery
            return real_load(name)

        with patch.object(orch, 'load', side_effect=fake_load):
            with patch.object(orch, 'run_python_script', side_effect=fake_script):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = orch.run(
                        self.source, str(self.binary), self.artifacts,
                        quality_wait=lambda *a, **k: {
                            'status': 'quality_gate_passed', 'head_sha': self.sha,
                            'workflow_id': 1, 'run_id': 3, 'run_attempt': 1, 'event': 'push',
                            'head_branch': 'main', 'repository_id': '2000000001',
                            'repository_full_name': 'Owner/Example-App',
                            'path': '.github/workflows/app-quality.yml',
                        },
                        sleeper=lambda _s: None, clock=lambda: 0.0)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'database_delivery_failed')
        self.assertEqual(result['stages']['provider_writes'], 'blocked')
        self.assertEqual(order, [('launch-app.py', 'enroll'), ('delivery', 'failed')])


if __name__ == '__main__':
    unittest.main()

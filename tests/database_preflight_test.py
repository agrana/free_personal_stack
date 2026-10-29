#!/usr/bin/env python3
"""Database onboarding read-only managed-service preflight tests.

Unit/API boundary tests always run. PostgreSQL execution of the exact catalog
SQL reuses APP-003 disposable fixtures (APP003_PG_TESTS=0 skip, 1 mandatory,
auto when podman+image available). Never uses production credentials.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from typing import Any, Dict, Optional, Tuple
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = ROOT / 'db' / 'shared_isolation'
TEST_BASELINE_SQL = DB_ROOT / 'provision' / '00_disposable_test_baseline.sql'
PROVISION_SQL = DB_ROOT / 'provision' / '01_control_and_roles.sql'
PREEXISTING_SQL = DB_ROOT / 'fixtures' / 'preexisting' / '01_seed.sql'
ALPHA_DIR = DB_ROOT / 'fixtures' / 'app_alpha'
PG_IMAGE = os.environ.get('APP003_PG_IMAGE', 'docker.io/library/postgres:16-alpine')
RUN_PG = os.environ.get('APP003_PG_TESTS', 'auto')


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migrate = _load('migrate_app_namespace', ROOT / 'scripts/migrate_app_namespace.py')
cfg = _load('database_onboarding_config', ROOT / 'scripts/database-onboarding-config.py')
preflight = _load('database_preflight', ROOT / 'scripts/database-preflight.py')
orch = _load('merge_onboarding', ROOT / 'scripts/merge-onboarding.py')


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _podman_available() -> bool:
    try:
        subprocess.run(['podman', 'image', 'exists', PG_IMAGE], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            subprocess.run(['podman', 'pull', PG_IMAGE], check=True, capture_output=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False


def _pg_tests_enabled() -> bool:
    if RUN_PG == '0':
        return False
    if RUN_PG == '1':
        return True
    return _podman_available()


def write_db_config(source: Path, **overrides):
    payload = {
        'schema_version': 1,
        'enabled': False,
        'namespace': None,
    }
    payload.update(overrides)
    path = source / 'database-onboarding.config.json'
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def write_onboarding_config(source: Path, **overrides):
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


def git_commit(source: Path, message: str = 'config') -> str:
    subprocess.run(['git', 'add', '-A'], cwd=source, check=True)
    subprocess.run(
        ['git', '-c', 'user.name=Offline', '-c', 'user.email=offline@example.invalid',
         'commit', '-qm', message],
        cwd=source, check=True)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()


def sample_catalog(**overrides) -> Dict[str, Any]:
    base = {
        'server_version_num': 160001,
        'observer': {
            'rolsuper': False,
            'rolcreaterole': False,
            'rolbypassrls': False,
        },
        'bootstrap_candidate': {
            'present': True, 'rolsuper': False, 'rolcreaterole': False, 'rolbypassrls': False,
        },
        'namespace': {
            'present': False,
            'expected_owner_match': False,
            'collision': False,
        },
        'roles': {
            'owner': {
                'present': False, 'rolsuper': False, 'rolcreaterole': False, 'rolbypassrls': False,
            },
            'migrator': {
                'present': False, 'rolsuper': False, 'rolcreaterole': False, 'rolbypassrls': False,
            },
            'runtime': {
                'present': False, 'rolsuper': False, 'rolcreaterole': False, 'rolbypassrls': False,
            },
        },
        'control_schema_present': False,
        'app_tables': {
            'count': 0,
            'rls_enabled_count': 0,
            'rls_forced_count': 0,
            'policy_count': 0,
        },
    }
    base.update(overrides)
    return base


class FakeTransport:
    def __init__(self, status: int = 201, body: Optional[bytes] = None, exc: Optional[BaseException] = None):
        self.status = status
        self.body = body if body is not None else json.dumps([{'catalog': sample_catalog()}]).encode()
        self.exc = exc
        self.calls = []

    def request(self, *, method, url, headers, body, timeout) -> Tuple[int, bytes]:
        self.calls.append({
            'method': method,
            'url': url,
            'headers': dict(headers),
            'body': body,
            'timeout': timeout,
        })
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


class DatabaseOnboardingConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'app'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_db_config(self.source)
        self.sha = git_commit(self.source, 'seed')

    def test_template_and_example_disabled_fixture(self):
        # Generated apps enable ROOT config without editing inherited tests.
        example = json.loads((ROOT / 'examples/database-onboarding.config.example.json').read_text())
        self.assertIs(example['enabled'], False)
        self.assertIsNone(example['namespace'])
        self.assertEqual(example['schema_version'], 1)
        validated = cfg.validate_mapping(example)
        self.assertFalse(validated['enabled'])
        cfg.validate_mapping(json.loads((ROOT / 'database-onboarding.config.json').read_text()))

    def test_missing_config_is_disabled_unconfigured(self):
        (self.source / 'database-onboarding.config.json').unlink()
        git_commit(self.source, 'remove-db-config')
        loaded = cfg.load_database_onboarding_config(
            self.source, require_tracked_clean=False)
        self.assertEqual(loaded['config_status'], 'unconfigured')
        self.assertFalse(loaded['enabled'])
        transport = FakeTransport()
        report = preflight.run_preflight(
            self.source, require_tracked_clean=False, transport=transport)
        self.assertEqual(report['status'], 'preflight_disabled')
        self.assertFalse(report['catalog_inspection_completed'])
        self.assertEqual(transport.calls, [])

    def test_rejects_unknown_duplicate_types_symlink_and_reserved(self):
        with self.assertRaisesRegex(cfg.ConfigError, 'config_unknown_key'):
            cfg.validate_mapping({
                'schema_version': 1, 'enabled': False, 'namespace': None, 'extra': True,
            })
        with self.assertRaisesRegex(cfg.ConfigError, 'config_enabled_not_bool'):
            cfg.validate_mapping({'schema_version': 1, 'enabled': 1, 'namespace': None})
        with self.assertRaisesRegex(cfg.ConfigError, 'config_duplicate_key'):
            cfg.parse_mapping('{"schema_version":1,"schema_version":1,"enabled":false,"namespace":null}')
        with self.assertRaisesRegex(cfg.ConfigError, 'config_namespace_invalid'):
            cfg.validate_mapping({
                'schema_version': 1, 'enabled': True, 'namespace': 'public',
            })
        write_db_config(self.source, enabled=True, namespace='app_demo')
        git_commit(self.source, 'enabled')
        target = self.source / 'database-onboarding.config.json'
        target.unlink()
        os.symlink('elsewhere.json', target)
        (self.source / 'elsewhere.json').write_text('{}')
        with self.assertRaisesRegex(cfg.ConfigError, 'config_symlink_forbidden'):
            cfg.config_path(self.source)

    def test_dirty_and_head_mismatch_rejected_in_active_context(self):
        write_db_config(self.source, enabled=True, namespace='app_demo')
        sha = git_commit(self.source, 'enabled')
        loaded = cfg.load_database_onboarding_config(self.source, expected_sha=sha)
        self.assertTrue(loaded['enabled'])
        (self.source / 'database-onboarding.config.json').write_text(
            '{"schema_version":1,"enabled":true,"namespace":"app_demo"}\n')
        with self.assertRaisesRegex(cfg.ConfigError, 'config_source_dirty|config_working_tree'):
            cfg.load_database_onboarding_config(self.source, expected_sha=sha)

    def test_inherited_enabled_root_config_validates(self):
        write_db_config(self.source, enabled=True, namespace='app_arena')
        sha = git_commit(self.source, 'enable-root')
        loaded = cfg.load_database_onboarding_config(self.source, expected_sha=sha)
        self.assertTrue(loaded['enabled'])
        self.assertEqual(loaded['namespace'], 'app_arena')


class DatabasePreflightApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'app'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_db_config(self.source, enabled=True, namespace='app_demo')
        self.sha = git_commit(self.source, 'seed')
        self.env = {
            'SUPABASE_URL': 'https://abcdefghijklmnop.supabase.co',
            'SUPABASE_ACCESS_TOKEN': 'sbp_test_token_not_real',
        }

    def test_merge_failure_persists_safe_preflight_artifact(self):
        artifact_dir = Path(self.tmp.name) / 'artifacts'
        with self.assertRaisesRegex(preflight.PreflightError, 'provider_auth_or_permission_denied'):
            preflight.run_for_merge(
                self.source, expected_sha=self.sha, artifact_dir=artifact_dir,
                transport=FakeTransport(status=403, body=b'private provider body'), env=self.env)
        text = (artifact_dir / 'database-preflight-result.json').read_text()
        report = json.loads(text)
        self.assertEqual(report['status'], 'preflight_failed')
        self.assertFalse(report['catalog_inspection_completed'])
        self.assertNotIn(self.env['SUPABASE_ACCESS_TOKEN'], text)
        self.assertNotIn('private provider body', text)

    def test_url_validation_rejects_unsafe_forms(self):
        for url in (
            '',
            'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE',
            'http://abcdefghijklmnop.supabase.co',
            'https://abcdefghijklmnop.supabase.co/',
            'https://abcdefghijklmnop.supabase.co/path',
            'https://abcdefghijklmnop.supabase.co:443',
            'https://user:pass@abcdefghijklmnop.supabase.co',
            'https://abcdefghijklmnop.supabase.co?x=1',
            'https://abcdefghijklmnop.supabase.co#frag',
            'https://evil.example',
        ):
            with self.subTest(url=url):
                with self.assertRaises(preflight.PreflightError):
                    if not url or url == 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE':
                        preflight.project_ref_from_supabase_url(url or '')
                    else:
                        preflight.project_ref_from_supabase_url(url)

    def test_setup_required_sentinel_no_network(self):
        transport = FakeTransport()
        for env in (
            {'SUPABASE_URL': 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE',
             'SUPABASE_ACCESS_TOKEN': 'tok'},
            {'SUPABASE_URL': 'https://abcdefghijklmnop.supabase.co',
             'SUPABASE_ACCESS_TOKEN': 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'},
            {'SUPABASE_URL': 'https://abcdefghijklmnop.supabase.co',
             'SUPABASE_ACCESS_TOKEN': ''},
        ):
            report = preflight.run_preflight(
                self.source, require_tracked_clean=True, expected_sha=self.sha,
                transport=transport, env=env)
            self.assertEqual(report['status'], 'setup_required')
        self.assertEqual(transport.calls, [])

    def test_exact_read_only_endpoint_body_and_namespace_parameter(self):
        transport = FakeTransport()
        report = preflight.run_preflight(
            self.source, require_tracked_clean=True, expected_sha=self.sha,
            transport=transport, env=self.env)
        self.assertEqual(report['status'], 'catalog_inspection_completed')
        self.assertFalse(report['database_ready'])
        self.assertFalse(report['live_migrations_authorized'])
        self.assertFalse(report['full_app_ready'])
        self.assertTrue(report['catalog_inspection_completed'])
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call['method'], 'POST')
        self.assertEqual(
            call['url'],
            'https://api.supabase.com/v1/projects/abcdefghijklmnop/database/query/read-only')
        self.assertNotIn('/database/query"', call['url'])
        self.assertTrue(call['url'].endswith('/database/query/read-only'))
        payload = json.loads(call['body'].decode())
        self.assertEqual(payload['query'], preflight.CATALOG_SQL)
        self.assertEqual(payload['parameters'], ['app_demo'])
        self.assertNotIn('app_demo', payload['query'])
        self.assertIn('Authorization', call['headers'])
        self.assertTrue(call['headers']['Authorization'].startswith('Bearer '))
        # Credentials stay out of argv; only env-fed token in headers for the transport.
        self.assertNotIn(self.env['SUPABASE_ACCESS_TOKEN'], payload['query'])

    def test_http_errors_are_fixed_safe_codes(self):
        for status, code in (
            (401, 'provider_auth_or_permission_denied'),
            (403, 'provider_auth_or_permission_denied'),
            (429, 'provider_throttled'),
            (503, 'provider_unavailable'),
        ):
            transport = FakeTransport(status=status, body=b'{"message":"leak-me-secret"}')
            with self.assertRaisesRegex(preflight.PreflightError, '^' + code + '$'):
                preflight.execute_read_only_catalog(
                    project_ref='abcdefghijklmnop',
                    access_token='secret-token-value',
                    namespace='app_demo',
                    transport=transport,
                )

    def test_malformed_oversized_redirect_rejected(self):
        transport = FakeTransport(status=201, body=b'not-json')
        with self.assertRaisesRegex(preflight.PreflightError, 'provider_response_malformed'):
            preflight.execute_read_only_catalog(
                project_ref='abcdefghijklmnop', access_token='t',
                namespace='app_demo', transport=transport)
        transport = FakeTransport(status=201, body=b'x' * (preflight.MAX_RESPONSE_BYTES + 2))
        with self.assertRaisesRegex(preflight.PreflightError, 'provider_response_oversized'):
            preflight.execute_read_only_catalog(
                project_ref='abcdefghijklmnop', access_token='t',
                namespace='app_demo', transport=transport)
        transport = FakeTransport(status=302, body=b'')
        with self.assertRaisesRegex(preflight.PreflightError, 'provider_redirect_rejected'):
            preflight.execute_read_only_catalog(
                project_ref='abcdefghijklmnop', access_token='t',
                namespace='app_demo', transport=transport)

    def test_artifacts_and_argv_omit_credentials(self):
        artifacts = Path(self.tmp.name) / 'artifacts'
        transport = FakeTransport()
        report = preflight.run_preflight(
            self.source, require_tracked_clean=True, expected_sha=self.sha,
            transport=transport, env=self.env, artifact_dir=artifacts)
        text = (artifacts / 'database-preflight-result.json').read_text()
        self.assertNotIn(self.env['SUPABASE_ACCESS_TOKEN'], text)
        self.assertNotIn('Bearer ', text)
        dumped = json.dumps(report)
        self.assertNotIn(self.env['SUPABASE_ACCESS_TOKEN'], dumped)
        # CLI argv must never accept token flags.
        source = (ROOT / 'scripts/database-preflight.py').read_text()
        self.assertNotIn('--token', source)
        self.assertNotIn('--access-token', source)

    def test_github_context_rejection_uses_merge_helper(self):
        transport = FakeTransport()
        env = {
            **self.env,
            'GITHUB_REPOSITORY': 'Owner/Example-App',
            'GITHUB_REPOSITORY_ID': orch.UPSTREAM_ID,
            'GITHUB_SHA': self.sha,
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'push',
            'GITHUB_TOKEN': 'offline',
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(preflight.PreflightError, 'template_or_repository_invalid'):
                preflight.run_preflight(
                    self.source, require_tracked_clean=True, expected_sha=self.sha,
                    require_github_context=True, transport=transport, env=env)
        self.assertEqual(transport.calls, [])


class MergePreflightIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        write_onboarding_config(self.source)
        write_db_config(self.source)
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
            'SUPABASE_URL': 'https://offlineprojref00.supabase.co',
            'SUPABASE_ANON_KEY': 'sb_publishable_offline',
            'SUPABASE_ACCESS_TOKEN': 'sbp_offline_access',
            'CLOUDFLARE_API_TOKEN': 'cf_offline_token',
        }
        self.patches = [
            patch.dict(os.environ, self.env, clear=False),
            patch.object(orch, 'github', side_effect=self.github),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.quality_runs = []
        self.launch_calls = []

    def head_repository(self):
        return {
            'id': int(os.environ['GITHUB_REPOSITORY_ID']),
            'full_name': os.environ['GITHUB_REPOSITORY'],
        }

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

    def enable_shell(self, **extra):
        write_onboarding_config(
            self.source,
            hostname='app.example.com',
            cloudflare_zone_name='example.com',
            auto_apply=True,
            **extra,
        )
        self.sha = git_commit(self.source, 'enable-shell')
        os.environ['GITHUB_SHA'] = self.sha

    def enable_db_preflight(self, namespace='app_demo'):
        write_db_config(self.source, enabled=True, namespace=namespace)
        self.sha = git_commit(self.source, 'enable-db')
        os.environ['GITHUB_SHA'] = self.sha

    def run_orch(self, quality_wait=None, transport=None):
        def fake_run_python(script, source, binary, extra_args, env_updates, artifact_dir,
                            **kwargs):
            self.launch_calls.append(script)
            if script == 'launch-app.py':
                event = {
                    'status': 'page_and_health_verified',
                    'project_id': 'prj_Offline',
                    'deployment_id': 'dpl_Offline',
                    'source_sha': self.sha,
                }
                return 0, [event], json.dumps(event), ''
            if script == 'custom-domain.py':
                op = extra_args[extra_args.index('--operation') + 1]
                if op == 'apply':
                    event = {'status': 'apply_complete'}
                else:
                    event = {'status': 'verification_passed'}
                return 0, [event], json.dumps(event), ''
            raise AssertionError(script)

        original_load = orch.load
        injected_transport = transport

        def selective_load(name):
            module = original_load(name)
            if name == 'database-preflight' and injected_transport is not None:
                def run_for_merge(source, *, expected_sha, artifact_dir,
                                  transport=None, env=None):
                    return module.run_preflight(
                        source,
                        require_tracked_clean=True,
                        expected_sha=expected_sha,
                        transport=injected_transport,
                        env=env or os.environ,
                        artifact_dir=artifact_dir,
                    )
                module.run_for_merge = run_for_merge
            return module

        with contextlib.redirect_stdout(io.StringIO()):
            with patch.object(orch, 'run_python_script', side_effect=fake_run_python):
                with patch.object(orch, 'load', side_effect=selective_load):
                    code = orch.run(
                        self.source, str(self.binary), self.artifacts,
                        quality_wait=quality_wait)
        result = json.loads((self.artifacts / 'merge-onboarding-result.json').read_text())
        return code, result

    def test_disabled_preflight_does_not_alter_orchestrator_flow(self):
        self.enable_shell()
        code, result = self.run_orch(
            quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'},
            transport=FakeTransport())
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'infrastructure_dns_verified_database_incomplete')
        self.assertEqual(result['stages']['database_preflight']['status'], 'preflight_disabled')
        self.assertFalse(result['database']['catalog_inspection_completed'])
        self.assertIn('launch-app.py', self.launch_calls)

    def test_enabled_setup_required_blocks_provider_writes(self):
        self.enable_shell()
        self.enable_db_preflight()
        os.environ['SUPABASE_ACCESS_TOKEN'] = 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'
        transport = FakeTransport()
        code, result = self.run_orch(
            quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'},
            transport=transport)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'setup_required')
        self.assertEqual(result['stages']['provider_writes'], 'blocked')
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.launch_calls, [])

    def test_enabled_failure_blocks_provider_writes(self):
        self.enable_shell()
        self.enable_db_preflight()
        transport = FakeTransport(status=403, body=b'{"msg":"nope"}')
        code, result = self.run_orch(
            quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'},
            transport=transport)
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'database_preflight_failed')
        self.assertEqual(result['stages']['provider_writes'], 'blocked')
        self.assertEqual(self.launch_calls, [])

    def test_successful_catalog_continues_shell_db_still_incomplete(self):
        self.enable_shell()
        self.enable_db_preflight()
        transport = FakeTransport()
        code, result = self.run_orch(
            quality_wait=lambda *a, **k: {'status': 'quality_gate_passed'},
            transport=transport)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'infrastructure_dns_verified_database_incomplete')
        self.assertTrue(result['database']['catalog_inspection_completed'])
        self.assertFalse(result['database']['database_ready'])
        self.assertFalse(result['full_app_ready'])
        self.assertIn('launch-app.py', self.launch_calls)
        self.assertTrue((self.artifacts / 'database-preflight-result.json').is_file())

    def test_workflow_maps_access_token_secret(self):
        wf = (ROOT / '.github/workflows/merge-onboarding.yml').read_text()
        self.assertIn('SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}', wf)
        self.assertIn('SUPABASE_ACCESS_TOKEN', orch.SECRET_ENV_KEYS)


class DatabasePreflightReviewRegressions(unittest.TestCase):
    def test_valid_namespace_can_overlap_fixed_sql_vocabulary(self):
        for namespace in ('app', 'namespace', 'roles'):
            with self.subTest(namespace=namespace):
                transport = FakeTransport()
                preflight.execute_read_only_catalog(
                    project_ref='fixture', access_token='offline-token',
                    namespace=namespace, transport=transport)
                body = json.loads(transport.calls[0]['body'])
                self.assertEqual(body['query'], preflight.CATALOG_SQL)
                self.assertEqual(body['parameters'], [namespace])

    def test_cli_requires_github_context_by_default(self):
        report = preflight.build_report(status='preflight_disabled')
        with patch.object(preflight, 'run_preflight', return_value=report) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.main(['--source', str(ROOT)]), 0)
        self.assertIs(run.call_args.kwargs['require_github_context'], True)
        self.assertIs(run.call_args.kwargs['require_tracked_clean'], True)

    def test_invalid_config_encoding_has_fixed_safe_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / 'database-onboarding.config.json').write_bytes(b'\xff')
            with self.assertRaisesRegex(cfg.ConfigError, '^config_read_failed$'):
                cfg.load_database_onboarding_config(source, require_tracked_clean=False)

    def test_catalog_rejects_impossible_numeric_values(self):
        cases = [sample_catalog(server_version_num=-1)]
        for key, value in (('count', -1), ('rls_enabled_count', -1),
                           ('rls_forced_count', -1), ('policy_count', -1),
                           ('rls_enabled_count', 1), ('rls_forced_count', 1)):
            raw = sample_catalog()
            raw['app_tables'][key] = value
            cases.append(raw)
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(preflight.PreflightError):
                    preflight.normalize_catalog(raw)


@unittest.skipUnless(_pg_tests_enabled(), 'disposable local PostgreSQL (podman) unavailable')
class CatalogSqlPostgresTests(unittest.TestCase):
    container_name: str
    port: int
    password: str
    admin_url: str

    @classmethod
    def setUpClass(cls):
        cls.password = f'preflight-{uuid.uuid4().hex}'
        cls.port = _free_loopback_port()
        cls.container_name = f'preflight-pg-{uuid.uuid4().hex[:12]}'
        cls.admin_url = (
            f'postgresql://postgres:{cls.password}@127.0.0.1:{cls.port}/preflight')
        run = subprocess.run(
            [
                'podman', 'run', '-d', '--name', cls.container_name,
                '-p', f'127.0.0.1:{cls.port}:5432',
                '-e', f'POSTGRES_PASSWORD={cls.password}',
                '-e', 'POSTGRES_USER=postgres',
                '-e', 'POSTGRES_DB=preflight',
                PG_IMAGE,
            ],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            msg = f'podman run failed: {run.stderr.strip()}'
            if RUN_PG == '1':
                raise RuntimeError(msg)
            raise unittest.SkipTest(msg)
        try:
            deadline = time.time() + 40
            import psycopg
            while time.time() < deadline:
                try:
                    with psycopg.connect(cls.admin_url, connect_timeout=2) as conn:
                        conn.execute('SELECT 1')
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                ready_msg = 'postgres did not become ready on loopback'
                if RUN_PG == '1':
                    raise RuntimeError(ready_msg)
                raise unittest.SkipTest(ready_msg)
            cls._bootstrap()
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def _cleanup(cls):
        subprocess.run(['podman', 'rm', '-f', cls.container_name], capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    @classmethod
    def _bootstrap(cls):
        import psycopg
        baseline = TEST_BASELINE_SQL.read_text(encoding='utf-8')
        provision = PROVISION_SQL.read_text(encoding='utf-8')
        preexisting = PREEXISTING_SQL.read_text(encoding='utf-8')
        with psycopg.connect(cls.admin_url, autocommit=True) as conn:
            migrate._execute_script(conn, baseline.encode('utf-8'))
            migrate._execute_script(conn, provision.encode('utf-8'))
            migrate._execute_script(conn, preexisting.encode('utf-8'))
            conn.execute(
                'SELECT app_migration_control.provision_app(%s, %s)',
                ('app_alpha', f'app_alpha-mig-{cls.password[-8:]}'),
            )
            conn.execute(
                'SELECT app_migration_control.allow_realtime_projection(%s, %s)',
                ('app_alpha', 'game_public_events'),
            )
        migrator_url = (
            f'postgresql://app_alpha_migrator:app_alpha-mig-{cls.password[-8:]}'
            f'@127.0.0.1:{cls.port}/preflight'
        )
        result = migrate.migrate_app(
            database_url=migrator_url,
            namespace='app_alpha',
            migrations_dir=ALPHA_DIR,
        )
        if result.status != 'ok':
            raise RuntimeError('fixture migrate failed')

    def _run_exact_catalog_sql(self, namespace: str) -> Dict[str, Any]:
        import psycopg
        from psycopg import sql as pg_sql
        # Bind via PostgreSQL PREPARE/EXECUTE so the statement text stays exactly
        # CATALOG_SQL ($1 parameters), not a Python %s rewrite of the catalog query.
        with psycopg.connect(self.admin_url) as conn:
            conn.execute('DEALLOCATE ALL')
            conn.execute('PREPARE db_preflight_catalog (text) AS ' + preflight.CATALOG_SQL)
            row = conn.execute(
                pg_sql.SQL('EXECUTE db_preflight_catalog({})').format(
                    pg_sql.Literal(namespace))
            ).fetchone()
            self.assertIsNotNone(row)
            catalog = row[0]
            if isinstance(catalog, str):
                catalog = json.loads(catalog)
            return preflight.normalize_catalog(catalog)

    def test_exact_sql_under_restricted_effective_role(self):
        import psycopg
        from psycopg import sql as pg_sql
        with psycopg.connect(self.admin_url) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute('SET LOCAL ROLE app_alpha_runtime')
            conn.execute('PREPARE db_preflight_restricted (text) AS ' + preflight.CATALOG_SQL)
            raw = conn.execute(
                pg_sql.SQL('EXECUTE db_preflight_restricted({})').format(
                    pg_sql.Literal('app_alpha'))).fetchone()[0]
            catalog = preflight.normalize_catalog(raw)
        self.assertFalse(catalog['observer']['rolsuper'])
        self.assertFalse(catalog['observer']['rolcreaterole'])
        self.assertFalse(catalog['observer']['rolbypassrls'])
        self.assertTrue(catalog['bootstrap_candidate']['present'])
        self.assertTrue(catalog['bootstrap_candidate']['rolsuper'])
        self.assertTrue(catalog['namespace']['present'])

    def test_exact_sql_empty_namespace(self):
        catalog = self._run_exact_catalog_sql('app_missing')
        self.assertIsInstance(catalog['server_version_num'], int)
        self.assertFalse(catalog['namespace']['present'])
        self.assertFalse(catalog['namespace']['collision'])
        self.assertFalse(catalog['roles']['owner']['present'])
        self.assertTrue(catalog['control_schema_present'])
        self.assertEqual(catalog['app_tables']['count'], 0)
        self.assertEqual(catalog['app_tables']['rls_enabled_count'], 0)
        self.assertEqual(catalog['app_tables']['policy_count'], 0)
        steps = preflight.required_next_steps(catalog)
        self.assertIn('provision_app_namespace_and_roles', steps)

    def test_exact_sql_owned_namespace_and_rls_counts(self):
        catalog = self._run_exact_catalog_sql('app_alpha')
        self.assertTrue(catalog['namespace']['present'])
        self.assertTrue(catalog['namespace']['expected_owner_match'])
        self.assertFalse(catalog['namespace']['collision'])
        self.assertTrue(catalog['roles']['owner']['present'])
        self.assertTrue(catalog['roles']['migrator']['present'])
        self.assertTrue(catalog['roles']['runtime']['present'])
        self.assertGreaterEqual(catalog['app_tables']['count'], 1)
        self.assertGreaterEqual(catalog['app_tables']['rls_enabled_count'], 1)
        self.assertGreaterEqual(catalog['app_tables']['policy_count'], 1)
        # Role flags are observations only.
        self.assertIn('rolbypassrls', catalog['roles']['owner'])

    def test_exact_sql_collision_schema(self):
        import psycopg
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute('CREATE SCHEMA IF NOT EXISTS app_collide AUTHORIZATION postgres')
        try:
            catalog = self._run_exact_catalog_sql('app_collide')
            self.assertTrue(catalog['namespace']['present'])
            self.assertTrue(catalog['namespace']['collision'])
            self.assertFalse(catalog['namespace']['expected_owner_match'])
            self.assertIn(
                'resolve_namespace_ownership_collision',
                preflight.required_next_steps(catalog))
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as conn:
                conn.execute('DROP SCHEMA IF EXISTS app_collide CASCADE')


if __name__ == '__main__':
    unittest.main()

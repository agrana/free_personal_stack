"""Offline provider contracts with real independent Git repos and CLI subprocesses."""
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
spec = importlib.util.spec_from_file_location('launcher', ROOT / 'scripts/launch-app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
GET_PAGE = app.get_page

STUB = '''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(__file__).with_suffix('.json')
s = json.loads(p.read_text())
a = sys.argv[1:]
assert os.environ.get('VERCEL_TOKEN') == 'offline-token'
assert not any(k in os.environ for k in ('SUPABASE_URL','SUPABASE_ANON_KEY','VERCEL_API_TOKEN','GITHUB_TOKEN'))
assert '--token' not in a and '--scope' in a
value = sys.stdin.read()
s['calls'].append([a, value])
if a[:2] == ['api','/v10/projects']:
    assert a[2:6] == ['-X','POST','--input','-']
    body = json.loads(value)
    assert body['framework'] == 'nextjs' and body['nodeVersion'] == '24.x'
    if s.get('collision409'):
        p.write_text(json.dumps(s)); print('HTTP 409',file=sys.stderr); sys.exit(1)
    assert not s['project']
    s['project'] = {'id':'prj_Offline', 'name':body['name'], 'accountId':'team_Offline'}
    print(json.dumps(s['project']))
elif a[:2] == ['project','inspect']:
    assert a[2] == s['project']['id']
    print(json.dumps({'id':s.get('inspect_id',s['project']['id']), 'name':s['project']['name'], 'owner':{'slug':'not-an-id'}, 'config':{}}))
elif a[:2] == ['env','add']:
    assert '--type' in a and a[a.index('--type')+1] == 'config'
    key = a[2]
    assert key.startswith('TEMPLATE_REPOSITORY_ID_') or key in ('NEXT_PUBLIC_SUPABASE_URL','NEXT_PUBLIC_SUPABASE_ANON_KEY')
    assert ('--force' in a) == key.startswith('NEXT_PUBLIC_')
    if s.get('interrupt') == key:
        p.write_text(json.dumps(s)); sys.exit(1)
    s['envs'] = [e for e in s['envs'] if e['key'] != key]
    s['envs'].append({'key':key, 'value':'opaque-ciphertext', 'target':['production'], 'type':'encrypted','visibility':'config'})
elif a[0] == 'deploy':
    sha = a[a.index('--env')+1].split('=',1)[1]
    assert a[a.index('--build-env')+1] == 'VERCEL_GIT_COMMIT_SHA='+sha
    metas = dict(a[i+1].split('=',1) for i,x in enumerate(a) if x == '--meta')
    assert metas['githubCommitSha'] == sha
    s['remote'] = {'id':'dpl_Offline','url':'offline.vercel.app','projectId':'prj_Offline','target':'production','readyState':'READY','meta':metas,'alias':['production.vercel.app']}
    s['project']['targets'] = {'production':{'id':'dpl_Offline'}}
    print(json.dumps({'id':'dpl_Offline','url':'offline.vercel.app','productionUrl':'production.vercel.app','target':'production','readyState':'READY'}))
else:
    raise AssertionError(a)
p.write_text(json.dumps(s))
'''

class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git','init','-q',str(self.source)], check=True)
        (self.source / 'app.txt').write_text('independent application')
        subprocess.run(['git','add','.'], cwd=self.source, check=True)
        subprocess.run(['git','-c','user.name=Offline','-c','user.email=offline@example.invalid',
                        'commit','-qm','independent'], cwd=self.source, check=True)
        self.sha = subprocess.check_output(['git','rev-parse','HEAD'], cwd=self.source, text=True).strip()
        self.binary = self.base / 'vercel'
        self.binary.write_text(STUB)
        self.binary.chmod(0o755)
        self.statefile = self.binary.with_suffix('.json')
        self.save({'project':None,'envs':[], 'calls':[]})
        self.env = {'GITHUB_REPOSITORY':'Owner/Example-App','GITHUB_REPOSITORY_ID':'2000000001',
            'GITHUB_SHA':self.sha,'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch',
            'VERCEL_API_TOKEN':'offline-token','VERCEL_TEAM_ID':'team_Offline',
            'SUPABASE_URL':'https://offline.supabase.co','SUPABASE_ANON_KEY':'sb_publishable_offline',
            'GITHUB_TOKEN':'offline-github'}
        self.real_load = app.load
        self.vercel = self.real_load('check-vercel-access')
        self.supabase = self.real_load('check-supabase-access')
        self.patches = [patch.dict(os.environ, self.env), patch.object(app,'github',side_effect=self.github),
            patch.object(app,'load',side_effect=lambda name: self.vercel if name == 'check-vercel-access' else self.supabase),
            patch.object(self.vercel,'items',side_effect=self.items),
            patch.object(self.vercel,'get_json',side_effect=self.read),
            patch.object(self.supabase,'check',return_value='passed'),
            patch.object(app,'get_page',side_effect=self.page)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
    def state(self):
        return json.loads(self.statefile.read_text())
    def save(self, s):
        self.statefile.write_text(json.dumps(s))
    def github(self, path):
        if path.startswith('/repositories/'):
            return {'id':int(os.environ['GITHUB_REPOSITORY_ID']), 'full_name':os.environ['GITHUB_REPOSITORY'],
                'is_template':False,'fork':False,'default_branch':'main'}
        return {'sha':self.sha}
    def items(self, route, key, token, query=None):
        return iter([{'id':'team_Offline'}] if key == 'teams' else
                    ([self.state()['project']] if self.state()['project'] else []))
    def read(self, path, token):
        s = self.state()
        if '/env?' in path: return {'envs':s['envs']}
        if path.startswith('/v9/projects/'): return s['project']
        if path.startswith('/v13/deployments/'): return s['remote']
        raise AssertionError(path)
    def page(self, url):
        return (200, json.dumps({'status':'ready','service':'app','revision':self.sha}).encode()) if url.endswith('/api/health') else (200,b'<!doctype html><html>Shell</html>')
    def run_app(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return app.run(self.source,str(self.binary),**kw)
    def deploy_count(self):
        return sum(c[0][0] == 'deploy' for c in self.state()['calls'])
    def test_two_independent_generated_repositories(self):
        self.assertEqual(self.run_app(),0)
        self.assertEqual(self.state()['project']['name'],'example-app')
        self.assertEqual(self.run_app(),0)
        self.assertEqual(self.run_app(operation='verify'),0)
        self.assertEqual(self.deploy_count(),1)
        # A new independent history, repository name and numeric identity.
        self.source = self.base / 'second-independent-source'
        self.source.mkdir()
        subprocess.run(['git','init','-q',str(self.source)], check=True)
        (self.source / 'second.txt').write_text('a separate root history')
        subprocess.run(['git','add','.'], cwd=self.source, check=True)
        subprocess.run(['git','-c','user.name=Offline','-c','user.email=offline@example.invalid',
                        'commit','-qm','second independent root'], cwd=self.source, check=True)
        self.sha = subprocess.check_output(['git','rev-parse','HEAD'], cwd=self.source,text=True).strip()
        os.environ.update(GITHUB_REPOSITORY='Other/Second-App',GITHUB_REPOSITORY_ID='2000000002',GITHUB_SHA=self.sha)
        self.save({'project':None,'envs':[], 'calls':[]})
        self.assertEqual(self.run_app(),0)
        self.assertEqual(self.state()['project']['name'],'second-app')
        self.assertEqual(self.state()['envs'][0]['key'],'TEMPLATE_REPOSITORY_ID_2000000002')
    def test_missing_and_placeholder_secrets_no_writes(self):
        for key in app.REQUIRED:
            original = os.environ[key]
            for value in ('','SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'):
                os.environ[key] = value
                self.assertEqual(self.run_app(),0)
                self.assertEqual(self.state()['calls'],[])
            os.environ[key] = original
    def test_push_without_merge_policy_rejected_and_verify_never_creates(self):
        with self.assertRaisesRegex(app.Failure,'manual_creation_required'):
            self.run_app(operation='verify')
        os.environ['GITHUB_EVENT_NAME']='push'
        with self.assertRaisesRegex(app.Failure,'event_not_authorized'): self.run_app()
        self.assertEqual(self.state()['calls'],[])
    def test_marker_conflict_and_exact_recovery(self):
        s=self.state(); s['project']={'id':'prj_Offline','name':'example-app','accountId':'team_Offline'}; self.save(s)
        with self.assertRaisesRegex(app.Failure,'unmarked_project'): self.run_app()
        with self.assertRaisesRegex(app.Failure,'expected_project_mismatch'):
            self.run_app(expected_project='prj_Wrong',confirm_repository='2000000001')
        self.assertEqual(self.run_app(expected_project='prj_Offline',confirm_repository='2000000001'),0)
        s=self.state(); s['envs'].append(dict(s['envs'][0],key=app.MARKER+'999')); self.save(s)
        with self.assertRaisesRegex(app.Failure,'repository_marker_conflict'):
            self.run_app(expected_project='prj_Offline',confirm_repository='2000000001')
    def test_interrupted_marker_fails_closed_and_explicitly_recovers(self):
        s=self.state(); s['interrupt']=app.MARKER+'2000000001'; self.save(s)
        with self.assertRaisesRegex(app.Failure,'cli_failed'): self.run_app()
        with self.assertRaisesRegex(app.Failure,'unmarked_project'): self.run_app()
        s=self.state(); del s['interrupt']; self.save(s)
        self.assertEqual(self.run_app(expected_project='prj_Offline',confirm_repository='2000000001'),0)
    def test_interrupted_public_env_resumes_owned_project(self):
        s=self.state(); s['interrupt']='NEXT_PUBLIC_SUPABASE_URL'; self.save(s)
        with self.assertRaisesRegex(app.Failure,'cli_failed'): self.run_app()
        s=self.state(); del s['interrupt']; self.save(s)
        self.assertEqual(self.run_app(),0)
        self.assertEqual(sum(c[0][:2]==['api','/v10/projects'] for c in self.state()['calls']),1)
    def test_sha_context_and_team_fail_closed(self):
        for key,value,error in [('GITHUB_SHA','a'*40,'source_sha_mismatch'),
            ('GITHUB_EVENT_NAME','pull_request','event_not_authorized'),
            ('GITHUB_REF','refs/heads/other','event_not_authorized'),
            ('GITHUB_REPOSITORY_ID',app.UPSTREAM_ID,'template_or_repository_invalid'),
            ('GITHUB_REPOSITORY','Owner/9unsupported','slug_invalid'),
            ('VERCEL_TEAM_ID','team_Wrong','team_unavailable')]:
            old=os.environ[key]; os.environ[key]=value
            with self.assertRaisesRegex(app.Failure,error): self.run_app()
            os.environ[key]=old
        self.assertEqual(self.state()['calls'],[])
    def test_protected_deployment_uses_bound_alias(self):
        with patch.object(app,'get_page',side_effect=lambda url: (401,b'') if 'offline.vercel.app' in url else self.page(url)):
            self.assertEqual(self.run_app(),0)
        with patch.object(app,'get_page',return_value=(401,b'')):
            self.assertEqual(self.run_app(operation='verify'),1)
    def test_redirect_and_wrong_revision_fail(self):
        self.assertEqual(self.run_app(),0)
        with patch.object(app,'get_page',return_value=(302,b'')):
            with self.assertRaisesRegex(app.Failure,'homepage_invalid'): self.run_app(operation='verify')
        with patch.object(app,'get_page',side_effect=lambda u: (200,b'{}') if u.endswith('/api/health') else self.page(u)):
            with self.assertRaisesRegex(app.Failure,'health_revision_invalid'): self.run_app(operation='verify')
    def test_source_change_updates_only_owned_project(self):
        self.assertEqual(self.run_app(),0)
        subprocess.run(['git','-c','user.name=Offline','-c','user.email=offline@example.invalid',
                        'commit','--allow-empty','-qm','new revision'], cwd=self.source, check=True)
        self.sha = subprocess.check_output(['git','rev-parse','HEAD'], cwd=self.source,text=True).strip()
        os.environ['GITHUB_SHA'] = self.sha
        self.assertEqual(self.run_app(),0)
        self.assertEqual(self.deploy_count(),2)
        self.assertEqual(sum(c[0][:2]==['api','/v10/projects'] for c in self.state()['calls']),1)
    def test_creation_collision_never_marks_or_deploys(self):
        s=self.state(); s['collision409']=True; self.save(s)
        with self.assertRaisesRegex(app.Failure,'cli_failed'): self.run_app()
        self.assertEqual(len(self.state()['calls']),1)
        self.assertEqual(self.state()['envs'],[])
        self.assertEqual(self.deploy_count(),0)
    def test_create_result_id_must_match_inspection(self):
        s=self.state(); s['inspect_id']='prj_Other'; self.save(s)
        with self.assertRaisesRegex(app.Failure,'project_scope_mismatch'): self.run_app()
        self.assertEqual(self.state()['envs'],[])
    def test_same_sha_changed_public_config_rebuilds(self):
        self.assertEqual(self.run_app(),0)
        before=len(self.state()['calls'])
        os.environ['SUPABASE_ANON_KEY']='sb_publishable_rotated'
        with self.assertRaisesRegex(app.Failure,'deployment_revision_not_ready'):
            self.run_app(operation='verify')
        self.assertEqual(len(self.state()['calls']),before)
        self.assertEqual(self.run_app(),0)
        self.assertEqual(self.deploy_count(),2)
        self.assertEqual(sum(c[0][:2]==['env','add'] for c in self.state()['calls']),5)
    def test_superseding_push_after_env_before_deploy_refuses(self):
        original=self.github
        def raced(path):
            result=original(path)
            if '/commits/' in path and len(self.state()['envs']) == 3:
                return {'sha':'0'*40}
            return result
        with patch.object(app,'github',side_effect=raced):
            with self.assertRaisesRegex(app.Failure,'source_sha_mismatch'): self.run_app()
        self.assertEqual(self.deploy_count(),0)
        self.assertEqual(len(self.state()['envs']),3)
    def test_verify_has_no_cli_writes_and_wrong_raw_projection_fails(self):
        self.assertEqual(self.run_app(),0)
        before=self.state()['calls']
        self.assertEqual(self.run_app(operation='verify'),0)
        self.assertEqual(self.state()['calls'],before)
        s=self.state(); s['remote']['meta']['githubCommitSha']='0'*40; self.save(s)
        with self.assertRaisesRegex(app.Failure,'deployment_revision_not_ready'):
            self.run_app(operation='verify')
        self.assertEqual(self.state()['calls'],before)
    def test_ambiguous_team_and_repository_api_identity(self):
        with patch.object(self.vercel,'items',return_value=iter([{'id':'team_Offline'},{'id':'team_Offline'}])):
            with self.assertRaisesRegex(app.Failure,'team_unavailable'): self.run_app()
        with patch.object(app,'github',return_value={'id':2000000001,'full_name':'Owner/Example-App','is_template':True,'fork':False}):
            with self.assertRaisesRegex(app.Failure,'repository_identity_mismatch'): self.run_app()
        self.assertEqual(self.state()['calls'],[])
    def test_redirect_classification_never_follows(self):
        from urllib.error import HTTPError
        for location, expected in [('https://vercel.com/sso-api?next=hidden',401),
            ('https://evil.invalid/',307),('https://vercel.com.evil.invalid/sso-api',307),
            ('https://user@vercel.com/sso-api',307)]:
            error=HTTPError('https://offline.vercel.app',307,'redirect',{'Location':location},None)
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=error
                self.assertEqual(GET_PAGE('https://offline.vercel.app'),(expected,b''))
                self.assertEqual(opener.return_value.open.call_count,1)
    def test_url_and_public_key_restrictions(self):
        for url in ('http://test.vercel.app','https://user@test.vercel.app','https://test.vercel.app.evil.invalid','https://test.vercel.app/path'):
            with self.assertRaises(app.Failure): app.safe_url(url)
        public = self.real_load('secrets-preflight')
        self.assertNotEqual(public.classify_supabase_public_key('sb_secret_private'), 'public')
        self.assertNotEqual(public.classify_supabase_public_key('SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'), 'public')

    def test_enroll_creates_marker_never_deploys(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = app.run(self.source, str(self.binary), operation='enroll')
        self.assertEqual(code, 0)
        events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        self.assertTrue(any(e.get('status') == 'plan' and 'production_deployment' not in e.get('allowed_mutations', []) for e in events))
        plan = next(e for e in events if e.get('status') == 'plan')
        self.assertEqual(plan['allowed_mutations'], ['create_project', 'repository_marker'])
        enrolled = next(e for e in events if e.get('status') == 'project_enrolled')
        self.assertEqual(enrolled['project_id'], 'prj_Offline')
        self.assertEqual(enrolled['repository_id'], '2000000001')
        self.assertEqual(enrolled['team_id'], 'team_Offline')
        self.assertEqual(enrolled['source_sha'], self.sha)
        self.assertEqual(self.deploy_count(), 0)
        self.assertTrue(any(e['key'] == app.MARKER + '2000000001' for e in self.state()['envs']))
        # Existing marked project enrolls without deploy even if production target missing/old.
        s = self.state()
        s['project']['targets'] = {'production': {'id': 'dpl_Old'}}
        s['remote'] = {
            'id': 'dpl_Old', 'url': 'offline.vercel.app', 'projectId': 'prj_Offline',
            'target': 'production', 'readyState': 'ERROR',
            'meta': {'githubCommitSha': '0' * 40, 'githubRepoId': '2000000001'},
        }
        self.save(s)
        before = list(self.state()['calls'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.run(self.source, str(self.binary), operation='enroll'), 0)
        self.assertEqual(self.deploy_count(), 0)
        self.assertEqual(self.state()['calls'], before)

    def test_enroll_recovery_same_safeguards_and_delivery_reuse(self):
        s = self.state()
        s['project'] = {'id': 'prj_Offline', 'name': 'example-app', 'accountId': 'team_Offline'}
        self.save(s)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(app.Failure, 'unmarked_project'):
                app.run(self.source, str(self.binary), operation='enroll')
            self.assertEqual(
                app.run(self.source, str(self.binary), operation='enroll',
                        expected_project='prj_Offline', confirm_repository='2000000001'),
                0)
        self.assertEqual(self.deploy_count(), 0)
        # Public-config READY shell must not satisfy delivery-bound deploy reuse.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.run(self.source, str(self.binary), operation='deploy'), 0)
        digest = 'a' * 64
        os.environ['ONBOARDING_DELIVERY_BINDING_SHA256'] = digest
        try:
            before = self.deploy_count()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.run(self.source, str(self.binary), operation='deploy'), 0)
            self.assertEqual(self.deploy_count(), before + 1)
            metas = {}
            for call, _value in self.state()['calls']:
                if call and call[0] == 'deploy':
                    for i, flag in enumerate(call):
                        if flag == '--meta':
                            key, value = call[i + 1].split('=', 1)
                            metas[key] = value
            self.assertEqual(metas.get('templateDeliveryBindingSha256'), digest)
        finally:
            os.environ.pop('ONBOARDING_DELIVERY_BINDING_SHA256', None)

    def test_canonical_project_name_normalizes_underscores(self):
        self.assertEqual(
            app.canonical_vercel_project_name('my_app_repo'), 'my-app-repo')
        self.assertEqual(
            app.canonical_vercel_project_name('Example-App'), 'example-app')
        with self.assertRaisesRegex(app.Failure, 'slug_invalid'):
            app.canonical_vercel_project_name('---')
        previous = os.environ['GITHUB_REPOSITORY']
        os.environ['GITHUB_REPOSITORY'] = 'Owner/my_app_repo'
        try:
            self.save({'project': None, 'envs': [], 'calls': []})
            self.assertEqual(self.run_app(), 0)
            self.assertEqual(self.state()['project']['name'], 'my-app-repo')
        finally:
            os.environ['GITHUB_REPOSITORY'] = previous
            self.save({'project': None, 'envs': [], 'calls': []})

    def test_bound_deploy_preserves_matching_reviewed_recovery_policy(self):
        s = self.state()
        s['project'] = {'id': 'prj_Offline', 'name': 'example-app', 'accountId': 'team_Offline'}
        s['envs'] = [{
            'key': app.MARKER + '2000000001', 'value': 'opaque',
            'target': ['production'], 'type': 'encrypted', 'visibility': 'config',
        }]
        self.save(s)
        policy = {
            'auto_apply': True,
            'expected_project_id': 'prj_Offline',
            'confirm_repository_id': '2000000001',
            'hostname': 'example.invalid',
        }
        with patch.object(app, 'context', return_value=(
                '2000000001', 'example-app', self.sha, policy)), \
                patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push'}):
            self.assertEqual(self.run_app(bound_project='prj_Offline'), 0)
            s = self.state()
            s['envs'] = []
            self.save(s)
            with self.assertRaisesRegex(app.Failure, 'bound_project_marker_missing'):
                self.run_app(bound_project='prj_Offline')
            policy['expected_project_id'] = 'prj_Other'
            with self.assertRaisesRegex(app.Failure, 'bound_project_recovery_conflict'):
                self.run_app(bound_project='prj_Offline')

    def test_bound_project_pins_marked_project_and_rejects_drift(self):
        s = self.state()
        s['project'] = {'id': 'prj_Offline', 'name': 'example-app', 'accountId': 'team_Offline'}
        s['envs'] = [{
            'key': app.MARKER + '2000000001', 'value': 'opaque',
            'target': ['production'], 'type': 'encrypted', 'visibility': 'config',
        }]
        self.save(s)
        with self.assertRaisesRegex(app.Failure, 'bound_project_mismatch'):
            self.run_app(bound_project='prj_Other')
        # Bound + recovery is exclusive.
        with self.assertRaisesRegex(app.Failure, 'bound_project_recovery_conflict'):
            self.run_app(
                bound_project='prj_Offline',
                expected_project='prj_Offline',
                confirm_repository='2000000001')
        before = self.deploy_count()
        self.assertEqual(self.run_app(bound_project='prj_Offline'), 0)
        self.assertEqual(self.deploy_count(), before + 1)
        # Unmarked bound project cannot be adopted via bound pin.
        s = self.state()
        s['envs'] = []
        self.save(s)
        with self.assertRaisesRegex(app.Failure, 'bound_project_marker_missing'):
            self.run_app(bound_project='prj_Offline')

if __name__ == '__main__': unittest.main()

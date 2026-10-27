#!/usr/bin/env python3
"""Offline source contracts for the database-free template landing shell."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TemplateShellTests(unittest.TestCase):
    def test_legacy_onboarding_cannot_run_automatically_or_publish_credentials(self):
        for name in ('initial-setup.yml', 'template-setup.yml', 'repository-setup-trigger.yml'):
            text = (ROOT / '.github/workflows' / name).read_text()
            with self.subTest(workflow=name):
                self.assertIn('workflow_dispatch:', text)
                self.assertNotIn('push:', text)
                self.assertNotIn('repository_dispatch:', text)
                self.assertIn('contents: read', text)
                self.assertNotIn('TERRAFORM_STATE_PASSWORD', text)
                self.assertNotIn('issues.create', text)
                self.assertNotIn('secrets.', text)
        terraform = (ROOT / '.github/workflows/terraform.yml').read_text()
        self.assertNotIn('\n  push:\n', terraform)
        self.assertNotIn('\n  pull_request:\n', terraform)

    def test_real_server_render_needs_no_database_configuration(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
for (const key of Object.keys(process.env)) {
  if (/SUPABASE|VERCEL_GIT_REPO_SLUG/.test(key)) delete process.env[key];
}
const filename = path.resolve('app/page.tsx');
const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS }
}).outputText;
const mod = new Module(filename, module);
mod.filename = filename;
mod.paths = Module._nodeModulePaths(path.dirname(filename));
mod._compile(source, filename);
const render = () => renderToStaticMarkup(React.createElement(mod.exports.default));
const html = render();
assert.match(html, /<h1[^>]*>Application ready<\/h1>/);
assert.match(html, /Your application/);
assert.match(html, /href="\/api\/health"/);
process.env.VERCEL_GIT_REPO_SLUG = 'my-application';
assert.match(render(), /my application/);
process.env.VERCEL_GIT_REPO_SLUG = '<script>alert(1)</script>';
assert.ok(!render().includes('<script>'));
console.log('Server render: fallback, optional repository title and escaping verified');
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_root_is_a_server_rendered_database_free_shell(self):
        page = (ROOT / 'app/page.tsx').read_text()
        layout = (ROOT / 'app/layout.tsx').read_text()
        self.assertIn('Application ready', page)
        self.assertIn('/api/health', page)
        for source in (page, layout):
            self.assertNotRegex(source, r"['\"]use client['\"]")
            self.assertNotRegex(source, r'(?i)supabase|createClient|Navigation|VerificationDashboard|DashboardSkeleton')
            self.assertNotRegex(source, r'(?i)\.from\(|\.rpc\(|fetch\(|useEffect|useState')
        self.assertNotRegex(page, r'(?i)demo|sign.?in|database connected|all systems operational')
        self.assertNotRegex(layout, r'next/font|Infrastructure Verification')

    def test_auth_building_blocks_remain_but_are_not_root_navigation(self):
        for path in ('app/auth/signin/page.tsx', 'app/auth/signout/route.ts',
                     'app/components/Navigation.tsx'):
            self.assertTrue((ROOT / path).is_file(), path)
        self.assertNotIn('/auth/', (ROOT / 'app/page.tsx').read_text())

    def test_health_retains_exact_revision_and_no_database_probe(self):
        health = (ROOT / 'app/api/health/route.ts').read_text()
        self.assertIn('VERCEL_GIT_COMMIT_SHA', health)
        self.assertIn('FULL_SHA_RE', health)
        self.assertIn("service: HEALTH_SERVICE", health)
        self.assertNotRegex(health, r'(?i)supabase|createClient|\.from\(')

    def test_launch_onboarding_names_exact_required_secrets(self):
        docs = (ROOT / 'docs/secrets-only-launch.md').read_text()
        for name in ('VERCEL_API_TOKEN', 'VERCEL_TEAM_ID', 'SUPABASE_URL', 'SUPABASE_ANON_KEY'):
            self.assertIn(name, docs)
        for term in ('expected_project_id', 'confirm_repository_id', '/auth/signin', '/auth/signout', 'RLS'):
            self.assertIn(term, docs)
        self.assertIn('secrets-only-launch.md', (ROOT / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()

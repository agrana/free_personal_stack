"""Offline verification of public-only Supabase reachability probe."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('supabase_probe', Path(__file__).resolve().parents[1] / 'scripts/check-supabase-access.py')
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
PUBLIC = 'sb_publishable_' + hashlib.sha256(b'probe-test').hexdigest()


class SupabaseAccessTests(unittest.TestCase):
    def test_rejects_unsafe_urls_before_network(self):
        with patch.object(probe.urllib.request, 'build_opener') as opener:
            for url in ('', 'http://abc.supabase.co', 'https://example.test', 'https://abc.supabase.co@evil.test', 'https://abc.supabase.co/path', 'https://abc.supabase.co:443'):
                with self.subTest(url=url), self.assertRaises(probe.CheckFailure):
                    probe.check(url, PUBLIC)
            opener.assert_not_called()

    def test_rejects_non_public_keys_before_network(self):
        with patch.object(probe.urllib.request, 'build_opener') as opener:
            for key in ('', 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE', 'sb_secret_' + 'x' * 32):
                with self.assertRaises(probe.CheckFailure):
                    probe.check('https://abc.supabase.co', key)
            opener.assert_not_called()

    def test_only_gets_settings_and_never_reads_response_body(self):
        with patch.object(probe.urllib.request, 'build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            result = probe.check('https://abc.supabase.co', PUBLIC)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.full_url, 'https://abc.supabase.co/auth/v1/settings')
            response.read.assert_not_called()
            self.assertNotIn(PUBLIC, result)
            self.assertIn('RLS remain unverified', result)

    def test_http_error_does_not_leak_response_or_key(self):
        with patch.object(probe.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = probe.urllib.error.HTTPError('https://abc.supabase.co', 401, PUBLIC, {}, None)
            with self.assertRaisesRegex(probe.CheckFailure, '^Supabase Auth settings GET failed: HTTP 401$'):
                probe.check('https://abc.supabase.co', PUBLIC)

    def test_refuses_redirect(self):
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test'))


if __name__ == '__main__':
    unittest.main()

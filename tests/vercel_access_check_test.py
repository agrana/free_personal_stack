"""Offline checks for the read-only provider access probe."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('vercel_probe', Path(__file__).resolve().parents[1] / 'scripts/check-vercel-access.py')
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class AccessCheckTests(unittest.TestCase):
    def test_placeholder_is_rejected_before_network(self):
        with patch.object(probe, 'get_json') as get:
            for token in ('', 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE'):
                with self.assertRaises(probe.CheckFailure):
                    probe.check(token)
            get.assert_not_called()

    def test_allowlisted_metadata_only(self):
        with patch.object(probe, 'get_json', side_effect=[
            {'user': {'id': 'account-test', 'email': 'private@example.test'}},
            {'teams': [{'id': 'team-test', 'name': 'private-team-name'}]},
            {'projects': [{'id': 'project-test', 'name': 'agent-arena', 'env': 'private'}]},
        ]) as get:
            result = probe.check('synthetic-token')
            self.assertEqual(result, {'authentication': 'passed', 'account_id': 'account-test', 'teams': [{'id': 'team-test', 'projects': [{'id': 'project-test', 'name': 'agent-arena'}]}]})
            self.assertTrue(all(call.args[0].startswith(('/v2/user', '/v2/teams?', '/v9/projects?')) for call in get.call_args_list))

    def test_pagination(self):
        with patch.object(probe, 'get_json', side_effect=[
            {'teams': [{'id': 'one'}], 'pagination': {'next': 100}},
            {'teams': [{'id': 'two'}]},
        ]) as get:
            self.assertEqual(len(list(probe.items('/v2/teams', 'teams', 'synthetic'))), 2)
            self.assertIn('until=100', get.call_args_list[1].args[0])

    def test_repeat_cursor_fails(self):
        with patch.object(probe, 'get_json', return_value={'teams': [], 'pagination': {'next': 1}}):
            with self.assertRaises(probe.CheckFailure):
                list(probe.items('/v2/teams', 'teams', 'synthetic'))

    def test_redirects_are_rejected(self):
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test'))

    def test_http_error_is_sanitized(self):
        error = probe.urllib.error.HTTPError('https://api.vercel.com/v2/user', 401, 'private-body', {}, None)
        with patch.object(probe.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(probe.CheckFailure, '^Vercel GET failed: HTTP 401$'):
                probe.get_json('/v2/user', 'synthetic')
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.full_url, 'https://api.vercel.com/v2/user')


if __name__ == '__main__':
    unittest.main()

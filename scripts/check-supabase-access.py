#!/usr/bin/env python3
"""Validate public Supabase configuration and GET Auth settings, without data access."""
import importlib.util
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


class CheckFailure(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(url, key):
    if not isinstance(url, str) or not re.fullmatch(r'https://[a-z0-9]+\.supabase\.co', url):
        raise CheckFailure('SUPABASE_URL must be the hosted HTTPS project URL without a trailing slash or path')
    spec = importlib.util.spec_from_file_location('probe_preflight', Path(__file__).with_name('secrets-preflight.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.classify_supabase_public_key(key) != 'public':
        raise CheckFailure('SUPABASE_ANON_KEY must contain a public publishable key or legacy anon key')
    request = urllib.request.Request(
        url + '/auth/v1/settings', headers={'apikey': key, 'Accept': 'application/json'}, method='GET'
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise CheckFailure('Supabase Auth settings GET returned an unexpected status')
        # Do not log the body or any public/secret configuration values.
    except urllib.error.HTTPError as exc:
        raise CheckFailure('Supabase Auth settings GET failed: HTTP ' + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CheckFailure('Supabase Auth settings GET failed: connection error') from None
    return 'Public configuration valid; Supabase Auth settings GET passed. Table permissions and RLS remain unverified.'


def main():
    try:
        message = check(os.environ.get('SUPABASE_URL', ''), os.environ.get('SUPABASE_ANON_KEY', ''))
    except CheckFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == '__main__':
    sys.exit(main())

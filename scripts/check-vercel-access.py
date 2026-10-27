#!/usr/bin/env python3
"""Read-only Vercel inventory; never print credentials or API error bodies."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class CheckFailure(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_json(path, token):
    # Callers supply fixed API routes and encoded query parameters only.
    if not path.startswith('/') or path.startswith('//'):
        raise CheckFailure('Invalid API route')
    request = urllib.request.Request(
        'https://api.vercel.com' + path,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
        method='GET',
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise CheckFailure('API response exceeds inventory bound')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise CheckFailure('Invalid API response shape')
        return result
    except urllib.error.HTTPError as exc:
        raise CheckFailure('Vercel GET failed: HTTP ' + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise CheckFailure('Vercel GET failed: transport or JSON error') from None


def items(route, key, token, query=None):
    query = dict(query or {})
    query['limit'] = 100
    seen = set()
    for _ in range(20):
        data = get_json(route + '?' + urllib.parse.urlencode(query), token)
        page = data.get(key)
        if not isinstance(page, list) or not all(isinstance(x, dict) for x in page):
            raise CheckFailure('Invalid inventory page')
        yield from page
        pagination = data.get('pagination') or {}
        if not isinstance(pagination, dict):
            raise CheckFailure('Invalid pagination')
        cursor = pagination.get('next')
        if cursor is None:
            return
        if not isinstance(cursor, (str, int)) or str(cursor) in seen:
            raise CheckFailure('Invalid inventory cursor')
        seen.add(str(cursor))
        query['until'] = cursor
    raise CheckFailure('Inventory exceeds page bound')


def check(token):
    if not token or token.strip() == 'SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE':
        raise CheckFailure('VERCEL_API_TOKEN is missing or still a placeholder')
    account = get_json('/v2/user', token).get('user')
    if not isinstance(account, dict) or not isinstance(account.get('id'), str):
        raise CheckFailure('Invalid account response')
    # Emit approved metadata only; omit usernames, emails, env values and domains.
    inventory = {'authentication': 'passed', 'account_id': account['id'], 'teams': []}
    for team in items('/v2/teams', 'teams', token):
        if not isinstance(team.get('id'), str):
            raise CheckFailure('Invalid team identifier')
        projects = list(items('/v9/projects', 'projects', token, {'teamId': team['id']}))
        inventory['teams'].append({
            'id': team['id'],
            'projects': [{'id': p.get('id'), 'name': p.get('name')} for p in projects],
        })
    return inventory


def main():
    try:
        result = check(os.environ.get('VERCEL_API_TOKEN', ''))
    except CheckFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

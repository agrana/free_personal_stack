# New app runbook

## Human quickstart (zero local setup)

1. GitHub **Use this template** → create an independent repository (not a fork). Use a lowercase slug such as `my-application` (letters, digits, single hyphens; start with a letter). Leave **Template repository** off on the new repo.
2. In that repository: **Settings → Secrets and variables → Actions** → add exactly these four repository secrets:

   | Secret | Value |
   | --- | --- |
   | `VERCEL_API_TOKEN` | Vercel access token authorized for the intended team |
   | `VERCEL_TEAM_ID` | That team's public ID (`team_…`) |
   | `SUPABASE_URL` | Existing Supabase project HTTPS URL |
   | `SUPABASE_ANON_KEY` | Modern `sb_publishable_…` key (legacy anon JWT also accepted) |

3. **Actions → Launch app → Run workflow** on the default branch. Operation `deploy`. Leave recovery inputs empty on the first run.
4. Read the summary status (`page_and_health_verified`, `ready_but_verification_protected`, or `setup_required`). A green `setup_required` run is not launch success.

No local `.env`, clone, app-config edit, custom DNS, Cloudflare account, service-role key, or Terraform credential is required for this shell. Secret edits do not start Actions. Details: [secrets-only-launch.md](secrets-only-launch.md).

### Optional secret-name placeholders

An authorized agent may precreate the four names (dry-run first) so you can edit values with the pencil icons:

```sh
python3 scripts/setup-secret-placeholders.py --repo '<username>/<repository>' --profile launch
# After reviewing the exact target and approving:
python3 scripts/setup-secret-placeholders.py --repo '<username>/<repository>' --profile launch --apply
```

Placeholders are not credentials. Replace `SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` in the GitHub UI. Never paste secret values into chat or tracked files.

## Maintainer local rehearsal (APP-008) — not a live deploy

This path proves a **second disposable app** can be generated from an exact Git tracked-tree export, built with synthetic public configuration, and exercised against a **local stub** provider. It is not cloud rehearsal and does not create Vercel/Supabase resources.

**Accepted local scope for APP-008:** verified local stub results suffice. Clean GitHub Actions plus actual second-repo/provider E2E are owned by **[APP-013](https://trello.com/c/MPRbb9Jl)** (no Actions gate on APP-008). APP-012 remains Arena historical evidence / live deploy-no-op; local rehearsal does not prove those.

| Label | Value |
| --- | --- |
| `provider_mode` | `stub` |
| `live_cloud_rehearsal` | `false` |
| Safety boundary | Patched provider clients + stub CLI (not an OS network sandbox) |

### Dependencies

- Node.js matching `package.json` `engines` (declared `24.x`), npm, Git
- Repository `.venv` with `requirements-bootstrap.txt` (for the unified test runner)
- Network only for `npm ci` against the package registry; provider APIs are not contacted

### Entrypoint

```sh
# Evidence goes outside the tracked tree (example: durable task log directory).
mkdir -p /tmp/app008-evidence
.venv/bin/python integration/bootstrap-rehearsal.py \
  --output-dir /tmp/app008-evidence
```

Optional durable work root (never deleted by the tool):

```sh
.venv/bin/python integration/bootstrap-rehearsal.py \
  --output-dir /tmp/app008-evidence \
  --work-root /tmp/app008-work
```

`--skip-npm` is **contract-only / incomplete**: status `local_contract_only`, `build_executed=false`, exit `0` when launcher scenarios pass. Status `local_rehearsal_complete` is reserved for an actually executed passing full quality path under Node 24.

Focused regressions (includes a real second-app `npm ci` / lint / type-check / build):

```sh
.venv/bin/python -m unittest tests.bootstrap_rehearsal_test -v
```

### Maintainer Node 24 acceptance (ephemeral; no global install)

Host shells may still be Node 22. Full APP-008 acceptance and the mandatory PostgreSQL suite should use a pinned ephemeral Node 24 on PATH (no system reconfiguration):

```sh
npx --yes --package=node@24.22.1 -c \
  'APP003_PG_TESTS=1 .venv/bin/python scripts/run-all-tests.py'

npx --yes --package=node@24.22.1 -c \
  '.venv/bin/python -m unittest tests.bootstrap_rehearsal_test -v'

npx --yes --package=node@24.22.1 -c \
  '.venv/bin/python integration/bootstrap-rehearsal.py --output-dir /tmp/app008-evidence'

npx --yes --package=node@24.22.1 -c 'npm run lint'
npx --yes --package=node@24.22.1 -c 'npm run type-check'
npx --yes --package=node@24.22.1 -c \
  'NEXT_PUBLIC_SUPABASE_URL=https://fixture-app008.supabase.co \
   NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_local_fixture_only \
   APP006_LOCAL_TEST_REVISION=$(git rev-parse HEAD) \
   VERCEL_GIT_COMMIT_SHA=$(git rev-parse HEAD) \
   npm run build'
```

Zero-local human onboarding above is unchanged. Evidence records actual `node`/`npm` versions and the invoked snapshot `source_revision` / independent `app_revision`.

### Operator checklist (local fixture)

1. Confirm branch intent and that this is **local stub** work (`live_cloud_rehearsal=false`).
2. Run under Node 24 (ephemeral wrapper above) when claiming full acceptance.
3. Run `integration/bootstrap-rehearsal.py` with an output directory outside the repo.
4. Confirm evidence JSON labels `provider_mode=stub`, synthetic IDs contain `fixture` / `Fixture`, and runtime Node major is 24.
5. Confirm collision rejection, interrupt/resume, and unchanged retry did not increase deployment counts; confirm captured launcher events include real `url` / `source_sha`.
6. Confirm second-app build `build_executed=true` with exit `0` and health revision equals the independent snapshot HEAD (not a hardcoded test SHA).
7. Do **not** treat local no-op as live deploy-no-op proof (APP-012). Do **not** treat local results as Actions/second-repo E2E (APP-013).
8. **APPROVAL required** before any new live resources, secret changes, workflow dispatch, or provider mutations.

### Expected output shape (fixture example)

Machine-readable events are captured from real launcher stdout (allowlisted fields only). Example fields (fixture only):

```json
{
  "provider_mode": "stub",
  "live_cloud_rehearsal": false,
  "status": "page_and_health_verified",
  "project_id": "prj_FixtureApp008",
  "deployment_id": "dpl_FixtureApp008",
  "url": "https://fixture-app008-rehearsal.vercel.app",
  "source_sha": "<independent-40-char-app-revision>",
  "fixture": true
}
```

Schema comparison vs Agent Arena CLI emitter is a **STATIC source review reference** (`../agent-arena/scripts/deploy-arena-shell-cli.py`, reviewed source SHA literal `fa9b793dec761ac859ada434510c79b7248635e4`) — not runtime verification of a live evidence manifest. Shared categories: `project_readback`, `page_and_health_verified`, `ready_but_verification_protected`, plus success fields `status`, `project_id`, `deployment_id`, `url`, `source_sha`.

Differences (not invented live evidence):

- Arena also emits `deployment_created`; the template launcher emits `plan`, `setup_required`, and `project_created_recovery_handle` instead/in addition.
- Arena homepage check looks for the literal product string; the template launcher accepts a generic HTML doctype for the DB-free shell.
- No historical production evidence blob is fabricated here; APP-012 owns live evidence gaps and live deploy-no-op proof.
- Actions cleanliness and real second-repo/provider E2E are APP-013.

## Known limits after the shell

- `/api/health` is process liveness + revision only — not database, auth, or RLS readiness.
- Protected Vercel deployments may yield `ready_but_verification_protected` (not public verification success).
- Custom DNS is APP-010. Live DB/auth isolation is APP-011. Database-backed gameplay stays gated on APP-011.
- Advanced initializer live adapter remains blocked (`apply.live_integration_missing`).
- Any new live cloud resource requires separate explicit approval.

## Related docs

- [secrets-only-launch.md](secrets-only-launch.md) — primary human launch path
- [decisions/shared-supabase.md](decisions/shared-supabase.md) — accepted shared Supabase boundaries (APP-009)
- [supabase-upgrade-isolation.md](supabase-upgrade-isolation.md) — future isolation checklist only
- [AGENTS.md](../AGENTS.md) — agent/developer commands and safety gates
- [bootstrap.md](bootstrap.md) — advanced initializer (blocked live apply)
- [app-deployment.md](app-deployment.md) — advanced guarded verification

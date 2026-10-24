# App quality & deployment verification (APP-006)

Independent half of deployment readiness: **CI quality gates**, a **manual/reusable deploy workflow that stays scaffold-only**, and an **offline-tested HTTPS health smoke CLI**.

This slice is **verification tooling and guarded orchestration**. A real deployment/rollback **adapter remains missing**. Fixture and synthetic-build secret scans are **not production proof**. This document does **not** claim completed live deployed verification.

DNS provider is **Cloudflare**, hosting is **Vercel**, database is the **one existing shared Supabase** project (SavedTube untouched).

## Initializer boundary (actual, current)

APP-005 is **in tree** (`scripts/app-new`, `bootstrap/`, offline plan/apply/resume). The shipped default adapter is `BlockedLiveApplyAdapter`. CLI apply without an injected live adapter returns `apply.live_integration_missing`.

| Path | Status |
| --- | --- |
| Offline plan + local scaffold | Implemented (APP-005) |
| Reviewed-digest apply/resume interface | Implemented (APP-005) |
| Fixture `RecordingApplyAdapter` orchestration tests | Implemented — **not** live proof |
| Live Vercel/DNS/cloud mutations | **Not integrated** — preserve block |
| This APP-006 deploy workflow | **Scaffold-only** — `if: false` on mutate jobs |
| Live deploy / rollback adapter | **Missing** — not expanded in this slice |

Do **not** treat fixture adapter success, metadata `externalVerification` flags, dry-run OK, or this workflow’s validation job as authorization to mutate production. Full operational automation is **not** claimed here.

## What APP-006 owns

| Artifact | Role |
| --- | --- |
| `.github/workflows/app-quality.yml` | Lint, typecheck, unified tests, build, enforced security validation — no production secrets |
| `.github/workflows/app-deploy.yml` | `workflow_dispatch` / `workflow_call`; `environment: production` is a **configured admin prerequisite**, not a guarantee of approval by keyword alone; apply/deploy **disabled** |
| `app/api/health/route.ts` | Public health JSON allowlist (`status`, `revision`, `service`); `ready` = **app process liveness** only |
| `scripts/verify-deployment.py` (+ `.sh`) | Strict HTTPS/loopback health probe with DNS pin / no env proxy |
| `scripts/run-all-tests.py` | Deterministic runner for **all** explicit suites (hyphen + underscore) |
| `scripts/health-route-http-smoke.py` | Build-and-start HTTP check of the real Next route |
| `tests/deployment_verification_test.py` | Probe safety + pinning + local smoke + fixture browser scan |
| `tests/workflow_safety_test.py` | Static workflow/docs gates |
| `package.json` `test` | Invokes `scripts/run-all-tests.py` (not discover `*_test.py` alone) |

Legacy `.github/workflows/deploy.yml` and `terraform.yml` mutate paths remain **disabled** (APP-002/APP-003). This card does not re-enable them.

## Separated evidence classes

| Class | What it proves | What it does **not** prove |
| --- | --- | --- |
| Fixture / unit checks | Offline probe safety, workflow static gates, browser-scan on supplied fixture bytes | Live production health, DNS, or deploy success |
| Synthetic built-artifact scan | Server-only fixture canaries absent from a CI `.next` JS chunk (`claimsFullProductionBuild=false`) | Production secret posture or live deploy safety |
| Genuine deployed health | HTTPS probe against an explicit trusted hostname + exact revision (when a real deploy exists) | Browser acceptance, shared-infra safety |
| Rollback (docs only) | Intended app-only Vercel deployment rollback procedure | Shared Terraform destroy / shared DB wipe (forbidden); **no adapter implemented** |

Never call a fixture scan, synthetic build scan, or local loopback smoke “actual production proof.”

## Quality workflow (no credentials)

`app-quality.yml` runs on pull requests and pushes with least-privilege `contents: read`. It does **not** use `pull_request_target` and does **not** inject `secrets.*` production credentials into PR code. Actions are major-pinned (`@vN`); untrusted input is not interpolated into shell.

Gates:

1. **Lint** — `npm run lint`
2. **Typecheck** — `npm run type-check`
3. **Unit tests** — `npm test` → `python3 scripts/run-all-tests.py` (every explicit suite)
4. **Build** — `npm run build` with **labelled synthetic public fixtures** (`NEXT_PUBLIC_*` only for client bundle needs)
5. **Health HTTP smoke** — real Next `GET /api/health` after build
6. **Enforced security validation** — deterministic offline suites (via the unified runner) plus scan of a synthetic-built `.next` JS artifact with **fixture-only** canaries (`claimsFullProductionBuild` stays false). Tracked `.secrets.baseline` is **not** executed as a live secret scan here.
7. **Informative npm audit** — clearly **non-gating** advisory output; not the enforced security step

Node version matches `package.json` `engines.node` (`24.x`). Install uses **`npm ci`** (lockfile). Python deps use pinned `requirements-bootstrap.txt`.

### Mandatory Postgres in CI

APP-003 disposable Postgres integration uses harness-supported modes: `0` (skip), `1` (mandatory), `auto` (run when podman/image ready). Quality CI sets **`APP003_PG_TESTS=1`**, provisions podman + `APP003_PG_IMAGE`, and **fails** if integration is skipped. Local developers may keep `auto`.

### Frontend build and production keys

`next.config.js` forwards `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` into the client bundle. A template build does **not** require genuine production keys when those public fixtures are supplied (as in CI). Server-only secrets must never be injected for quality builds.

### Browser secret-exclusion

APP-004 `secrets-preflight` can stream **supplied** artifact bytes (fixture or built bundle). Missing paths ⇒ `unverified`/`skipped`, never `ok`. `claimsFullProductionBuild` is always false. APP-006 tests exercise fixtures under `tests/fixtures/secrets_preflight/` and CI may scan a synthetic `.next` chunk — that is **not** production build proof.

## Deploy workflow (explicit approval, apply blocked)

`app-deploy.yml` supports:

- `workflow_dispatch` (manual)
- `workflow_call` (reusable)

It does **not** trigger on `push` or `pull_request`. Jobs may reference the GitHub **`production`** environment. **`environment: production` does not by itself guarantee human approval** — reviewers/protection rules are a **configured repository admin prerequisite**. Passing the placeholder job only records approval context when those rules exist; it does **not** deploy.

Required input **`reviewed_ref`**: full 40-character commit SHA. Checkout uses `ref: ${{ inputs.reviewed_ref }}` (immutable reviewed ref — not a floating branch tip alone).

### Gate status (honest / scaffold-only)

| Step | Status |
| --- | --- |
| Immutable `reviewed_ref` format + checkout | Allowed (safe) |
| Input / free-tier validation | Allowed (safe) |
| Distinct Vercel project id input present | Metadata check only |
| Production environment job | Records approval context when rules are configured — **does not deploy** |
| Apply / Vercel deploy | **`if: false`** — `blocked_live_integration_missing` |
| Scoped migrations | **`if: false`** — APP-003 harness only after live adapter; never `supabase db push` |
| Post-deploy smoke | **`if: false`** — needs a real deployment |
| Rollback adapter | **Not implemented** |

Gate output today: `blocked_live_integration_missing` (scaffold-only). Do **not** treat boolean flags or fake hash-checks as live evidence or deploy authorization.

### Free-tier compatibility

`FREE_TIER_COMPATIBLE=true` / `PAID_FEATURES_ENABLED=false`. When `free_tier_only` is true, paid-tier features must remain off.

### Integration with APP-005 (coordination only)

APP-006 does **not** invent new deploy flags on `app-new`. Existing contract:

```bash
./scripts/app-new.sh plan|apply|resume ...
# apply with shipped adapter → apply.live_integration_missing
```

**Inputs (operator / future live adapter):**

- Distinct generated `vercel_project_id` (must not collide with SavedTube)
- Approved hostname FQDN (Cloudflare DNS)
- Expected revision + immutable `reviewed_ref`
- Reviewed plan digest / ownership artifacts (existing plan guard — not uploaded raw here)
- Scoped migration intent (APP-003 harness), never unattended shared DB push

**Outputs (this workflow today):**

- Validation summary + explicit `blocked_live_integration_missing`
- No deploy URL, no production evidence claim

Artifact policy: **do not** upload raw Terraform plans, `.env` files, or secret-bearing logs.

## Health endpoint

`GET /api/health` is **force-dynamic / no-store**. It returns only:

```json
{ "status": "ready", "revision": "<immutable sha>", "service": "app" }
```

or, when revision cannot be derived:

```json
{ "status": "not_ready", "revision": "unavailable", "service": "app" }
```

(`HTTP 503` when not ready.)

**`ready` means app process liveness** (this route handler is serving). It is **not** database, Auth, DNS, or provider readiness.

Revision selection:

1. `VERCEL_GIT_COMMIT_SHA` when it is a full 40-character hex SHA (primary on Vercel)
2. Otherwise `APP006_LOCAL_TEST_REVISION` when it is a full 40-character hex SHA (explicit server-side local/CI test only)

Do **not** prefer `NEXT_PUBLIC_DEPLOYMENT_REVISION` (or any other public env) over the platform SHA. Missing/invalid revision ⇒ `not_ready`.

## Smoke verification (`verify-deployment`)

```bash
./scripts/verify-deployment.sh \
  --url "https://arena.example.com/api/health" \
  --expected-hostname "arena.example.com" \
  --expected-revision "<sha>"
```

Local disposable server (**explicit loopback HTTP only**):

```bash
./scripts/verify-deployment.sh \
  --url "http://127.0.0.1:3000/api/health" \
  --expected-hostname "127.0.0.1" \
  --expected-revision "<sha>" \
  --allow-loopback-http
```

Safety properties:

- Strict hostname match; production HTTPS only on port **443** with path exactly **`/api/health`**
- Redirects rejected (not followed)
- DNS resolved **once** per request; TCP/TLS is **pinned** to those validated addresses (no urllib re-resolution; environment proxies are not used)
- TLS certificate validation + SNI and HTTP `Host` remain bound to the original hostname
- Mixed public/non-public resolved address sets are rejected
- Arbitrary hostnames that resolve to loopback are rejected even with `--allow-loopback-http` (explicit loopback hosts only: `127.0.0.1`, `::1`, `localhost`)
- Finite bounded timeout; bounded header/body wire reads with total request deadline; stdlib HTTP framing for `Content-Length` and `Transfer-Encoding: chunked`; JSON schema allowlist; exact revision; `status=ready`
- Rejects userinfo, shell metacharacters; invalid ports fail with generic codes (no traceback / input leak)
- IPv6 literals use correct bracket formatting
- Production URL/evidence absent ⇒ **fail closed**

### DNS timeout limitation (stdlib)

CPython `socket.getaddrinfo` has **no portable cancellation**. APP-006 bounds wait with a worker-thread join timeout and fails closed if the join expires, but a stuck resolver thread may linger until process exit. Do not claim stronger DNS wall-time enforcement than that.

### HTTP probe vs browser verification

Passing `verify-deployment` is an **HTTP probe**, not browser acceptance. This repository does not ship Playwright/Puppeteer as a direct toolchain dependency for APP-006, so **browser acceptance remains pending**. Do not claim browser verification from HTTP success. `--require-browser-evidence` fails closed when browser evidence is absent.

SSRF-oriented tests include connection pinning and proxy isolation; they are **not** solely mock-resolver assertions.

## App-only rollback (documentation)

Until a reviewed live adapter and APP-007 wire live deploy (**adapter not present in this slice**):

1. Do **not** use template `terraform destroy` / shared DB reset (disabled).
2. App-only rollback means promoting/restoring the **previous immutable Vercel deployment** for the **distinct app project** (not SavedTube), after human review.
3. Database: reverse only **app-scoped** migrations via the APP-003 harness against an approved target — never shared-project wipe.
4. DNS/email/zone and shared infrastructure destruction remain out of scope; no unapproved remote operations are implemented here.

## Local tests

```bash
# Prefer existing project venv (do not auto-install in constrained workers)
.venv/bin/python scripts/run-all-tests.py
# Optional: health HTTP smoke after npm run build
APP006_LOCAL_TEST_REVISION=abcdef0123456789abcdef0123456789abcdef01 npm run test:health-http
npm run verify:deployment -- --help
```

## Remaining live gates (not done here)

- Reviewed live apply / deploy / rollback adapter replacing `BlockedLiveApplyAdapter` (clears `apply.live_integration_missing`)
- Explicit production deploy enablement after that hand-off (flip `if: false` only under review)
- APP-007 / live deployment + HTTPS domain evidence on the real hostname
- Browser acceptance on the live site
- Coordinated scoped production migrations with reviewed checksums

No model calls are required for these checks.

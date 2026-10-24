# App bootstrap contract (APP-001 + APP-002 + APP-003 + APP-004 + APP-005)

Pinned template revision: `7f21f51209659a77fd56b19e6116f388e783d93d`

Branches: `feat/app-bootstrap-contract` (APP-001/002), `feat/shared-database-isolation` (APP-003), `feat/credential-preflight` (APP-004), `feat/app-initializer` (APP-005)

Scope: reusable **non-secret** app configuration (APP-001), shared-project infrastructure hardening + offline plan guard (APP-002), reusable shared-database migration/access isolation (APP-003), secure credential preflight/binding (APP-004), and configuration-driven initializer plan/apply/resume (APP-005 offline + fixture adapters).
Not in scope: product UI / live SavedTube cutover, claiming live production credential readiness, or **verified live** Vercel/DNS/Terraform apply (deployment gate — see APP-005).

## What this contract is

A closed-field JSON document (`app.config`) describes one app’s bootstrap metadata:

| Field | Purpose |
| --- | --- |
| `schemaVersion` | Contract version (`1`) |
| `app.slug` | Safe lowercase app slug |
| `repository.owner` / `repository.name` | Repo metadata only (not proof of ownership) |
| `hostname` | FQDN only (no URL, path, port, wildcard) |
| `database.mode` | Must be `existing-project-shared` |
| `database.namespace` | Per-app schema inside the **single** shared Supabase database |
| `deployment.identity` | Per-app deployment identity (separate Vercel project key) |
| `deployment.stateKey` | Isolated Terraform/state key for this app |
| `dns.choice` | `unresolved` \| `cloudflare` \| `cloudfront` |
| `secretAliases` | Uppercase **alias names** (never secret values) |
| `localConflictInventory` | Locally supplied known identifiers for **other** apps |
| `externalVerification` | Live verification status (blocks ready until verified) |

Validation enforces schema, closed fields, and cross-field invariants. It does **not** call remote APIs or prove account ownership.

## Deployment-ready means metadata readiness only

`--mode deployment-ready` checks that the config document records resolved DNS choice and verified/complete `externalVerification` flags. That is **metadata readiness**, not proof that remote facts are true.

Callers **must independently verify** live account permissions, shared-database preservation, secret-source resolution, DNS ownership/propagation, and live conflict discovery **before any apply**. Passing deployment-ready validation is never authorization to mutate infrastructure.

## Audit inventory (filenames only)

### Workflows / automation

- `.github/workflows/terraform.yml` — plan/apply/destroy via `workflow_dispatch`; push/PR on `terraform/**`
- `.github/workflows/deploy.yml` — automatic Supabase migrations on push to `main`/`master`
- `.github/workflows/initial-setup.yml`
- `.github/workflows/setup.yml`
- `.github/workflows/template-setup.yml`
- `.github/workflows/repository-setup-trigger.yml`
- `.github/scripts/validate-workflows.sh`
- `.github/scripts/validate-workflows-simple.sh`
- `.github/template.yml`

### Terraform / DNS / email / destructive hooks

- `terraform/main.tf`
- `terraform/variables.tf` — includes `enable_supabase_project_deletion` (default **true** in workflow), `enable_email_routing`, `create_cloudflare_zone`
- `terraform/modules/dns/` — DNS records for app hostname
- `terraform/modules/email_routing/` — Cloudflare email routing rules
- `terraform/modules/vercel/` — Vercel project + env wiring
- `terraform/modules/supabase/main.tf` — destroy-time `null_resource` CLI project deletion when enabled
- `terraform/modules/supabase/create_project.tf.example`
- `scripts/quick-start.sh` — local plan/apply/destroy helper
- `package.json` scripts: `terraform:apply`, `terraform:destroy`, `db:migrate`, `db:reset`

### Tracked template-state artifacts (do not read values here)

- `template.config.json`
- `platforms.json`
- `supabase/.temp/cli-latest`
- `supabase/.temp/gotrue-version`
- `supabase/.temp/pooler-url`
- `supabase/.temp/postgres-version`
- `supabase/.temp/project-ref`
- `supabase/.temp/rest-version`
- `supabase/.temp/storage-version`
- `supabase/config.toml`
- `supabase/migrations/20240101000001_initial_schema.sql`
- `supabase/seed.sql`
- `.secrets.baseline`

### Env / secret **NAMES** (inventory only; no values)

From `env.example` and workflow/docs references:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `NEXT_PUBLIC_SITE_URL`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `NEXTAUTH_SECRET`
- `NEXTAUTH_URL`
- `CLOUDFLARE_API_TOKEN`
- `VERCEL_API_TOKEN`
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_PROJECT_ID`
- `SUPABASE_DATABASE_PASSWORD`
- `DOMAIN_NAME`
- `TERRAFORM_STATE_PASSWORD`
- `APP_URL`
- `SITE_URL`
- `GITHUB_REPO`
- `VERCEL_PROJECT_NAME`
- `VERCEL_TEAM_ID`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_ZONE_NAME`
- `SUPPORT_EMAIL_DESTINATION`
- `CONTACT_EMAIL_DESTINATION`
- `ENABLE_EMAIL_ROUTING`
- `CREATE_CLOUDFLARE_ZONE`
- `ENABLE_SUPABASE_PROJECT_DELETION`

Secret aliases in `app.config` reuse these **names** and must resolve from the approved original secure source used by SavedTube. Do **not** retrieve secrets from GitHub for this task, and never put plaintext credentials in config.

## Shared database and isolation rules

- Exactly **one** existing Supabase project/database must be preserved.
- Each app gets its own `database.namespace` (tables/schema), never the reserved/`shared` namespaces.
- Each app gets a separate Vercel project identity and isolated `deployment.stateKey`.
- Config mode is fixed to `existing-project-shared`. Creating or deleting the shared database is forbidden by this contract.

## Conflict inventory: local vs live

| Source | What it covers | Status in APP-001 |
| --- | --- | --- |
| `localConflictInventory` | Operator-supplied known slugs, namespaces, hostnames, deployment identities, state keys for other apps | Enforced by validator |
| Live remote discovery | Actual Vercel projects, DNS records, DB schemas, Terraform state keys in the account | `externalVerification.liveConflictDiscovery` must become `complete` before deployment-ready |

Metadata match against local inventory is **not** live discovery.

## DNS choice

- `unresolved` — allowed for **draft** configuration; **fails** `--mode deployment-ready`
- `cloudflare` / `cloudfront` — explicit manual choice recorded in config; ownership/propagation still require live preflight (APP-005+)

## Validation setup (isolated venv; reproducible)

`jsonschema` is **not** assumed to be installed system-wide. Use a repo-local venv and the pinned lockfile:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt
```

Do **not** `pip install --user` or install into the global interpreter.

## Validation command (implemented)

```bash
# Draft (schema + invariants; unresolved DNS allowed)
./scripts/validate-app-config.sh --config examples/app.config.example.json --mode draft

# Deployment-readiness preflight (metadata flags only; see section above)
./scripts/validate-app-config.sh --config path/to/app.config.json --mode deployment-ready

# Tests (prefer the same venv)
.venv/bin/python tests/config-validation_test.py
.venv/bin/python tests/secrets_preflight_test.py
```

The shell wrapper prefers `.venv/bin/python` when present.

Unsafe inputs (shell metacharacters, control characters, embedded credential patterns) fail validation. Schema and CLI errors use schema-controlled field references and generic reasons — they omit instance values and arbitrary unknown property names. Missing or invalid schema files fail cleanly without tracebacks.

Malformed JSON roots (null/list/scalar) and wrong types for nested objects (`database` / `app` / `dns` / …) return schema errors and do not run cross-field invariants that would crash on `.get`.

## APP-002 — shared Supabase / per-app ownership hardening

Goal: make this reusable template safe when many apps share **one existing** Supabase project. That project is a **reference only**.

### What changed in Terraform

- Removed Supabase create/delete/reset/destroy hooks and secret-bearing `null_resource` / `local-exec` triggers.
- Each app owns only its distinct **Vercel project** (`vercel_project_name` / `deployment.identity`) and an **explicitly approved hostname**.
- Shared DNS **zone** creation/ownership removed (`create_cloudflare_zone` must stay `false`).
- Mail routing and root/www/api DNS defaults removed from the happy path; `enable_dns` and `enable_email_routing` default **false**.
- DNS fails closed when `dns_provider=unresolved` if DNS is enabled; **CloudFront is not implemented** here — do not treat Cloudflare resources as covering `dns.choice=cloudfront`.
- `deployment_state_key` is **required** with **no shared default** (prevents one app clobbering another’s state).
- Vercel project/domain use `lifecycle.prevent_destroy`.

### Dangerous entrypoints (disabled, not left as bypasses)

| Path | APP-002 status |
| --- | --- |
| `.github/workflows/terraform.yml` apply / destroy | `if: false` — disabled until APP-005 enforcement |
| `.github/workflows/deploy.yml` migrations | `if: false` — **stays disabled** after APP-003 (no unattended main path) |
| `package.json` `terraform:apply` / `destroy`, `infra:apply` / `destroy`, `db:reset` / `db:migrate` | exit 1 stubs (`db:migrate:app` is the explicit harness only) |
| `scripts/quick-start.sh` apply / destroy | exit 1 |

### Offline plan ownership guard (implemented)

```bash
# After: terraform show -json tfplan > tfplan.json
./scripts/validate-terraform-plan.sh \
  --plan tfplan.json \
  --ownership examples/plan-ownership.example.json
```

Fail-closed checks:

- Closed ownership schema: `resourceBindings` maps every allowed Terraform address → supported type; unsupported caller `allowedResourceTypes` are rejected
- Supported type whitelist only (`vercel_project`, `vercel_project_domain`, `vercel_project_environment_variable`, `cloudflare_record`)
- Exact action tuples only: `create`, `update`, `no-op` (delete/replace/read/arbitrary arrays rejected)
- Ownership checked on **before and after** for update/no-op (renaming an unrelated resource cannot pass)
- Cloudflare: `zoneId` / record name / type / target must match approved `cloudflare` config
- Vercel: `teamId` / `projectId` linkage required; known IDs for existing resources; newly computed `project_id` must reference the bound `vercel_project` in configuration expressions or is rejected pending APP-005
- Unexpected addresses rejected even when the type is allowlisted
- Malformed nested `change` / `after_unknown` fails closed (no crash)
- Generic redacted errors only — never echo raw plan addresses, types, or attribute values

This guard is **not** a full deploy initializer.

### Warning for existing template users (immutable plan / state)

If you already applied the **pre-APP-002** template, Terraform state may still track shared zone, www/api records, email routing, or Supabase deletion hooks.

**Do not** casually apply a plan that removes those resources from configuration: Terraform will try to **destroy** them in the shared account.

Required before any apply against existing state:

1. Produce an immutable reviewed plan (`terraform plan -out=…` + JSON) and run the ownership guard.
2. Manually review **state detachment** for any shared resources that must remain (zone, mail, shared DB). Live `terraform state rm` / import / migrate against real resources is **out of scope** for APP-002 and must be a deliberate operator action after review — this repo does not perform it.
3. Refuse apply until APP-005 remote account/permission verification exists.

### Plan / apply / resume contract (APP-005 — implemented offline; live apply gated)

APP-002 disables unguarded apply/destroy. APP-005 implements a configuration-driven initializer with durable manifests, reviewed plan digests, and an explicit **live-integration missing** gate.

| Capability | Status |
| --- | --- |
| Offline `plan` (validate config, pin template, scaffold, secret-free plan artifact) | **Implemented** |
| Reviewed-plan-bound `apply` / `resume` interface | **Implemented** |
| Idempotent local re-plan / fixture-adapter re-apply | **Implemented** (tests) |
| Interrupted resume without duplicate adoption-by-name | **Implemented** (fixture adapter tests) |
| Default shipped apply adapter performing live cloud mutations | **Not integrated** — `apply.live_integration_missing` |
| Live Vercel/DNS/account verification against real providers | **Unverified** — deployment gate |

Honest limitation: fixture `RecordingApplyAdapter` tests prove orchestration contracts only. They are **not** evidence that live adapters work. Treat `gates.liveApplyIntegrated: false` and `apply.live_integration_missing` as a **deployment gate** until a reviewed live adapter lands (APP-006+).

Pinning: `TEMPLATE_REVISION` markers are **never sufficient alone**. The controlling git repository must contain the pinned commit object (`git cat-file`), and materializable template contents are hashed into scaffold/plan digests.

## APP-003 — shared-database migration / access isolation

Goal: when multiple apps share **one** existing Supabase/Postgres database, each app can migrate and query only inside its own namespace, without touching Supabase CLI / SavedTube migration history, and without claiming isolation from a project-wide admin key.

### What is implemented

| Piece | Role |
| --- | --- |
| `db/shared_isolation/provision/00_disposable_test_baseline.sql` | **Disposable local test only**: Supabase-like roles, authenticator memberships, `REVOKE CREATE ON public FROM PUBLIC` |
| `db/shared_isolation/provision/01_control_and_roles.sql` | **Trusted reusable provisioning**: control schema, registration, ledger RLS, Realtime allowlist, scoped SECURITY DEFINER guard |
| `scripts/migrate_app_namespace.py` (+ `.sh`) | App migrator harness: ordered IDs, SHA-256 checksums, transaction-scoped advisory lock + retry, idempotent re-apply |
| `db/shared_isolation/fixtures/app_{alpha,beta}/` | Two fixture apps demonstrating denial, sanitized projection, private-state exclusion from Realtime |
| `db/shared_isolation/fixtures/preexisting/` | Pre-existing tables + mock `supabase_migrations` history that must remain untouched |
| `tests/shared_db_isolation_test.py` | Unit + disposable local Postgres integration (podman) |

### Ledger and lock model

- Ledger table: `app_migration_control.ledger` keyed by `(app_namespace, migration_id)` with `checksum_sha256`.
- App migrators may **SELECT** their own rows (RLS bound to `session_user` + `registered_apps`, not a user-settable GUC) and **record** via `record_applied` only; direct INSERT/UPDATE/DELETE are denied.
- Transaction-scoped advisory locks (`pg_try_advisory_xact_lock`) are held until the apply transaction commits or rolls back; retry/backoff on contention.
- Idempotence: same id + checksum → skip; same id + different checksum → **abort**; new id below ledger max → **abort** (out-of-order).
- Each migration file is read once into immutable bytes shared by checksum and execution (no TOCTOU re-read).
- The harness **never** reads `DATABASE_URL` / `SUPABASE_DB_URL` from the environment. Prefer `--database-url-file` / `--database-url-stdin` so credentials are not visible in process listings; `--database-url` is allowed only with `--local-disposable-only` (tests) or reviewed `APP003_ALLOW_NON_LOCAL=1`. CLI errors are redacted.

### Privilege model (not regex)

Arbitrary SQL cannot be made safe by regex. Isolation is:

1. Restricted `{namespace}_migrator` LOGIN roles (CREATE only in own schema; per-role `REVOKE CREATE` on `public` only — never silent global `REVOKE … FROM PUBLIC`).
2. Transaction boundaries in the harness (see trusted SQL limits below).
3. Trusted provisioning separated from app migration files and from disposable test baseline.
4. Event trigger rejecting `SECURITY DEFINER` only for registered migrator sessions and registered app namespaces (unrelated preexisting owners may still create approved definer routines).
5. Realtime membership only through `add_to_realtime`, which checks a bootstrap-managed `realtime_allowlist` (app migrations cannot extend the allowlist or publish alternate tables).
6. `provision_app` fail-closed: incompatible `PUBLIC` CREATE baseline → operator prerequisite error; unknown existing roles/schema owners → collision error (no password reset of strangers); namespace length capped so `{namespace}_migrator` fits in 63-byte identifiers.
7. Reusable provisioning does **not** alter shared `authenticator` role memberships.

### Supabase roles and JWT boundary (explicit)

| Role | Meaning in this contract |
| --- | --- |
| `anon` | Untrusted client role; only what RLS + grants allow |
| `authenticated` | Logged-in client; fixture policies require JWT claims `request.jwt.claim.sub` (owner) **and** `request.jwt.claim.app_namespace` (app) |
| `service_role` | **Privileged** (`BYPASSRLS`). It can read private game state. That is an operator/admin escape hatch, **not** an app-isolation guarantee. Migrations must not use a project-wide admin/service key to “prove” cross-app isolation |

JWT app/owner authorization is an application boundary on top of schema grants. Schema privileges stop cross-app DDL/DML from migrator roles; RLS + sanitized views stop cross-user leakage inside an app for `anon`/`authenticated`.

### Sanitized projection and Realtime

- Private table: `game_private_state` (never added to `supabase_realtime`).
- Public events may be published via the helper.
- View `game_sanitized_projection` uses `security_barrier` + `security_invoker` and **omits** `secret_payload` (exposes only `has_secret`).

### Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt

# Disposable local integration (podman + postgres image; loopback only)
.venv/bin/python tests/shared_db_isolation_test.py

# Manual harness: prefer file/stdin so the URL is not on argv
printf '%s\n' 'postgresql://app_alpha_migrator:…@127.0.0.1:55432/app003' \
  > /tmp/app003-db-url
./scripts/migrate_app_namespace.sh \
  --database-url-file /tmp/app003-db-url \
  --namespace app_alpha \
  --migrations-dir db/shared_isolation/fixtures/app_alpha \
  --local-disposable-only
```

Integration tests set `APP003_PG_TESTS=auto` (default): run when `podman` can use `APP003_PG_IMAGE` (default `docker.io/library/postgres:16-alpine`). Set `APP003_PG_TESTS=0` to skip DB integration; unit tests still run.

### Ledger coordination limits (honest)

- **SavedTube code/history was not inspected** in APP-003. The preexisting fixture only simulates a shared history table.
- Advisory locks exclude **other writers that use the same lock protocol**. They cannot promise exclusion against legacy `supabase db push`, manual superuser DDL, or other tools that ignore the ledger.
- All future writers must adopt this lock/ledger **or** explicitly serialize maintenance windows.
- **Trusted SQL transaction-control limitation:** migration files are reviewed operator SQL executed as a multi-statement script. The harness does **not** sandbox `COMMIT` / `ROLLBACK` / `SET TRANSACTION` inside those files; a migration that issues transaction-control statements can end the harness transaction early. Treat transaction control in migration SQL as forbidden unless the apply path is redesigned around single-statement execution.
- Live shared-project compatibility remains **pending** operator review; fixture pass ≠ production sign-off.
- Operator prerequisite before reusable `provision_app`: `PUBLIC` must already lack `CREATE` on schema `public` (apply that revoke in a reviewed change window — reusable provisioning will not rewrite shared public grants).
- Production apply (when allowed later) requires backup/recovery readiness, immutable SQL + checksum approval, and must not re-enable unattended main deploy migrations.

### Example identity in docs

Use generic `example-owner` / `example.com` in documentation and examples (already used by APP-001 config examples).

## APP-004 — secure credential preflight and binding

Goal: resolve SavedTube credential **values** only from an authorized secure source supplied by a trusted launcher; report **presence** and **permission capabilities** (verified / unverified / blocked) without ever emitting secret values. Apps may reuse suitable Vercel token/scope, shared Supabase config, and an **optional** model key — but **must** use a distinct Vercel project ID.

### What is implemented

| Piece | Role |
| --- | --- |
| `scripts/secrets-preflight.py` (+ `.sh`) | Allowlisted alias resolution from caller-provided mapping; fixed-code JSON report |
| `examples/credential-binding.example.json` | Reusable binding plan (alias names / generic `appId` targets only; no plaintext env export) |
| `env.example` | Separates public `NEXT_PUBLIC_*` allowlist from server-only secrets |
| `.github/workflows/terraform.yml` | Fork-PR guard: secret-bearing plan skipped for forks |
| `tests/secrets_preflight_test.py` | Missing/invalid/redaction/collision/fork/public/browser/CLI regression cases |

### Source interface (bounded)

- **Allowed:** `CallerProvidedEnvSource` — in-memory mapping of allowlisted alias → **string** value from a trusted launcher. `None`/empty ⇒ missing; non-string types rejected (no `str(None)` coercion).
- **CLI value transports (executable path):** `--values-stdin` (pipe JSON object) or `--values-env` (read **only** allowlisted alias names from the process environment — no environment dump). **`--values-json` is fixtures-only** (path must resolve under `tests/fixtures/`); never require secrets-on-disk for real runs.
- **Consumes** alias **names** from `app.config` `secretAliases`. **Remapping is prohibited:** each alias value must equal its slot key (schema + validator + preflight). Optional APP-004 keys: `VERCEL_PROJECT_ID`, `SAVEDTUBE_VERCEL_PROJECT_ID`; `OPENAI_API_KEY` is **optional**.
- **Disallowed:** arbitrary shell execution, automatic `.env`/home-store probing, environment dumps, persisting credentials, overwriting shared SavedTube secrets.
- Values remain in **process memory** only. Artifacts contain presence + capabilities — no raw request URLs, response bodies, headers, exception messages, class names, or secret substrings. Probe failures emit fixed codes such as `probe.transport_error` only.

### Identity and capability rules

- Supabase refers to the **existing shared** project; `SUPABASE_SERVICE_ROLE_KEY` / DB password / access token are **server-only**.
- Explicit distinct Vercel check: SavedTube existing ID vs prospective app ID. **Missing app Vercel project ID ⇒ blocked** (not success) until an initializer binds a new project. This task does **not** create that project.
- Token **read** vs **write** are independent. A successful GET must never claim write ability.
- `OPENAI_API_KEY` is optional; presence does **not** prove Codex or Decisions entitlement.
- Public `NEXT_PUBLIC_*` configuration is a **strict key allowlist**, and public **values** must not equal any resolved server-only secret value.
- Preview credentials default **disabled / least privileged**; production secrets are **never** injected into fork workflows.
- Reports expose `localMetadataOk` separately from **`productionReady: false`** and **`deploymentAuthorized: false`**. Unverified capabilities never authorize deployment. Production credential capabilities remain explicitly **`unverified`** until an authorized read-only supervisor preflight runs. GitHub secret-**list** access is never evidence of secret-**value** access.

### Network probes (opt-in)

If enabled: fixed official allowlisted hosts only (`api.vercel.com`, `api.supabase.com`, `api.openai.com`), bounded timeout, TLS expected via transport, redirects rejected, schema-validated responses, tested with fixtures. This worker does **not** perform live provider calls; missing authorization/account facts fail closed. Exception paths never embed `str(exc)`, class names, or upstream bodies.

### Browser secret-exclusion check

Streams **supplied** artifact bytes with overlap (or rejects oversize visibly). Missing/empty/unreadable paths fail closed. **No supplied paths ⇒ `unverified`/`skipped`, never `ok`.** `claimsFullProductionBuild` is always false; `evidenceKind` is a caller label for supplied-artifact scan scope only — never proof of a full production build. Reports never print artifact contents or credential-bearing paths.

### Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt

# Fixture / trusted-launcher values only — never point at real home credential stores
.venv/bin/python tests/secrets_preflight_test.py

# Preferred: stdin transport (no secrets-on-disk)
./scripts/secrets-preflight.sh \
  --aliases-json path/to/aliases.json \
  --values-stdin \
  --app-id example-app \
  --format json < /path/from/trusted/launcher/pipe

# Or allowlisted env names only (no dump)
./scripts/secrets-preflight.sh \
  --aliases-json path/to/aliases.json \
  --values-env \
  --app-id example-app \
  --format json

# Fixtures-only on-disk values (tests/fixtures/** only)
./scripts/secrets-preflight.sh \
  --aliases-json tests/fixtures/secrets_preflight/cli_aliases.json \
  --values-json tests/fixtures/secrets_preflight/cli_values.json \
  --format json
```

### Files introduced by APP-004

- `scripts/secrets-preflight.py` / `scripts/secrets-preflight.sh`
- `examples/credential-binding.example.json`
- `tests/secrets_preflight_test.py`
- `env.example` (public vs server-only separation)
- `docs/bootstrap.md` (this section)
- Minimal optional alias keys on `app.config.schema.json` (remapping prohibited)
- Bounded fork-PR secret safeguard in `.github/workflows/terraform.yml`

## Blocking preflight items (explicit)

These are **not** verified as live facts in APP-001/APP-002. Deployment-ready only requires the corresponding **metadata flags**. Callers must still perform independent live verification before apply:

1. Live account/permission verification (`externalVerification.accountPermissions`) — APP-005 **blocks** default apply with `apply.live_integration_missing` until a live adapter exists; metadata `verified` is still never proof
2. Shared-database preservation guard (`externalVerification.sharedDatabaseGuard`) — live check still required before any real apply
3. Approved secret-source verification for aliases (`externalVerification.secretSource`) — APP-004 provides local presence/binding preflight only; live supervisor verification remains required
4. Live conflict discovery (`externalVerification.liveConflictDiscovery`) — fixture inventory only in APP-005 tests
5. Resolved DNS choice (`dns.choice` ≠ `unresolved`) — APP-005 apply fails closed on unresolved / cloudfront

## APP-005 — configuration-driven initializer

Goal: repeatable plan → human-reviewed digest → apply/resume for a new app tree, without lifecycle-owning the shared Supabase database, without embedding secrets, and without claiming live provider success when only offline/fixture paths are available.

### What is implemented

| Piece | Role |
| --- | --- |
| `bootstrap/` package | Plan/apply/resume engine, path safety, locks, atomic manifest, adapters |
| `bootstrap-manifest.schema.json` | Closed-field durable resume state (IDs/hashes/stages only) |
| `scripts/app-new.py` (+ `.sh`) | CLI: `plan` / `apply` / `resume` with fixed redacted codes |
| `tests/fixtures/bootstrap/` | Minimal template + two distinct app configs |
| `tests/app_initializer_test.py` | Offline generation + fixture-adapter regression suite |

### Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt

# Offline plan (local scaffold + reviewed plan digest; no remote mutations)
./scripts/app-new.sh plan \
  --config tests/fixtures/bootstrap/configs/agent-arena.cloudflare.json \
  --template-revision 7f21f51209659a77fd56b19e6116f388e783d93d \
  --template-root tests/fixtures/bootstrap/template \
  --output-root /tmp/app005-out \
  --state-root /tmp/app005-state \
  --format json

# Apply requires the exact reviewed plan digest. Default adapter blocks live apply.
./scripts/app-new.sh apply \
  --config tests/fixtures/bootstrap/configs/agent-arena.cloudflare.json \
  --template-revision 7f21f51209659a77fd56b19e6116f388e783d93d \
  --state-root /tmp/app005-state \
  --plan-digest 'sha256:…' \
  --format json

# Tests
.venv/bin/python tests/app_initializer_test.py
```

### Safety contracts retained

- Shared Supabase DB: `reference_only` in plan intents — create/delete forbidden.
- Isolated `deployment.identity` / `deployment.stateKey` / `database.namespace` / hostname.
- Secret **aliases** only in plans/manifests/logs; scaffold excludes `.env`, `.git`, `node_modules`, `state/`, `temp/`, `supabase/.temp`, `platforms.json`, and related paths.
- Stale/tampered config hash or plan digest → fail closed.
- Symlink / path-escape / unexpected overwrite → fail closed.
- Exclusive per-app lock; contention → `runner.concurrent_rejected`.
- Dry-run revalidates reviewed bindings **and** on-disk generated-file hashes under the app lock; it **does not** mark the durable manifest `completed`.
- Workflows copy to `.github/workflows.pending/` (never auto-enabled).
- Unresolved DNS and CloudFront block apply; Cloudflare is the only DNS choice that may proceed past the DNS gate (live DNS still unverified).

### APP-005 safety corrections (post source review)

These are **local/offline** fail-closed behaviors. They do **not** prove live provider permissions or production readiness.

| Correction | Supported guarantee | Remaining limitation |
| --- | --- | --- |
| Reviewed digest ↔ content | `planDigest` includes `contentSnapshotDigest` + `scaffoldDigest` from actual template/scaffold bytes; content changes with an unchanged declared revision invalidate the reviewed digest | Caller-supplied digest/revision strings are never trusted as content proof; the engine hashes what it reads |
| Snapshot provenance / dirty policy | Git commit object required; when materializable paths exist in the pinned commit, on-disk bytes must match those blobs (`template.content_dirty` on mismatch/mixed provenance). Offline fixture trees with **zero** pin paths bind as `contentProvenance: offline_bound` via content digest only | Offline fixtures are not git-tree-verified; `require_git_content_match=True` refuses offline-bound mode. Full-tree materialization from the pin is not implemented |
| Symlink-safe state/lock/temp | `ensure_owned_directory` validates **every lexical ancestor** with `is_symlink` before any create; lock/manifest opens use `O_NOFOLLOW` / exclusive temp create. Nested ancestors behind an existing real directory reached via a symlink parent are rejected without resolving the link away. External canaries must remain unchanged | Best-effort POSIX `nofollow` — **not** a full hostile concurrent filesystem sandbox. TOCTOU against a privileged concurrent attacker, hostile mount namespaces, and platforms without `O_NOFOLLOW` remain out of scope |
| Interrupted checkpoint preserve | Compatible re-plan keeps `resourceIds` / remote `ensure_*` checkpoints; strips account/capability/inventory evidence so the current digest is rechecked before mutation; incompatible config/revision → `checkpoint.resource_reset_forbidden`. Malformed existing manifests fail closed (`manifest.schema_invalid`) instead of being dropped | Does not contact providers to re-verify remote ownership; fixture adapters only |
| Generated-file edit detect | Owned scaffold files record content hashes; apply/resume/dry-run/completed noop re-read the generated set under the app lock and recompute `scaffoldDigest` against the reviewed plan. Edits/deletions/symlink replacements → `path.generated_content_modified` / `path.symlink_rejected` (non-destructive). Unchanged content re-plans idempotently | Ownership sidecar (`scaffold.owned.json`) is trusted local state under the private state root; validation refuses empty/mismatched sidecars rather than minting a new approving digest |
| Capability binding | Mutations require a `CapabilityReport` with required capabilities `True` **and** matching `app_slug` / `config_hash` / `plan_digest` / `provider` / `operation`, established in the **current** apply/resume invocation. Completed `verify_capabilities` from a prior digest is never treated as live proof | Fixture `RecordingApplyAdapter` reports are **not** live permission proof. Shipped CLI still uses `BlockedLiveApplyAdapter` |
| Completed resume bindings | `resume.noop` / `apply.noop` / `plan.noop` share the locked binding + generated-file validation path as apply (no unlocked fast path) | Same local binding surface; does not prove remote resource still exists |

### Deployment gate (explicit)

`BlockedLiveApplyAdapter` is the shipped default. CLI `apply` without an injected live adapter returns `apply.live_integration_missing`. Do **not** treat fixture adapter success, metadata `externalVerification` flags, or dry-run OK as authorization to mutate production. Live apply / trust-boundary resolution remains **unresolved** until a reviewed live adapter lands.

### Files introduced by APP-005

- `bootstrap/*.py` (engine, plan, scaffold, snapshot, adapters, manifest, lock, paths, …)
- `bootstrap-manifest.schema.json`
- `scripts/app-new.py` / `scripts/app-new.sh`
- `tests/fixtures/bootstrap/**`
- `tests/app_initializer_test.py`
- `docs/bootstrap.md` (this section)

## Files introduced by APP-001

- `docs/bootstrap.md` (this file)
- `app.config.schema.json`
- `examples/app.config.example.json`
- `requirements-bootstrap.txt`
- `scripts/validate-app-config.py`
- `scripts/validate-app-config.sh`
- `tests/config-validation_test.py`

## Files introduced / hardened by APP-002

- `scripts/validate-terraform-plan.py` / `scripts/validate-terraform-plan.sh`
- `examples/plan-ownership.example.json`
- `tests/plan-guard_test.py` / `tests/infra-safety_test.py`
- `tests/fixtures/plans/*`
- Terraform defaults and modules under `terraform/` (shared-project safe)
- Disabled mutate paths in `.github/workflows/terraform.yml`, `.github/workflows/deploy.yml`, `package.json`, `scripts/quick-start.sh`

## Files introduced by APP-003

- `scripts/migrate_app_namespace.py` / `scripts/migrate_app_namespace.sh`
- `db/shared_isolation/provision/00_disposable_test_baseline.sql`
- `db/shared_isolation/provision/01_control_and_roles.sql`
- `db/shared_isolation/fixtures/preexisting/01_seed.sql`
- `db/shared_isolation/fixtures/app_alpha/*.sql`
- `db/shared_isolation/fixtures/app_beta/*.sql`
- `tests/shared_db_isolation_test.py`
- `docs/bootstrap.md` (this section)
- `requirements-bootstrap.txt` (`psycopg` for local DB tests/harness)
- Deploy workflow remains `if: false` (no restored unattended migration path)

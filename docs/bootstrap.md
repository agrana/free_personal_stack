# App bootstrap contract (APP-001 + APP-002)

Pinned template revision: `7f21f51209659a77fd56b19e6116f388e783d93d`

Branch: `feat/app-bootstrap-contract`

Scope: reusable **non-secret** app configuration (APP-001) and shared-project infrastructure hardening + offline plan guard (APP-002).
Not in scope: namespaced DB migrations (APP-003), full plan/apply/resume automation (APP-005), game/initializer product work.

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
| `.github/workflows/deploy.yml` migrations | `if: false` — disabled pending APP-003 |
| `package.json` `terraform:apply` / `destroy`, `infra:apply` / `destroy`, `db:reset` / `db:migrate` | exit 1 stubs |
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

### Plan / apply / resume contract (APP-005 — not implemented)

APP-002 disables unguarded apply/destroy. APP-005 must implement:

1. **Plan (read-only)** — compute intended app-scoped changes without mutating infrastructure.
2. **Explicit app-scoped resource ownership** — only resources keyed to this app’s `deployment.identity` / `stateKey` / `namespace` / approved hostname.
3. **Immutable reviewed plan hash** — apply only after human review of that exact plan artifact.
4. **Remote account identity + permissions verification** — verify live cloud accounts/permissions **before** plan/apply; enforce this on every workflow/script path that could apply (do not leave legacy bypasses).
5. **Shared DB guard** — refuse if shared-database preservation checks fail.
6. **Manual DNS choice** — honor `dns.choice`; never auto-flip global DNS/email; CloudFront only via a dedicated later implementation.
7. **No secret values in artifacts** — plans/logs store alias names and redacted refs only.
8. **Dry run + idempotent resume** — safe re-entry after interruption.

Until APP-005, supported commands are: app-config validation (APP-001), offline plan guard (APP-002), and read-only `terraform plan` where credentials exist locally.

## Blocking preflight items (explicit)

These are **not** verified as live facts in APP-001/APP-002. Deployment-ready only requires the corresponding **metadata flags**. Callers must still perform independent live verification before apply:

1. Live account/permission verification (`externalVerification.accountPermissions`) — **APP-005 must enforce**
2. Shared-database preservation guard (`externalVerification.sharedDatabaseGuard`)
3. Approved secret-source verification for aliases (`externalVerification.secretSource`)
4. Live conflict discovery (`externalVerification.liveConflictDiscovery`)
5. Resolved DNS choice (`dns.choice` ≠ `unresolved`)

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

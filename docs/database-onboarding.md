# Database onboarding (managed-service preflight)

Read-only catalog preflight for an approved shared Supabase project. This stage
observes capability facts; it does **not** provision namespaces, apply
migrations, prove runtime connectivity, or complete application onboarding.

## Operator path (Secrets + merge)

1. Store the approved database target as repository Actions secrets:
   - `SUPABASE_URL` — `https://<project-ref>.supabase.co` (no path, port, query, or credentials)
   - `SUPABASE_ACCESS_TOKEN` — Management API token with **database:read** / fine-grained **database_read** (replace any `SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` placeholder in **Settings → Secrets and variables → Actions**; never paste tokens in chat)
   - Existing shell secrets (`SUPABASE_ANON_KEY` and Vercel/Cloudflare secrets) remain required for merge shell/DNS
2. Review and merge a PR that sets tracked `database-onboarding.config.json`:
   - `enabled: true`
   - `namespace: "<safe_app_namespace>"` (non-reserved; same rules as `migrate_app_namespace.validate_namespace`)
3. With shell `onboarding.config.json` `auto_apply: true` and hostname/zone set, default-branch push runs **Merge onboarding** after **App Quality** succeeds. No manual **Run workflow** click is required for the routine path.
4. When preflight is enabled, merge runs the read-only catalog inspection **after** exact-head App Quality and **before** Vercel/Cloudflare writes. `setup_required` or preflight failure blocks provider writes and writes a durable artifact. Successful catalog inspection records the artifact and continues shell/DNS; database readiness stays incomplete.

Template default and `examples/database-onboarding.config.example.json` keep `enabled: false` and `namespace: null`. Older generated apps without the file stay disabled/unconfigured (no network). A present malformed file fails closed.

There is no database target field in `onboarding.config.json`. The live target is the repository Supabase secrets above.

## Observed versus proven

| Signal | Meaning |
| --- | --- |
| Effective query-role flags and separate standard admin-candidate flags (`rolsuper` / `rolcreaterole` / `rolbypassrls`) | Observed `pg_catalog` attributes only. The effective role is distinguished from the connection's original login; candidate flags establish neither bootstrap credential identity nor permission to execute managed provisioning. |
| Namespace / role presence, ownership match, collision | Catalog facts for the configured namespace only |
| App table / RLS enabled / forced / policy counts | Catalog counts for that namespace — not isolation acceptance |
| `catalog_inspection_completed` | Read-only Management API query succeeded |
| `database_ready` / `live_migrations_authorized` / `full_app_ready` | Always `false` in this slice |

Service-role credentials bypass RLS and must not be used to claim isolation. Preserve other applications, `public`/`auth`/shared settings, and unrelated DNS or email configuration.

## Read-only Management API

Preflight calls only the documented beta [read-only query endpoint](https://supabase.com/docs/reference/api/v1-read-only-query):

`POST https://api.supabase.com/v1/projects/{ref}/database/query/read-only`

It runs as `supabase_read_only_user`. The client never falls back to the
write-capable `/database/query` endpoint, never accepts arbitrary SQL/URLs, and
never echoes raw provider bodies into reports.

## Managed foundation and delivery

The [managed database foundation](managed-database-foundation.md) provides
app-specific data/control schemas, restricted roles, transactional migrations,
and runtime RLS checks exercised against disposable PostgreSQL 17.
[Managed database delivery](managed-database-delivery.md) adds existing-token
CLI login / pooler / Vercel custody orchestration behind a default-disabled
`database-delivery.config.json`. Deployed HTTP/JWT verification and live
enrollment before production deploy remain separate reviewed gates.

## Remaining live gates

- Managed-compatible provisioning of app-specific data/control schemas and roles against the approved target
- Reviewed app-scoped schema migrations against the approved target
- Deployed application runtime connectivity under RLS-governed credentials
- Cross-user and cross-app isolation verification

Live migration/initializer apply gates remain disabled. Report onboarding incomplete until those stages exist and pass.

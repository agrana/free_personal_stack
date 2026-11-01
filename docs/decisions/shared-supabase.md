# Decision: shared Supabase for SavedTube + Agent Arena

| Field | Value |
| --- | --- |
| Status | **Accepted** (current shared choice settled) |
| Card | APP-009 (documentation / planning only) |
| Checked on | 2026-10-28 UTC (public official pages; HTTP 200) |
| Live controls | **Not** claimed applied — APP-011 remains the live DB/auth gate |

## Decision

Owner accepts **one existing shared Supabase project** for SavedTube and Agent Arena due to Free-plan **active project** allowance/budget. Each app keeps a **separate Vercel project**. The template may still **recommend** dedicated Supabase for independent isolation; that does not reopen this accepted choice.

This record plans boundaries and options only. It does **not** provision, change provider settings, apply schema/RLS, read private usage, or cut over endpoints.

## Access controls vs coupled risks

**Required before data-backed use** (APP-011 + reviewed migrations; not proven by shell launch):

- App-specific schemas, restricted roles/grants, migration ownership that does not touch the other app’s history.
- **RLS on every application table**, explicit policies, and **cross-app + cross-user denial tests**.
- Routine traffic uses RLS-governed credentials and authenticated app/user authorization.
- `service_role` / secret provisioning credentials **bypass RLS** — never isolation proof ([Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)). Schema name alone does **not** isolate `anon`/`authenticated`; grants + policies combine.

**Still coupled** even with correct RLS: compute, I/O, connections/pooler, Storage, egress, availability, Auth settings, recovery/restore. Free projects **pause after 1 week** of inactivity ([Pricing](https://supabase.com/pricing)); no claim the current project is paused. Owner considers early use manageable; **quota pressure has not been measured**. More capacity can relieve contention; a separate project establishes independent capacity and restore boundaries. RPO means acceptable data loss; RTO means acceptable recovery time. Shell `/api/health` is liveness only ([secrets-only-launch.md](../secrets-only-launch.md)).

## Options (future; approval-gated)

| Option | Benefit | Limits / approval considerations |
| --- | --- | --- |
| Stay Free / shared | Current budget choice; no paid org fee | Two active Free projects; inactivity pause; shared quotas and recovery; automatic daily DB backups excluded |
| Paid shared headroom (e.g. Pro) | Higher quotas, removal of inactivity pausing, Pro daily DB backups (7-day) | Data remains in one Postgres/Auth/Storage/Realtime project boundary; review organization-wide billing impact |
| Dedicated Supabase project | Independent managed Postgres+Auth+Storage+Realtime **project** boundary | Same-org billing/allowances remain shared; smaller compute tiers can use shared physical CPUs; org-wide allowances remain unchanged |
| Later other Postgres host | Independent DB host | Requires an explicit integration or replacement plan for each Supabase-specific service used |

## Advertised cost / backup facts (not an invoice)

Checked 2026-10-28 UTC. **Advertised baselines** only — not invoice totals; add-ons/overage/tax may apply. **No derived monthly totals.**

- Free: **2 active projects** across orgs where account is Owner/Admin; paused do not count; another org is **not** extra Free allowance ([Billing](https://supabase.com/docs/guides/platform/billing-on-supabase)).
- Free examples: **500 MB DB/project**, **1 GB** Storage, **5 GB** egress + **5 GB** cached egress, **50k MAU**. Nano: **60** DB connections / **200** pooler clients ([Compute limits](https://supabase.com/docs/guides/platform/compute-and-disk#limits-and-constraints)); Realtime **200** peak connections (distinct, [Pricing](https://supabase.com/pricing)). Prefer included allowance over per-unit overages; official pages may round differently.
- Billing scope: org-wide for MAU/egress/storage size; **DB size per project**; compute per running project ([Billing](https://supabase.com/docs/guides/platform/billing-on-supabase), [Compute](https://supabase.com/docs/guides/platform/manage-your-usage/compute)).
- Pro **from $25/mo** org; additional projects **from $10/mo**; compute hourly. Paid org **$10/mo compute credit**, monthly reset, **non-accumulating**, per **org** not per project ([Pricing](https://supabase.com/pricing), [Compute](https://supabase.com/docs/guides/platform/manage-your-usage/compute)).
- **Spend Cap**: Pro **enabled by default** for selected usage items — safeguard, **not** absolute bill ceiling; covered exceed can **restrict** service. Excludes **Compute**, **PITR**, other opted-in add-ons ([Pricing](https://supabase.com/pricing), [Cost control](https://supabase.com/docs/guides/platform/cost-control#spend-cap)).
- Backups: Free automatic daily backup **not** included. Pro daily DB backup, **7-day** retention; **excludes Storage object bytes**. **PITR** paid add-on needs ≥ **Small** compute — do not promise Pro includes PITR ([Backups](https://supabase.com/docs/guides/platform/backups)). Quote compute **and** backup features together.

**Clone warning:** restore-to-new-project is **whole-DB**, not per-app; paid + physical-backup/beta prerequisites; jobs/webhooks/extensions can **run immediately**. Do not blindly clone to extract Agent Arena ([Clone](https://supabase.com/docs/guides/platform/clone-project)). See [supabase-upgrade-isolation.md](../supabase-upgrade-isolation.md).

## Telemetry gap

**Not retrieved / unverified:** plan, compute, add-ons, Spend Cap, invoice, Usage metrics, per-app attribution. No management token; public settings ≠ private billing. Dashboard **not** inspected. Gap does not block this record; it **blocks** informed purchase/threshold decisions.

Read-only collection (authorized operator; no secrets in chat/git): plan + Spend Cap + upcoming invoice; per-project compute/add-ons/pause; period usage (DB size, Storage growth, cached vs uncached egress, MAU); DB vs pooler vs Realtime peaks; CPU/memory/IO/latency/errors where Metrics/Usage allow ([Metrics](https://supabase.com/docs/guides/observability/metrics)); peaks/trends and feasible per-app attribution. Owner sets numeric thresholds/review windows from representative data and budget.

## Review triggers

Revisit shared vs dedicated when: sustained capacity pressure affecting SLOs; rising production importance; independent RPO/RTO/restore need; stronger isolation/compliance. No invented thresholds or migration date.

## Related (separate)

APP-010 DNS · APP-011 live DB/auth gate · APP-012 live evidence · APP-013 fresh shell E2E

## Official sources (core)

1. [Pricing](https://supabase.com/pricing)
2. [Billing](https://supabase.com/docs/guides/platform/billing-on-supabase)
3. [Compute usage](https://supabase.com/docs/guides/platform/manage-your-usage/compute)
4. [Spend Cap](https://supabase.com/docs/guides/platform/cost-control#spend-cap)
5. [Backups](https://supabase.com/docs/guides/platform/backups)
6. [Restore to a new project](https://supabase.com/docs/guides/platform/clone-project)

Narrower auth links as needed: [Sessions](https://supabase.com/docs/guides/auth/sessions), [Redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls), [Signing keys](https://supabase.com/docs/guides/auth/signing-keys), [MFA](https://supabase.com/docs/guides/auth/auth-mfa), [SMTP](https://supabase.com/docs/guides/auth/auth-smtp). RLS: [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security).

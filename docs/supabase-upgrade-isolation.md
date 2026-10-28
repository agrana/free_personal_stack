# Supabase upgrade / isolation runbook (future checklist)

High-level **future** checklist only — not execution. Does not migrate, change provider settings, export/import, run SQL, or cut over secrets. Checklist coverage ≠ migration verified now.

Accepted shared Supabase for SavedTube + Agent Arena ([decision](decisions/shared-supabase.md)). Template still prefers dedicated projects for new apps. Live DB/auth = **APP-011**.

## When to open

After owner approval when the decision-record triggers fire. Capacity decisions use representative usage, service objectives and an approved budget. Security or independent-recovery requirements can justify separation without waiting for a capacity threshold; obtain the target cost estimate and migration evidence before execution.

## Capacity-only upgrade path

For headroom in the existing shared project: inventory **every project** in the organization, its current compute/add-ons and expected paid-plan billing; get owner approval for the organization plan, compute changes and spend-cap limits. Review backups, maintenance impact and rollback constraints before any separately authorized upgrade. Afterwards verify both apps, unchanged endpoints/project identity, capacity metrics and the upcoming invoice. This path preserves the shared data and restore boundary; extraction uses the separate checklist below.

## 1. Inventory

Without mutating production: app schemas/tables/volumes, migration ledger/history, extensions/functions/triggers/jobs/webhooks, **cross-schema** refs; roles/grants/**RLS** on every app table; Auth IDs/identities/sessions/signing keys/redirects/OAuth/SMTP/MFA; Storage bytes **and** metadata/policies (DB backup ≠ objects); Realtime publications/channels; Edge Functions + secret/config **names** only.

## 2. Identity ownership

Shared Auth users may belong to both apps. Decide ownership/mapping; test **reauthentication**. Do not assume JWT keys, sessions, cookies, or MFA portability, or duplicate the other app’s data. Reconfigure redirects/OAuth/SMTP per target ([Redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls), [Sessions](https://supabase.com/docs/guides/auth/sessions), [Signing keys](https://supabase.com/docs/guides/auth/signing-keys), [MFA](https://supabase.com/docs/guides/auth/auth-mfa)).

## 3. Target + cost approval

Name, **region**, organization, plan, compute, add-ons (backup/PITR/disk). Approve cost from advertised pricing + measured usage — no invented totals ([Pricing](https://supabase.com/pricing), [Billing](https://supabase.com/docs/guides/platform/billing-on-supabase)). Same-org dedicated project = independent project boundary; org allowances/billing still shared. Confirm Spend Cap (Pro default-on for covered quotas; excludes compute/PITR/selected add-ons) ([Cost control](https://supabase.com/docs/guides/platform/cost-control#spend-cap)).

## 4. Backup / restore / rehearsal

Independent DB + Storage backup/restore validation. Whole-project clone is **not** selective; physical restore can start jobs/webhooks/extensions immediately ([Clone](https://supabase.com/docs/guides/platform/clone-project)) — require side-effect containment or selective/logical export of **Arena-owned** objects only. Disposable rehearsal with denied side effects. **Source SavedTube remains untouched.** PITR needs paid add-on + ≥ Small compute ([Backups](https://supabase.com/docs/guides/platform/backups)).

## 5. Migration mechanics (approval-bound)

Selective Arena boundary; resolve cross-schema dependencies; coordinate write freeze/final sync, including SavedTube writes if shared objects require it. Verify target managed-service role capabilities, scoped migration ownership, grants/default privileges and RLS. Check integrity counts/checksums and user/app denial tests with routine credentials; `service_role` bypasses RLS. Name the observer/approver and acceptance criteria; define RPO (acceptable data loss) and RTO (recovery time).

## 6. App-only cutover

Inventory Agent Arena’s actual configuration writers and environment targets first: GitHub Actions secrets, Vercel Production/Preview/Development bindings, and any native integration or Terraform/manual synchronization ([binding ownership](setup/VERCEL_SUPABASE.md#environment-targets-and-writers)). Select one authoritative writer per app-scoped binding and coordinate any competing synchronization before cutover; integration presence remains unverified until inventoried. Keep privileged credentials server-side and production access restricted to reviewed environments.

After approval, update the Arena-owned bindings through the reviewed GitHub/Actions delivery path and any separately approved integration changes, then rebuild/redeploy so browser-bundled public configuration targets the new project. Verify the deployed project identity and configuration, Auth, app/user-denial tests, read/write, Realtime/Storage if used, and browser behavior. Preserve SavedTube’s endpoints and bindings. Name the acceptance approver, monitoring window and rollback owner before enabling writes.

## 7. Rollback and cleanup

After target **new writes**, rollback needs reconciliation/freeze — not URL flip alone. Retain source until acceptance, backups, and rollback window. Cleanup separately approved: only Arena-owned resources; never delete shared DB/identities/objects SavedTube needs.

## Related

| Item | Role |
| --- | --- |
| [shared-supabase.md](decisions/shared-supabase.md) | Accepted decision + cost/telemetry |
| APP-010 / 011 / 012 / 013 | DNS · live DB/auth · live evidence · fresh E2E |
| [secrets-only-launch.md](secrets-only-launch.md) | Shell; public keys ≠ isolation |
| [VERCEL_SUPABASE.md](setup/VERCEL_SUPABASE.md) | Vercel↔Supabase binding |

## APP-009 no-ops

Docs only: no purchases, provisioning, dashboard mutations, SQL, credential requests, workflow dispatch, or cutover.

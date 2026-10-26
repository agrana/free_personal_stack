# Connect Vercel to Supabase

## Recommendation: connect the existing project first

Supabase documents an integration that links a selected Vercel project to a selected Supabase project and synchronizes environment configuration. Its partner guide explicitly permits an existing Supabase project. Prefer this connection path over manually copying database keys when the current integration screen offers it.

For multiple apps sharing one database, connect the new Vercel app to the intended existing Supabase project. Verify the selected project reference and the list of target Vercel environments before approving. If the interface offers only creation of a new database, stop and use the reviewed manual-binding fallback; do not create another project to get past setup.

### Two different provider flows

| Flow | Provider-documented behavior | Suitability |
| --- | --- | --- |
| Existing-project integration | Select Vercel and Supabase projects; synchronize environment variables | First choice for the existing shared database, subject to current account/UI availability |
| Vercel Marketplace native provisioning | Create a Supabase project/organization through Vercel; synchronize environment variables, team roles and Vercel-managed billing | An option for a fresh installation, not the default for reusing an existing externally managed database |

The Marketplace documentation says both its starter-template and install quickstarts **create a Supabase project**. Its discussion of linking existing user accounts is not evidence that it imports existing databases. Neither attaching your existing database to Marketplace billing nor transferring project ownership has been verified here. Keep those lifecycle and billing changes outside normal app onboarding.

## Existing-project setup

1. In the intended Vercel account/team, open integrations and select Supabase. The provider guide describes **Add Integration**, **Specific Projects**, then choosing the Vercel and Supabase projects in the authorization flow. Screen labels can change.
2. Grant access to the specific new Vercel app and select the existing Supabase database project. Authorize only after reviewing scopes and target environments.
3. Inspect synchronized variable **names and targets**, not values. Existing integration installations may use different variable names from the new Marketplace flow.
4. Confirm the connected project's identity. Keep privileged values server-side. Prefer modern publishable names when the integration provides them; see the matrix below.
5. After environment changes, perform an authorized redeployment and verify the actual app. A successful connection is not proof that schema permissions, Auth redirect URLs or RLS are configured correctly.

## Public key name matrix (modern + legacy)

| Configuration | Runtime / Next wiring | Secrets preflight / build boundary |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` nonempty | Preferred; also mapped into the legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY` contract for auth-helpers | Allowed when the value classifies as public (`sb_publishable_*`) |
| Only `NEXT_PUBLIC_SUPABASE_ANON_KEY` nonempty | Legacy blank fallback | Allowed when the value classifies as public (legacy anon JWT) |
| Both nonempty (equal or **unequal**) | Prefer modern; validate **each** candidate | Allowed when each candidate is public-class. Modern publishable and legacy anon JWT are legitimately different strings for the same project |
| Privileged JWT (`role=service_role` / `supabase_admin`) or `sb_secret_*` in a public slot | Must not be browser-bound | **Fail closed** (`public.config.supabase_key_privileged`) at preflight and `next.config.js` |
| Unsupported roles (including `authenticated`), unknown opaque strings, malformed JWT shapes | Rejected | **Fail closed** (`public.config.supabase_key_malformed`) |
| JWT `ref` disagrees with explicit URL / project id | Rejected when evidence is explicit | **Fail closed** (`public.config.supabase_key_project_mismatch`); otherwise project binding stays **unknown** (key strings do not prove same-project; live preflight required) |
| Arbitrary other `NEXT_PUBLIC_*` names | Not authorized by this allowlist | **Fail closed** (`public.config.allowlist_violation`) |

Public variable **names** never authorize server secret or service-role values. Renaming does not change privileges. Public Supabase keys rely on database RLS; they are not service credentials. Compatibility unit tests do **not** prove a live Vercel–Supabase integration is installed or correctly targeted.

### Environment targets and writers

- Review which Vercel environments (Production / Preview / Development) receive synchronized or Terraform-managed bindings before approving.
- Prefer one writer per app-scoped binding: either the native existing-project integration or Terraform/manual binding — not both fighting over the same key.
- Untrusted Preview branches must not receive broad production database access by default.
- Synced Vercel runtime variables are separate from GitHub Actions secrets and migration credentials.
- Creating a **new** Supabase project (Marketplace create-database path or otherwise) is a future backlog investigation for this template — not implemented here and not universally forbidden for every future mode.

## Integration safety and limits

- Variable synchronization reduces manual copying; it does not provide app-level data isolation.
- Project-wide secret/service-role keys bypass RLS. Do not assume the integration distributes least-privilege app roles.
- Database migrations, per-app grants, Realtime publication, Supabase Auth redirect URLs and shared-service protection remain explicit reviewed steps.
- An integration can trigger environment/deployment changes. Obtain authorization before installing or modifying it; this research performed no installation or account changes.

### Optional local environment retrieval

The provider guide documents `vercel link` and `vercel env pull`. Use an approved/pinned CLI and explicit app/team/environment selection, check supported flags with its help, and review authorization before retrieval. Pulling environment configuration can download privileged values. Write only to a verified ignored local file with restrictive permissions; never display or commit the contents. Do not blindly pull a shared production environment for frontend development.

## Fallback

When existing-project integration is unavailable, use the approved original credential source and app-scoped binding workflow. Validate references and permissions, then bind only the required runtime values. Human authorization stays separate from agent-visible secret data. GitHub's secret metadata cannot return the original values.

## Official sources

- [Supabase Vercel integration partner guide](https://supabase.com/partners/catalog/vercel) — existing-project option, project selection and local env retrieval.
- [Supabase Vercel Marketplace guide](https://supabase.com/docs/guides/integrations/vercel-marketplace) — native provisioning, synchronized names (`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_SUPABASE_URL`, and related), billing and limitations.
- [Vercel Supabase integration](https://vercel.com/integrations/supabase).

Verified against public provider documentation. Account-specific availability, permissions, actual synchronized values and deployment behavior remain unverified until an explicitly authorized live preflight.

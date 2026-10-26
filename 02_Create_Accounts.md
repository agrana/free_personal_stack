# Part 2: Accounts and service connections

## What you need before deployment

Create an account only when you do not already have a suitable one. Reuse authorized service connections for subsequent apps. Free plans have shared usage limits and eligibility conditions; this template does not guarantee a zero bill.

| Service | Required for | Prepare once | Per application |
| --- | --- | --- | --- |
| [GitHub](https://github.com) | Repository, template generation and CI | Account with permission to create repositories and configure Actions; authorize the Vercel GitHub integration for selected repositories | Generate a repository using **Use this template**; inspect workflow triggers before connecting credentials |
| [Vercel](https://vercel.com) | Hosted Next.js application | Account/team with access to the intended GitHub repository | Separate Vercel project, approved environment bindings and deployment access |
| [Supabase](https://supabase.com) | Database and authentication | Account with access to the intended project for this rollout (reuse one existing shared database here); creating a new project is a separate future backlog investigation | Isolate app data, migrations and permissions within the chosen project |
| [Cloudflare](https://cloudflare.com) | Custom hostname managed by this template | Account with DNS access to the existing domain's zone | App-specific DNS record after review |
| Domain registrar | Your own hostname | A registered domain and control of its DNS delegation; registration may cost money | Reuse a subdomain of the existing domain |

Cloudflare and a purchased domain are unnecessary for an initial Vercel-provided hostname. A custom domain managed by this template uses Cloudflare DNS. Domain registration and Cloudflare DNS hosting are separate responsibilities; use the existing authoritative zone instead of creating or moving a zone for each app.

No AWS account or CloudFront is required. OpenAI, Jev and other inference accounts are optional application features, not template prerequisites. Cursor/Codex subscriptions and API access are separate products; a coding-agent subscription is not required to host the app.

## Human-friendly connection sequence

1. **Generate your GitHub repository.** Use the template; inspect its [agent guide](AGENTS.md) and workflows.
2. **Connect GitHub to Vercel.** Allow access to the selected app repository and create/import its own Vercel project. Review automatic deployment triggers and initial configuration before import; incomplete configuration may cause an initial build failure.
3. **Connect the existing Supabase project.** Prefer the existing-project integration if offered for your account; see [Vercel–Supabase integration choices](docs/setup/VERCEL_SUPABASE.md). Do not choose a flow that creates another database when reusing a shared one.
4. **Validate environment bindings.** Check names, target environments and public/server boundaries without printing values. Supabase integration does not configure GitHub Actions credentials, app migrations or RLS isolation automatically.
5. **Authorize Cloudflare when ready for your custom domain.** Reuse the correct zone and review the proposed app record before writing it.
6. **Review the deployment plan.** Account connection and a valid configuration are separate from approval to provision resources or run migrations. The initializer's live apply integration remains gated; follow the current [bootstrap contract](docs/bootstrap.md).

The intended one-entry-point wizard is an onboarding goal, not an implemented guarantee. Today, use published commands and provider connection screens. An agent should inspect existing connection status and request only missing authorization, preserving progress between steps.

## Public Supabase key names

Prefer `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` when the integration or dashboard provides it. Legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY` remains supported as a blank fallback. Modern publishable and legacy anon JWT values may legitimately differ for the same project; resolve prefers modern when nonempty and validates each configured candidate. Privileged/service-role material must never occupy a public slot. Public keys rely on database RLS and are not service credentials. See the [Vercel–Supabase key matrix](docs/setup/VERCEL_SUPABASE.md#public-key-name-matrix-modern--legacy).

## Credential boundaries

An account, a runtime key and an automation-management token serve different purposes. Request only the credentials required by the chosen operation:

| Connection | Identifiers / public configuration | Sensitive credentials, only if required |
| --- | --- | --- |
| GitHub | Repository identity | Approved CLI/OAuth session; Actions permission to set secrets does not reveal existing secret values |
| Vercel | Team/organization ID and a new app-specific project ID | Management token/session for reviewed automation; native Git deployments do not by themselves require a Terraform token |
| Supabase runtime | Project URL and publishable/legacy anon key, restricted by RLS | Secret/service-role key only for explicit server-side requirements; it bypasses RLS and is not app-isolated |
| Supabase migrations | Existing project reference and app namespace | Reviewed database connection with suitable privileges; project-management token only for operations that require it |
| Cloudflare | Existing zone/account IDs and approved hostname | Zone-scoped API token for authorized DNS automation |

Keep secret values in an approved secret store or provider environment configuration. Use names/references in repository configuration. Never paste credentials into chat or commit them. Avoid resetting a shared database password merely to simplify setup; rotation affects all consumers and requires coordination.

For Cloudflare DNS automation, request Zone Read and DNS Edit on the **specific existing zone**. Email-routing and account-wide zone-management permissions are outside ordinary app DNS setup. Cloudflare's zone-scoped DNS token can still modify other records in that zone, so application ownership guards and reviewed plans remain necessary.

## Existing shared database checklist

- Select the existing project by reference; confirm it matches the intended account and database.
- Preserve other applications, authentication settings and shared project lifecycle.
- Use app-owned migrations, namespaces and reviewed RLS/grants; names alone do not enforce isolation.
- Review the integration's environment targets before allowing Preview or Development access to production data.
- Verify managed Supabase compatibility and recovery procedures before bootstrap/migration changes. Local PostgreSQL tests alone do not establish managed-project privileges.
- Confirm available storage, compute and Realtime capacity; all apps share the project's allowances.

## Next steps

Use the [README starter prompt](README.md#start-with-your-coding-agent), [AGENTS.md](AGENTS.md) and [current bootstrap documentation](docs/bootstrap.md). Earlier deployment guides provide historical context; current disabled provisioning and migration gates take precedence.

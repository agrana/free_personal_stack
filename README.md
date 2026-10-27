# Free Personal Stack

A reusable Next.js template with a **secrets-only launch** to Vercel. Start with a database-free landing page; add your product after the shell is deployed.

## Quickstart: template → four secrets → Launch app

1. Select **Use this template → Create a new repository** in GitHub. Use a lowercase name such as `my-application` (letters, digits and single hyphens; start with a letter). Create an independent repository, not a fork; leave **Template repository** disabled on your new app.
2. In the generated repository, open **Settings → Secrets and variables → Actions → Repository secrets** and add these four entries:

   | Secret              | Value                                                                                  |
   | ------------------- | -------------------------------------------------------------------------------------- |
   | `VERCEL_API_TOKEN`  | Vercel access token authorized for the intended team and project creation/deployment   |
   | `VERCEL_TEAM_ID`    | That team's public ID (`team_…`), stored as a secret to choose scope explicitly        |
   | `SUPABASE_URL`      | Existing Supabase project's HTTPS URL                                                  |
   | `SUPABASE_ANON_KEY` | That project's modern `sb_publishable_…` key; a valid legacy anon key is also accepted |

3. Open **Actions → Launch app → Run workflow** on your default branch (normally `main`). Choose `deploy`; leave the recovery inputs empty. This is your authorization to create this app's Vercel project and deploy the current revision.
4. Read the run summary. `page_and_health_verified` reports the verified URL and source SHA. `setup_required` means secrets are missing or still placeholders, even if the run is green. A protected deployment is not public verification success.

**No local `.env`, clone, app-config edit, Cloudflare account, service-role key or Terraform credentials are needed for this launch.** Secret edits alone do not trigger Actions. Once an owned project exists, later default-branch pushes may deploy it; pushes never create a project or adopt an unmarked one. The upstream template is blocked from self-deploying by numeric repository ID.

See **[Secrets-only launch guide](docs/secrets-only-launch.md)** for account permissions, finding your team ID, database choices, interrupted-run recovery and verification limits.

## What the initial deployment includes

- A server-rendered **Application ready** page with no application database reads or writes, demo data or inherited navigation.
- `/api/health` reporting process liveness and the exact deployed source revision; it does not certify database access, authentication or RLS.
- Reusable auth code retained at `/auth/signin` and `/auth/signout`, without links from the landing shell. Authentication settings and database isolation still need review before product use.
- An explicitly scoped, repository-owned Vercel project. No domain, database, schema, DNS or email provisioning.

Use an existing Supabase project, or create a dedicated project in Supabase before launch. A dedicated database is preferred for isolation. Reusing a shared project is your choice and carries shared-resource risk; configure app-scoped schema and RLS and verify cross-app isolation **before adding data features**. The launcher does not create provider databases or apply migrations. Free-plan limits and provider charges still apply.

## Build your product with a coding agent

Open your generated repository in your preferred coding agent and use:

```text
Read AGENTS.md, package.json and docs/secrets-only-launch.md. Inspect Git status
and preserve existing work. Confirm this is my generated app, not upstream.
Keep the initial shell database-free. Help me implement my product in small,
tested steps. Before adding data access, review app-scoped schema ownership,
RLS, cross-app isolation, auth redirects and migration/backup/recovery plans.
Keep secret values out of source, logs and chat. Do not bypass disabled legacy
infrastructure gates. Ask for separate approval before publishing, modifying
secrets, changing provider resources or applying migrations. Report actual
verification results and distinguish local tests from live deployment evidence.
```

Agent instructions and local verification: [AGENTS.md](AGENTS.md).

## Advanced and historical paths

The generic **Launch app** runner is separate from the safety initializer. The initializer's live adapter remains blocked with `apply.live_integration_missing`; legacy apply/destroy/reset and unattended shared-database migration gates remain disabled. Historical setup workflows require additional credentials and are **not** the four-secret quickstart.

- [Bootstrap contract and safety initializer](docs/bootstrap.md)
- [Deployment verification and guarded orchestration](docs/app-deployment.md)
- [Vercel–Supabase integration and key mapping](docs/setup/VERCEL_SUPABASE.md)
- [Account and permission checklist](02_Create_Accounts.md)
- [Local development](03_Local_Setup.md)
- [Terraform workflow background](docs/ci-cd/TERRAFORM_SETUP.md) and [Terraform reference](terraform/README.md)
- [Documentation index](docs/README.md), [architecture](docs/architecture/) and [development guides](docs/development/)
- [Historical walkthrough](00_Manifesto.md) through [building your app](06_Build_Your_App.md)

Reusable fixes belong upstream; generated repositories do not receive template updates automatically. Application features belong in the generated repository.

## License

MIT

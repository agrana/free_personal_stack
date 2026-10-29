# Free Personal Stack

A reusable Next.js template whose required GitHub-only onboarding includes an intended hostname, a Supabase connection, app-scoped schema migrations, deployment, DNS and end-to-end verification. The [onboarding contract and implementation status](docs/end-to-end-onboarding.md) distinguish required outcomes from the currently shipped partial workflows.

## Quickstart: template → credentials → deploy → DNS → verify

Configure the intended hostname and authoritative zone in tracked `onboarding.config.json`, and the approved database target via repository Supabase Secrets, before provisioning an application project. Required live schema migration and application-to-database verification orchestration remain implementation gaps. The shell and DNS workflows below cover intermediate stages; complete onboarding additionally requires applied migrations, runtime database access, grants and RLS isolation checks through Actions.

1. Select **Use this template → Create a new repository** in GitHub. Use a lowercase name such as `my-application` (letters, digits and single hyphens; start with a letter). Create an independent repository, not a fork; leave **Template repository** disabled on your new app.
2. In the generated repository, open **Settings → Secrets and variables → Actions → Repository secrets** and add:

   | Secret                 | Value                                                                                  |
   | ---------------------- | -------------------------------------------------------------------------------------- |
   | `VERCEL_API_TOKEN`     | Vercel access token authorized for the intended team and project creation/deployment   |
   | `VERCEL_TEAM_ID`       | That team's public ID (`team_…`), stored as a secret to choose scope explicitly        |
   | `SUPABASE_URL`         | Existing Supabase project's HTTPS URL                                                  |
   | `SUPABASE_ANON_KEY`    | That project's modern `sb_publishable_…` key; a valid legacy anon key is also accepted |
   | `CLOUDFLARE_API_TOKEN` | Zone Read + DNS Edit for the intended Cloudflare zone                                  |

3. Review and merge a prepared PR that sets `onboarding.config.json` hostname, `cloudflare_zone_name`, and `auto_apply: true` (template default is `false` with null host/zone). That merge is the apply authorization. You do not click Run workflow or paste digests for routine onboarding.
4. Default-branch push runs **Merge onboarding** after **App Quality** succeeds on the same head: shell deploy, hostname attach, DNS-only CNAME, and verification. Read the summary. `infrastructure_dns_verified_database_incomplete` means shell+DNS passed while database/migration/RLS stages remain unimplemented. `setup_required` or `onboarding_auto_apply_disabled` means no provider writes. A protected deployment is not public verification success.

**This quickstart uses Secrets + merge-reviewed config.** Maintainer **Launch app** / **Custom domain** digest workflows remain available. The upstream template is blocked from self-deploying by numeric repository ID. Onboarding is complete only after DNS, HTTPS, the page and exact deployed-revision checks pass at the intended hostname, runtime database access works, schema migrations are applied, and grants and RLS isolation are verified. The Vercel URL is an intermediate deployment result.

See **[Secrets-only launch guide](docs/secrets-only-launch.md)** for shell-stage permissions, team ID, database choices and recovery. Required hostname setup: **[Custom domain](docs/custom-domain.md)**. Maintainer local stub rehearsal (not live): **[New app runbook](docs/new-app-runbook.md)**.

## What the initial deployment includes

- A server-rendered **Application ready** page with no application database reads or writes, demo data or inherited navigation.
- `/api/health` reporting process liveness and the exact deployed source revision; it does not certify database access, authentication or RLS.
- Reusable auth code retained at `/auth/signin` and `/auth/signout`, without links from the landing shell. Authentication settings and database isolation still need review before product use.
- An explicitly scoped, repository-owned Vercel project. **Merge onboarding** handles routine shell deploy and hostname/DNS; maintainer **Launch app** / **Custom domain** workflows remain available. Database/schema provisioning and email changes retain their own approval boundaries.

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

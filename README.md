# Free Personal Stack

A reusable Next.js application and infrastructure template using Terraform, Vercel, Supabase, and Cloudflare. Local configuration, safety checks, and deployment verification are implemented; the initializer's live provisioning adapter remains gated. See [current capabilities and safety boundaries](docs/bootstrap.md).

## Accounts you need

| Account | Purpose | Reuse for additional apps? |
| --- | --- | --- |
| **GitHub** | Generate the repository, store code and run CI | Yes |
| **Vercel** | Host the app; each app gets its own project | Yes, reuse the account/team |
| **Supabase** | Database and authentication | Yes, reuse one existing project/database with app-specific isolation |
| **Cloudflare** | DNS for a custom domain managed by this template | Yes, reuse the existing zone |
| **Domain registrar** | Own the custom domain | Yes, use another subdomain |

Start with a Vercel-provided hostname if you do not need a custom domain yet; Cloudflare/domain ownership is optional for that first preview. OpenAI and other model-provider accounts are optional app features. No AWS account is needed. Free-plan limits and domain costs still apply.

**Before collecting tokens:** read the [account and permission checklist](02_Create_Accounts.md). Prefer the [Vercel–Supabase existing-project integration](docs/setup/VERCEL_SUPABASE.md) to reduce manual copying; the native Marketplace create-database flow is a different path. This rollout reuses one existing database; a create-new project mode is deferred backlog. Runtime, build boundary and preflight accept modern `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` with legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY` blank fallback (values may differ) — see that guide for the key matrix.

## Start with your coding agent

Create a repository with GitHub's **Use this template**, open its checkout in your coding agent, and paste this prompt. It is tool-neutral: use it with Cursor, Codex, Claude Code, or another agent that can inspect and edit the repository.

```text
Help me start an application from this template. Work in this repository.

First read AGENTS.md, package.json, docs/bootstrap.md and relevant workflows.
Inspect Git status and preserve existing edits. Determine whether this is the
upstream template or a generated app. If it is upstream, ask before creating
an app repository through GitHub's template-generation flow.

Ask me only for missing information: what the app does, its name, desired
hostname, and whether existing Vercel, Cloudflare and Supabase connections
can be reused. Use Cloudflare for DNS, a separate Vercel project for this app,
and one existing shared Supabase database with app-specific isolation.

Inspect the implemented commands before running them. Prepare non-secret
configuration, validate it, and run applicable local tests. Inspect dependency
scripts before installing. Use secure, explicitly authorized credential
sources; never ask me to paste secrets into chat or expose values in logs,
Git, command arguments or browser code. Reuse suitable authorized service
connections instead of asking me to copy credentials between dashboards.

Proceed with local setup and verification. With my approval to create only
missing secret placeholders in my explicit OWNER/REPO, use the authenticated
GitHub CLI and scripts/setup-secret-placeholders.py --repo OWNER/REPO first
(dry-run), verify the target is my app rather than upstream, then add --apply.
Preserve existing secrets. Direct me to edit the precreated entries in GitHub's
repository Secrets UI; placeholders are missing credentials, not readiness.
At a missing permission or human
authorization step, explain the exact action needed and preserve progress.
Report unsupported automation honestly: the shipped initializer's live apply
adapter is gated. Do not bypass safety checks or pretend fixture tests prove
live provider access. Use an explicit reviewed source if pinned template
history is missing; never disable provenance checks.

Before publishing code, binding secrets, provisioning resources, changing
DNS or applying database migrations, obtain the appropriate approval. For
live changes, first show an app-scoped plan with verified ownership, costs,
shared-resource protection and rollback steps; request approval for that
exact plan. Leave other apps, shared database lifecycle, DNS zones and email
configuration intact.

Finish with actual validation results, remaining blockers and the next
specific action. If deployment is authorized and supported, verify its real
URL and deployed revision before reporting success. Put reusable onboarding
fixes in the template and application features in the generated repository.
```

Agent instructions: [AGENTS.md](AGENTS.md). The prompt starts a guided setup; it does not promise unattended deployment or automatically authorize remote changes.

## Precreate secret entries, then edit them in GitHub

From your generated app checkout, authenticate `gh` as the repository owner
(or an explicitly authorized administrator). Use `gh auth login` interactively
with an owner PAT: a fine-grained PAT scoped to this repository with **Secrets:
read and write** (and repository metadata read), or a classic PAT with `repo`
scope. Never paste the PAT in chat, a command argument or a tracked file.
The default Actions `GITHUB_TOKEN` cannot manage repository secrets; adding
workflow `permissions` does not grant this ability. Environment `GH_TOKEN` /
`GITHUB_TOKEN` overrides saved CLI login: unset unsuitable tokens before login.

```bash
gh auth login --hostname github.com
gh auth status
# Replace OWNER/REPO with your generated app, not the upstream template.
python3 scripts/setup-secret-placeholders.py --repo OWNER/REPO
# Review the verified target and planned names; apply only with approval:
python3 scripts/setup-secret-placeholders.py --repo OWNER/REPO --apply
```

The default is dry-run (`--dry-run` is also accepted). The script verifies the
explicit target through GitHub, lists all repository secret names before writing,
and creates only missing names from `examples/app.config.example.json`
`secretAliases`: `CLOUDFLARE_API_TOKEN`, `VERCEL_API_TOKEN`, `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ACCESS_TOKEN`, and
`TERRAFORM_STATE_PASSWORD`. Existing entries are preserved, including existing
placeholders. Any owner/repository is permitted when explicitly selected; the
script never infers a target from this checkout's remotes.

Open `https://github.com/OWNER/REPO/settings/secrets/actions` → **Repository
secrets** and edit each placeholder with its real value. The sentinel
`SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` counts as missing in credential preflight
and setup validation; names existing in GitHub do **not** establish readiness.
These are repository secrets, not environment secrets. The historical setup
workflow has additional requirements beyond these seven bootstrap aliases.

Run without concurrent secret editors: GitHub's secret API is an upsert, not an
atomic create-only API. The script rechecks names before each write, but cannot
eliminate a concurrent create race. Remote errors stop the run with a nonzero
exit; earlier successful placeholders remain and a rerun preserves them. No
real secret values are read or printed. Secret entry creation neither deploys
resources nor authorizes binding, infrastructure apply or database migrations.

## 🚀 Quick Start

1. **Use this template** — create your own repository with GitHub's template-generation flow.
2. **Open the checkout in your coding agent** — paste the starter prompt above.
3. **Validate configuration and local prerequisites** — follow [AGENTS.md](AGENTS.md) and [the bootstrap contract](docs/bootstrap.md).
4. **Connect services securely** — reuse authorized connections, verify missing permissions, and keep credential values outside Git and chat.
5. **Review deployment readiness** — live provisioning remains gated; approve actual resource changes separately once the missing integration is implemented and verified.

For detailed setup instructions, see:
- [Blog Series](00_Manifesto.md) - Start here for the full journey
- [Terraform with GitHub Actions](docs/ci-cd/TERRAFORM_SETUP.md) - Complete automation guide
- [Quick Start Guide](docs/setup/QUICK_START.md) - Fastest path to deployment

## 📚 Documentation

### Setup Guides
- **[00_Manifesto.md](00_Manifesto.md)** - Why this stack exists
- **[01_Fork_and_Clone.md](01_Fork_and_Clone.md)** - Get started
- **[02_Create_Accounts.md](02_Create_Accounts.md)** - Account setup
- **[03_Local_Setup.md](03_Local_Setup.md)** - Local development
- **[04_Deploy.md](04_Deploy.md)** - Deploy from GitHub
- **[05_What_You_Got.md](05_What_You_Got.md)** - What's included
- **[06_Build_Your_App.md](06_Build_Your_App.md)** - Start building

### Automation & Infrastructure
- **[docs/ci-cd/TERRAFORM_SETUP.md](docs/ci-cd/TERRAFORM_SETUP.md)** - Run Terraform in GitHub Actions
- **[docs/ci-cd/SECRETS_CHECKLIST.md](docs/ci-cd/SECRETS_CHECKLIST.md)** - Required secrets checklist
- **[docs/setup/SETUP_HICCUPS.md](docs/setup/SETUP_HICCUPS.md)** - Common issues and solutions
- **[terraform/README.md](terraform/README.md)** - Terraform documentation

### Additional Documentation
- **[docs/README.md](docs/README.md)** - Complete documentation index
- **[docs/architecture/](docs/architecture/)** - System architecture and design
- **[docs/setup/](docs/setup/)** - Setup guides and troubleshooting
- **[docs/development/](docs/development/)** - Development tools and practices

## 🎯 What You Get

### Infrastructure (Terraform)
- **Cloudflare DNS** — configuration for explicitly approved app records; shared zone ownership stays separate
- **Vercel Project** — app-specific project configuration; live creation remains gated
- **Supabase** — reference an existing database with app-specific migration and access isolation
- **Credential preflight** — validated secret references and redacted reports; live permission and binding checks remain required
- **Shared services** — existing email routing and unrelated DNS records remain untouched

### Application (Next.js)
- ✅ **Verification Dashboard** - Check Domain, Auth, Database, Email status
- ✅ **Supabase Auth** - Ready to use with session management
- **Database** — app-scoped migration harness; unattended shared-database migrations remain disabled
- ✅ **TypeScript** - Full type safety
- ✅ **Tailwind CSS** - Beautiful, responsive UI

## 🔧 Tech Stack

- **Infrastructure**: Terraform
- **Hosting**: Vercel
- **Database & Auth**: Supabase
- **DNS & Email**: Cloudflare
- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS

## 🎓 Learning Path

### Option 1: Follow the Blog Series (Recommended)
Read [00_Manifesto.md](00_Manifesto.md) through [06_Build_Your_App.md](06_Build_Your_App.md) for a complete walkthrough.

### Option 2: Agent-guided Setup
Use the starter prompt above and [AGENTS.md](AGENTS.md) for the current path. Historical workflow guides provide background; verify each operation against [the bootstrap contract](docs/bootstrap.md). Live apply and unattended shared-database migrations remain disabled.

See [Terraform setup](docs/ci-cd/TERRAFORM_SETUP.md) for workflow context.

## 🔐 Credentials by operation

Use native GitHub/Vercel authorization and the existing-project Supabase integration where available. Automation tokens are required only by the operations that use them:

- **Cloudflare DNS automation:** Zone Read and DNS Edit, scoped to the specific existing zone.
- **Vercel management/Terraform:** approved management token/session for the intended account/team.
- **Supabase runtime:** project URL and publishable/legacy anon configuration; privileged credentials only for explicit server needs.
- **Database migrations:** a reviewed connection with app-appropriate privileges. A Supabase management token is separate and required only for management operations that need it.

See [the account and permission checklist](02_Create_Accounts.md). Reuse approved credential sources; GitHub secret names do not expose stored values.

## 📝 Workflow

1. **Use this template** → Generate the app repository.
2. **Connect accounts** → Reuse existing service connections and the shared database.
3. **Configure and validate** → Prepare non-secret app identity and verify credentials safely.
4. **Review the plan** → Confirm ownership, costs and shared-service protection; live apply remains gated.
5. **Approve supported deployment steps** → Make only reviewed app-scoped changes.
6. **Verify** → Check the actual URL, revision, access and database isolation before reporting success.

## 🐛 Troubleshooting

Common issues and solutions:
- [docs/setup/SETUP_HICCUPS.md](docs/setup/SETUP_HICCUPS.md) - All encountered issues
- [docs/setup/TRY_1_SETUP_HICCUPS.md](docs/setup/TRY_1_SETUP_HICCUPS.md) - Additional troubleshooting
- [terraform/README.md](terraform/README.md) - Terraform-specific issues

## 🤝 Contributing

Found a bug or want to improve something? PRs welcome!

## 📄 License

MIT

---

**Built with ❤️ for developers who want to ship fast**

# Free Personal Stack

A reusable Next.js application and infrastructure template using Terraform, Vercel, Supabase, and Cloudflare. Local configuration, safety checks, and deployment verification are implemented; the initializer's live provisioning adapter remains gated. See [current capabilities and safety boundaries](docs/bootstrap.md).

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

Proceed with local setup and verification. At a missing permission or human
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

## 🔐 Required API Tokens

You'll need these tokens (all free tiers available):

### Cloudflare
- API Token with:
  - **Zone:Zone:Read** (required)
  - **Zone:DNS:Edit** (required)
  - Scoped to your zone

### Vercel
- API Token from account settings

### Supabase
- Access Token (personal token)
- Project URL, API keys, Project ID

See [02_Create_Accounts.md](02_Create_Accounts.md) for detailed instructions.

## 📝 Workflow

1. **Fork & Clone** → Get the code
2. **Create Accounts** → Get API tokens
3. **GitHub Secrets** → Store tokens securely
4. **Run Terraform** → Create infrastructure
5. **Verify** → Check dashboard at your domain
6. **Build** → Start creating your app!

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

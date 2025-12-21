# Free Personal Stack

A complete, production-ready infrastructure template for personal projects using Terraform, Vercel, Supabase, and Cloudflare.

## 🚀 Quick Start

1. **Fork this repository**
2. **Create accounts** for Cloudflare, Vercel, and Supabase
3. **Set up GitHub Secrets** (see [TERRAFORM_SETUP.md](.github/TERRAFORM_SETUP.md))
4. **Run Terraform** via GitHub Actions
5. **Deploy your app!**

For detailed setup instructions, see:
- [Blog Series](00_Manifesto.md) - Start here for the full journey
- [Terraform with GitHub Actions](.github/TERRAFORM_SETUP.md) - Complete automation guide
- [Quick Start Guide](QUICK_START.md) - Fastest path to deployment

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
- **[.github/TERRAFORM_SETUP.md](.github/TERRAFORM_SETUP.md)** - Run Terraform in GitHub Actions
- **[.github/SECRETS_CHECKLIST.md](.github/SECRETS_CHECKLIST.md)** - Required secrets checklist
- **[SETUP_HICCUPS.md](SETUP_HICCUPS.md)** - Common issues and solutions
- **[terraform/README.md](terraform/README.md)** - Terraform documentation

## 🎯 What You Get

### Infrastructure (Terraform)
- ✅ **Cloudflare DNS** - Automatic DNS records (root, www, api subdomains)
- ✅ **Email Routing** - Forward support@, contact@, hello@ to your email
- ✅ **Vercel Project** - Auto-created with GitHub integration
- ✅ **Supabase** - Database and authentication (manual project creation)
- ✅ **Environment Variables** - Automatically configured in Vercel

### Application (Next.js)
- ✅ **Verification Dashboard** - Check Domain, Auth, Database, Email status
- ✅ **Supabase Auth** - Ready to use with session management
- ✅ **Database** - PostgreSQL with migrations via GitHub Actions
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

### Option 2: Automated Setup
Use GitHub Actions for everything:
1. Set up secrets in GitHub Environments
2. Run Terraform workflow
3. Infrastructure is ready!

See [.github/TERRAFORM_SETUP.md](.github/TERRAFORM_SETUP.md) for details.

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
- [SETUP_HICCUPS.md](SETUP_HICCUPS.md) - All encountered issues
- [terraform/README.md](terraform/README.md) - Terraform-specific issues

## 🤝 Contributing

Found a bug or want to improve something? PRs welcome!

## 📄 License

MIT

---

**Built with ❤️ for developers who want to ship fast**

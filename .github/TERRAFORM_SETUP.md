# Terraform with GitHub Actions Setup

This guide explains how to run Terraform in GitHub Actions using GitHub Environments and Secrets for complete automation.

## Overview

Instead of running Terraform locally, you can:
- Store all configuration as GitHub Secrets
- Use GitHub Environments for different environments (production, staging)
- Run `terraform plan`, `apply`, or `destroy` directly from GitHub Actions
- Full infrastructure automation without local setup

## Setup Steps

### 1. Create GitHub Environments

1. Go to your repository → **Settings** → **Environments**
2. Click **"New environment"**
3. Create environments:
   - **`production`** - For main branch deployments
   - **`staging`** (optional) - For preview deployments

### 2. Add Secrets to GitHub Environments

For each environment, add these secrets:

#### Required Secrets

| Secret Name | Description | Where to Get It |
|------------|-------------|-----------------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token | [Cloudflare Dashboard → API Tokens](https://dash.cloudflare.com/profile/api-tokens) |
| `VERCEL_API_TOKEN` | Vercel API token | [Vercel Account → Tokens](https://vercel.com/account/tokens) |
| `SUPABASE_URL` | Supabase project URL | Supabase Dashboard → Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Supabase anonymous key | Supabase Dashboard → Settings → API → anon/public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key | Supabase Dashboard → Settings → API → service_role key |
| `SUPABASE_ACCESS_TOKEN` | Supabase access token | [Supabase Dashboard → Account → Access Tokens](https://supabase.com/dashboard/account/tokens) |
| `SUPABASE_PROJECT_ID` | Supabase project ID | Supabase Dashboard → Settings → General → Reference ID |
| `DOMAIN_NAME` | Your domain (e.g., `yourdomain.com` or `subdomain.yourdomain.com`) | Your domain |
| `APP_URL` | Vercel app URL (e.g., `yourproject.vercel.app`) | Your Vercel project |
| `VERCEL_PROJECT_NAME` | Vercel project name | Your Vercel project name |
| `GITHUB_REPO` | GitHub repository (e.g., `username/repo`) | Your GitHub repository |

#### Optional Secrets

| Secret Name | Description | Default |
|------------|-------------|---------|
| `SITE_URL` | Production site URL | `https://{DOMAIN_NAME}` |
| `SUPPORT_EMAIL_DESTINATION` | Email forwarding destination | - |
| `CONTACT_EMAIL_DESTINATION` | Email forwarding destination | - |
| `VERCEL_TEAM_ID` | Vercel team ID (if using team) | - |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID | - |
| `CLOUDFLARE_ZONE_NAME` | Parent zone name (for subdomains) | - |
| `ENABLE_EMAIL_ROUTING` | Enable email routing | `true` |
| `CREATE_CLOUDFLARE_ZONE` | Create Cloudflare zone if missing | `false` |
| `ENABLE_SUPABASE_PROJECT_DELETION` | Auto-delete Supabase on destroy | `true` |

### 3. How to Add Secrets

**Option A: Via GitHub UI**
1. Go to repository → **Settings** → **Environments**
2. Click on an environment (e.g., `production`)
3. Click **"Add secret"**
4. Enter name and value
5. Click **"Add secret"**

**Option B: Via GitHub CLI**
```bash
gh secret set CLOUDFLARE_API_TOKEN --env production --body "your-token"
gh secret set VERCEL_API_TOKEN --env production --body "your-token"
# ... repeat for all secrets
```

### 4. Understanding GitHub Environments

**Environments provide:**
- **Separate secrets** per environment (production vs staging)
- **Protection rules** (required reviewers, deployment branches)
- **Deployment history** and approval workflows

**In the workflow:**
- Pushes to `main` → uses `production` environment
- Other branches → uses `staging` environment (if configured)

### 5. Running Terraform

#### Manual Run (Recommended for first setup)

1. Go to repository → **Actions** → **Terraform Infrastructure**
2. Click **"Run workflow"**
3. Select command:
   - **`plan`** - Preview changes (safe, read-only)
   - **`apply`** - Apply changes (modifies infrastructure)
   - **`destroy`** - Destroy infrastructure (⚠️ destructive)
4. Select branch (usually `main`)
5. Click **"Run workflow"**

#### Automatic Plan on Push

The workflow automatically runs `terraform plan` when:
- You push changes to `terraform/` directory
- You push to `main` branch

This shows you what would change without applying.

#### Automatic Apply

To enable automatic apply on push to main:
- Modify the workflow to run `apply` instead of `plan` for push events
- **⚠️ Warning:** This automatically changes infrastructure on every push

## State Management

**Default Setup:** Terraform state is stored as **GitHub Actions artifacts** - no configuration needed!

- ✅ Works immediately - no setup required
- ✅ State persists between workflow runs
- ✅ Access controlled (only repo collaborators can access)
- ⚠️ **Security:** Artifacts are accessible in plaintext to anyone with repo access
- ⚠️ No state locking (don't run multiple workflows simultaneously)

**Note:** For stronger encryption, see [ARTIFACT_STATE_SETUP.md](../terraform/ARTIFACT_STATE_SETUP.md) for encrypted artifact options.

📖 **See [terraform/ARTIFACT_STATE_SETUP.md](../terraform/ARTIFACT_STATE_SETUP.md) for details.**

**For Production:** If you need proper state locking and history, configure a remote backend:
- 📖 [terraform/BACKEND_SETUP.md](../terraform/BACKEND_SETUP.md) - Terraform Cloud or AWS S3

### Quick Recommendation

**Use Terraform Cloud** (free tier) - it's the easiest option:
1. Sign up at [app.terraform.io](https://app.terraform.io)
2. Create workspace (API-driven workflow)
3. Add backend config to `terraform/providers.tf`
4. Add `TF_TOKEN_app_terraform_io` secret to GitHub

See the [backend setup guide](../terraform/BACKEND_SETUP.md) for detailed steps.

## Workflow Features

### Protected Actions

You can add protection rules in GitHub Environments:
- **Required reviewers** - Require approval before apply/destroy
- **Deployment branches** - Only allow certain branches
- **Wait timer** - Delay before applying (prevents accidental deploys)

### Manual Approval

1. Go to repository → **Settings** → **Environments** → **production**
2. Enable **"Required reviewers"**
3. Add yourself as a reviewer
4. When workflow runs `apply`, you'll get a notification to approve

### Deployment Branches

Restrict deployments to `main` only:
1. Go to repository → **Settings** → **Environments** → **production**
2. Under **"Deployment branches"**, select **"Selected branches"**
3. Add `main`

## Complete Secret List

## Minimum Required Secrets

Only these secrets are **required** - everything else is auto-inferred:

```bash
# Cloudflare (Required)
CLOUDFLARE_API_TOKEN

# Vercel (Required)
VERCEL_API_TOKEN

# Supabase (Required)
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_ACCESS_TOKEN
SUPABASE_PROJECT_ID

# Domain (Required)
DOMAIN_NAME

# State Encryption (Required)
TERRAFORM_STATE_PASSWORD
```

## Auto-Inferred (No Need to Set!)

These are automatically inferred from context:

- `GITHUB_REPO` - From `github.repository` context
- `VERCEL_PROJECT_NAME` - From domain name (e.g., `vbf.alfonsograna.com` → `vbf-alfonsograna-com`)
- `APP_URL` - From project name (e.g., `vbf-alfonsograna-com.vercel.app`)
- `SITE_URL` - From domain (e.g., `https://vbf.alfonsograna.com`)

## Optional Secrets

Only set these if you need to override the defaults:

```bash
# Cloudflare (Optional)
CLOUDFLARE_ACCOUNT_ID  # For team accounts
CLOUDFLARE_ZONE_NAME   # If auto-detection fails

# Vercel (Optional)
VERCEL_TEAM_ID         # For team accounts
VERCEL_PROJECT_NAME    # Override auto-generated name
APP_URL                # Override auto-generated URL

# Domain (Optional)
SITE_URL               # Override default (defaults to https://{DOMAIN_NAME})

# Email Routing (Optional)
SUPPORT_EMAIL_DESTINATION
CONTACT_EMAIL_DESTINATION

# Feature Flags (Optional)
ENABLE_EMAIL_ROUTING (default: true)
CREATE_CLOUDFLARE_ZONE (default: false)
ENABLE_SUPABASE_PROJECT_DELETION (default: true)
```

## Example: Setting Up Production Environment

1. **Create environment:**
   - Settings → Environments → New environment → `production`

2. **Add all secrets** (see list above)

3. **Set protection rules:**
   - Required reviewers: Yourself
   - Deployment branches: Only `main`

4. **Test with plan:**
   - Actions → Terraform Infrastructure → Run workflow
   - Select `plan` command
   - Review the output

5. **Apply infrastructure:**
   - Actions → Terraform Infrastructure → Run workflow
   - Select `apply` command
   - Approve if required reviewers are enabled

## Troubleshooting

### "Secret not found" error

- Verify secret name matches exactly (case-sensitive)
- Check you're using the correct environment
- Ensure secret is added to the environment, not just repository secrets

### "Authentication error" from Cloudflare/Vercel/Supabase

- Verify API tokens are correct and not expired
- Check token permissions match requirements
- Recreate tokens if needed

### State lock errors

- Another Terraform run is in progress
- Wait for the previous run to complete
- If stuck, manually unlock: `terraform force-unlock <lock-id>`

## Security Best Practices

1. **Never commit secrets** - Always use GitHub Secrets
2. **Use environments** - Separate secrets for prod/staging
3. **Enable protection rules** - Require approvals for destructive operations
4. **Limit permissions** - Use least-privilege API tokens
5. **Audit logs** - Review GitHub Actions logs regularly
6. **Rotate secrets** - Update tokens periodically

## Migration from Local to GitHub Actions

If you've been running Terraform locally:

1. **Backup current state:**
   ```bash
   cd terraform
   cp terraform.tfstate terraform.tfstate.local.backup
   ```

2. **Set up remote backend** (see [BACKEND_SETUP.md](../terraform/BACKEND_SETUP.md))
   - Configure backend in `terraform/providers.tf`
   - Add backend credentials to GitHub Secrets

3. **Migrate state:**
   ```bash
   cd terraform
   terraform init
   # Confirm migration when prompted
   ```

4. **First run in GitHub Actions:**
   - Run `plan` first to verify it sees your existing resources
   - Should show "No changes" if migration was successful

## Next Steps

After setting up:
1. Run `terraform plan` via workflow to verify configuration
2. Run `terraform apply` to create/update infrastructure
3. Monitor deployments in Actions tab
4. Set up protection rules for production

---

**Now you can manage your entire infrastructure from GitHub! 🚀**


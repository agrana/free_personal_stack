# Minimum Required Secrets

This document shows the **absolute minimum** secrets you need to configure for GitHub Actions Terraform workflow.

## Only 8 Required Secrets!

### 1. Cloudflare
- `CLOUDFLARE_API_TOKEN` - API token with Zone:Zone:Read + Zone:DNS:Edit permissions

### 2. Vercel
- `VERCEL_API_TOKEN` - API token from Vercel account

### 3. Supabase (5 secrets)
- `SUPABASE_URL` - Project URL (https://xxx.supabase.co)
- `SUPABASE_ANON_KEY` - Anonymous/public API key
- `SUPABASE_SERVICE_ROLE_KEY` - Service role key
- `SUPABASE_ACCESS_TOKEN` - Personal access token for CLI
- `SUPABASE_PROJECT_ID` - Project reference ID

### 4. Domain
- `DOMAIN_NAME` - Your domain (e.g., `vbf.alfonsograna.com`)

### 5. State Encryption
- `TERRAFORM_STATE_PASSWORD` - Password for encrypting state (generate with `openssl rand -base64 32`)

**That's it! Just 8 secrets.**

## Auto-Inferred (No Secrets Needed!)

The following are automatically inferred - you don't need to set them:

| What | How It's Inferred |
|------|------------------|
| `GITHUB_REPO` | From `github.repository` context (e.g., `agrana/free_personal_stack`) |
| `VERCEL_PROJECT_NAME` | From `DOMAIN_NAME` - sanitized (e.g., `vbf.alfonsograna.com` → `vbf-alfonsograna-com`) |
| `APP_URL` | From `VERCEL_PROJECT_NAME` (e.g., `vbf-alfonsograna-com.vercel.app`) |
| `SITE_URL` | From `DOMAIN_NAME` (e.g., `https://vbf.alfonsograna.com`) |

## Examples

### Example 1: Minimal Setup
```bash
# Only these 8 secrets required:
CLOUDFLARE_API_TOKEN=cf_xxxxx
VERCEL_API_TOKEN=xxxxx
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJxxxxx
SUPABASE_SERVICE_ROLE_KEY=eyJxxxxx
SUPABASE_ACCESS_TOKEN=xxxxx
SUPABASE_PROJECT_ID=xxxxx
DOMAIN_NAME=vbf.alfonsograna.com
TERRAFORM_STATE_PASSWORD=Kx8mP2vQ9rT4nL6jH8fD1sA3bC5eG7hI9jK0lM2nP4qR6sT8uV0wX2yZ4aB6cD
```

**Auto-inferred:**
- `GITHUB_REPO` = `agrana/free_personal_stack` (from context)
- `VERCEL_PROJECT_NAME` = `vbf-alfonsograna-com` (from domain)
- `APP_URL` = `vbf-alfonsograna-com.vercel.app` (from project name)
- `SITE_URL` = `https://vbf.alfonsograna.com` (from domain)

### Example 2: With Overrides
If you want to customize, you can override:

```bash
# Required secrets (same as above)
CLOUDFLARE_API_TOKEN=cf_xxxxx
# ... etc

# Optional overrides
VERCEL_PROJECT_NAME=my-custom-project-name  # Override auto-generated name
APP_URL=my-custom-project-name.vercel.app   # Override auto-generated URL
```

## Quick Setup Checklist

- [ ] `CLOUDFLARE_API_TOKEN`
- [ ] `VERCEL_API_TOKEN`
- [ ] `SUPABASE_URL`
- [ ] `SUPABASE_ANON_KEY`
- [ ] `SUPABASE_SERVICE_ROLE_KEY`
- [ ] `SUPABASE_ACCESS_TOKEN`
- [ ] `SUPABASE_PROJECT_ID`
- [ ] `DOMAIN_NAME`
- [ ] `TERRAFORM_STATE_PASSWORD`

That's only **9 checkboxes** instead of 20+! 🎉


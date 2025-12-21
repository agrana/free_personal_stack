# GitHub Secrets Checklist

Use this checklist to ensure all required secrets are configured in your GitHub Environments.

## Quick Setup

1. Go to repository → **Settings** → **Environments**
2. Create `production` environment (and optionally `staging`)
3. Add all secrets from the list below
4. Test with `terraform plan` workflow

## Required Secrets (Minimum)

These are the **absolute minimum** secrets you need:

### Cloudflare
- [ ] `CLOUDFLARE_API_TOKEN` - API token with Zone:Zone:Read + Zone:DNS:Edit permissions

### Vercel
- [ ] `VERCEL_API_TOKEN` - API token from Vercel account

### Supabase
- [ ] `SUPABASE_URL` - Project URL (https://xxx.supabase.co)
- [ ] `SUPABASE_ANON_KEY` - Anonymous/public API key
- [ ] `SUPABASE_SERVICE_ROLE_KEY` - Service role key (keep secret!)
- [ ] `SUPABASE_ACCESS_TOKEN` - Personal access token for CLI
- [ ] `SUPABASE_PROJECT_ID` - Project reference ID

### Domain
- [ ] `DOMAIN_NAME` - Your domain (e.g., `yourdomain.com` or `subdomain.domain.com`)

### Terraform State Encryption
- [ ] `TERRAFORM_STATE_PASSWORD` - Password for encrypting/decrypting Terraform state files

## Optional Secrets (Auto-Inferred if Not Set)

These will be automatically inferred if you don't set them:

- `GITHUB_REPO` - **Auto-inferred** from `github.repository` context (no need to set!)
- `VERCEL_PROJECT_NAME` - **Auto-inferred** from domain name (sanitized, e.g., `vbf.alfonsograna.com` → `vbf-alfonsograna-com`)
- `APP_URL` - **Auto-inferred** from vercel project name (e.g., `vbf-alfonsograna-com.vercel.app`)
- `SITE_URL` - **Auto-inferred** as `https://{DOMAIN_NAME}`

## Additional Optional Secrets

### Cloudflare
- [ ] `CLOUDFLARE_ACCOUNT_ID` - Account ID for team accounts
- [ ] `CLOUDFLARE_ZONE_NAME` - Parent zone name for subdomains (usually auto-detected)

### Vercel
- [ ] `VERCEL_TEAM_ID` - Team ID if using team account
- [ ] `VERCEL_PROJECT_NAME` - Override auto-generated project name
- [ ] `APP_URL` - Override auto-generated app URL

### Domain & URLs
- [ ] `SITE_URL` - Override default site URL (defaults to `https://{DOMAIN_NAME}`)

### Email Routing (Optional)
- [ ] `SUPPORT_EMAIL_DESTINATION` - Real email for support@ forwarding
- [ ] `CONTACT_EMAIL_DESTINATION` - Real email for contact@ and hello@ forwarding

### Feature Flags (Optional)
- [ ] `ENABLE_EMAIL_ROUTING` - Enable email routing (default: `true`)
- [ ] `CREATE_CLOUDFLARE_ZONE` - Create zone if missing (default: `false`)
- [ ] `ENABLE_SUPABASE_PROJECT_DELETION` - Auto-delete on destroy (default: `true`)

## Secret Naming Convention

Terraform variables are passed via `TF_VAR_` prefix:
- Secret name: `CLOUDFLARE_API_TOKEN`
- Terraform variable: `cloudflare_api_token`
- Environment variable in workflow: `TF_VAR_cloudflare_api_token`

## Validation

After adding secrets, verify:
1. Run workflow → `plan` command
2. Check for "secret not found" errors
3. Review plan output for correct configuration

## Security Notes

- ⚠️ Never commit secrets to repository
- ✅ Use GitHub Environments for different environments
- ✅ Enable protection rules for production
- ✅ Use least-privilege tokens
- ✅ Rotate tokens regularly


# Setup Flow Verification

This document verifies the complete setup flow for someone using this template.

## Flow Overview

### 1. Initial Repository Setup

**Trigger:** User creates repository from template (initial commit)

**What Happens:**
1. `template-setup.yml` workflow runs automatically
   - Creates `production` environment
   - Generates `TERRAFORM_STATE_PASSWORD` (if GH_PAT available)
   - Creates welcome issue with all required secrets listed
   - Triggers `setup.yml` in `auto-setup` mode

2. `setup.yml` workflow runs in `auto-setup` mode
   - Checks which secrets exist and which are missing
   - Creates/updates issue with missing secrets
   - **Fails gracefully** if secrets are missing (expected behavior)

**Expected State:**
- ✅ Production environment created
- ✅ Welcome issue created with secret checklist
- ⚠️ Secrets not yet configured (workflows will fail until configured)

### 2. User Configures Secrets

**User Action:** Follows issue instructions to add secrets

**Steps:**
1. Go to Settings → Environments → production
2. Add all required secrets from the issue
3. Optionally run "Repository Setup" → "validate" mode to verify

**Required Secrets:**
- `CLOUDFLARE_API_TOKEN`
- `VERCEL_API_TOKEN`
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_PROJECT_ID`
- `SUPABASE_DATABASE_PASSWORD` (for migrations)
- `DOMAIN_NAME`
- `TERRAFORM_STATE_PASSWORD` (auto-generated if GH_PAT available)

**Expected State:**
- ✅ All secrets configured
- ✅ Validation workflow passes

### 3. User Runs Terraform

**User Action:** Go to Actions → Terraform Infrastructure → Run workflow

**What Happens:**
1. `terraform.yml` workflow runs
   - Validates setup is complete (not initial commit)
   - Validates required secrets are present
   - Downloads encrypted state (if exists)
   - Runs `terraform plan`
   - Uploads plan artifact

2. User reviews plan output in PR comment or workflow logs

3. User runs `apply` command
   - Requires manual approval (if configured)
   - Creates infrastructure:
     - Cloudflare DNS records
     - Vercel project (linked to GitHub)
     - Environment variables in Vercel
     - Email routing (if enabled)
   - Uploads encrypted state

**Expected State:**
- ✅ Infrastructure created
- ✅ Vercel project linked to GitHub
- ✅ Environment variables set in Vercel
- ✅ DNS configured

### 4. User Runs Supabase Migrations

**User Action:** Push to main or manually trigger deploy workflow

**What Happens:**
1. `deploy.yml` workflow runs
   - Validates required secrets are present
   - Links to Supabase project
   - Runs `supabase db push` to apply migrations
   - Verifies migrations applied

**Expected State:**
- ✅ Database schema deployed
- ✅ Migrations applied

### 5. Verification App Works

**What Happens:**
1. Vercel automatically deploys app (via GitHub integration)
2. App connects to Supabase using env vars (set by Terraform)
3. User can access verification dashboard
4. Dashboard verifies:
   - Domain configuration
   - Authentication
   - Database connection
   - Email routing

**Expected State:**
- ✅ App deployed and accessible
- ✅ All verification checks pass
- ✅ Ready for development

## Verification Checklist

- [ ] Initial commit triggers setup workflows
- [ ] Welcome issue created with secret checklist
- [ ] Setup workflows fail gracefully if secrets missing
- [ ] Terraform workflow validates secrets before running
- [ ] Supabase workflow validates secrets before running
- [ ] Terraform can be triggered manually after secrets configured
- [ ] Supabase migrations can run after secrets configured
- [ ] App deploys automatically after Terraform apply
- [ ] Verification dashboard works end-to-end

## Current Status

✅ **Working:**
- Template setup workflow creates issue
- Setup workflow validates secrets
- Terraform workflow skips initial commit
- Terraform workflow validates secrets before running
- Supabase workflow validates secrets before running
- Both workflows fail gracefully with clear error messages
- Flow documentation created

✅ **Verified Flow:**
1. Initial commit → Setup workflows run → Issue created
2. User adds secrets → Validation passes
3. User runs Terraform → Infrastructure created
4. User runs Supabase migrations → Schema deployed
5. App auto-deploys → Verification dashboard works


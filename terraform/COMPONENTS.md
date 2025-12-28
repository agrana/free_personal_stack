# Terraform Components Overview

This document shows all Terraform components defined in the infrastructure and their current state.

## Component Modules

### 1. DNS Module (`modules/dns`)
**Purpose:** Manages Cloudflare DNS records for the domain

**Resources:**
- `cloudflare_record.root` - Root domain CNAME record (points to app URL)
- `cloudflare_record.www` - WWW subdomain CNAME record
- `cloudflare_record.api` - API subdomain CNAME record

**State Location:** Managed via Terraform state in GitHub Actions artifacts

### 2. Vercel Module (`modules/vercel`)
**Purpose:** Manages Vercel project and deployment configuration

**Resources:**
- `vercel_project.main` - Vercel project configuration
- `vercel_project_domain.main` - Custom domain configuration
- `vercel_project_environment_variable.env_vars` - Environment variables (for_each)

**State Location:** Managed via Terraform state in GitHub Actions artifacts

### 3. Supabase Module (`modules/supabase`)
**Purpose:** Manages Supabase project lifecycle

**Resources:**
- `null_resource.delete_supabase_project` - Handles project deletion via CLI (conditional)

**Note:** Supabase project creation is not managed by Terraform (must be created manually)

**State Location:** Managed via Terraform state in GitHub Actions artifacts

### 4. Email Routing Module (`modules/email_routing`)
**Purpose:** Manages Cloudflare email routing rules

**Resources:**
- `cloudflare_email_routing_settings.email_routing` - Email routing settings (conditional)
- `cloudflare_email_routing_rule.support` - Support email forwarding rule
- `cloudflare_email_routing_rule.contact` - Contact email forwarding rule
- `cloudflare_email_routing_rule.hello` - Hello email forwarding rule

**State Location:** Managed via Terraform state in GitHub Actions artifacts

### 5. Cloudflare Zone (Root)
**Purpose:** Manages Cloudflare zone (optional - can use existing zone)

**Resources:**
- `data.cloudflare_zone.existing` - References existing zone (when not creating)
- `cloudflare_zone.new` - Creates new zone (when creating)

**State Location:** Managed via Terraform state in GitHub Actions artifacts

## State Management

### Current Implementation
- **Storage:** GitHub Actions artifacts (encrypted)
- **Encryption:** AES-256-CBC with PBKDF2 key derivation
- **Retention:** 90 days
- **Location:** `.github/workflows/terraform.yml`

### State Visibility
State information is displayed in GitHub Actions workflow summaries:
- Resource counts (managed, data sources, null resources)
- Resource type list
- Terraform state list output
- State file preview

### Viewing Current State

**Via GitHub Actions:**
1. Go to repository → Actions
2. Find the most recent successful "Terraform Plan" or "Terraform Apply" run
3. Check the "Verify State File After Init" step summary
4. View the "Resource Types in State" and "Terraform State List" sections

**State File Location in Workflow:**
- Encrypted: `terraform/terraform.tfstate.encrypted`
- Decrypted (during workflow): `terraform/terraform.tfstate`
- Artifact name: `terraform-state`

## Component Status Tracking

To check the current state of components:

1. **Run Terraform Plan** - Shows what resources exist vs what should exist
2. **Check GitHub Actions Summary** - Shows state after each plan/apply
3. **View State Artifact** - Download and decrypt the state artifact (requires `TERRAFORM_STATE_PASSWORD`)

## Expected Resources in State

When fully deployed, the state should contain:

### Cloudflare Resources
- 1 zone (data source or resource)
- 3 DNS records (root, www, api)
- 3-4 email routing rules (support, contact, hello, + optional settings)

### Vercel Resources
- 1 project
- 1 custom domain
- 4+ environment variables (NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, etc.)

### Supabase Resources
- 0-1 null_resource (for deletion handling)

**Total Expected Managed Resources:** ~10-15 resources (excluding data sources and null resources)

## Troubleshooting State Issues

If state is not being found or is inconsistent:

1. **Check Artifact Availability:**
   - Verify `terraform-state` artifact exists in recent workflow runs
   - Check that apply job succeeded (not just plan)

2. **Verify Encryption:**
   - Ensure `TERRAFORM_STATE_PASSWORD` secret is set
   - Check decryption step logs in workflow

3. **State Inconsistency:**
   - If resources exist but aren't in state: use `terraform import`
   - If state has resources that don't exist: verify with `terraform plan`

4. **Empty State:**
   - Normal for first run
   - If state should exist but is empty, check artifact download/decryption steps





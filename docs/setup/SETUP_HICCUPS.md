# Setup Hiccups and Solutions

This document summarizes all the issues encountered during the setup process and how they were resolved. This can serve as a troubleshooting reference for future setups.

## 1. Cloudflare API Token: Missing Zone:Zone:Read Permission

### Problem
```
Error: no zone found
data.cloudflare_zone.existing[0]: Reading...
Error: no zone found
```

Terraform couldn't find the Cloudflare zone even though the domain existed.

### Root Cause
The Cloudflare API token was missing the `Zone:Zone:Read` permission. The user initially only gave `Zone:DNS:Edit`, thinking it would include read permissions.

### Solution
- Added `Zone:Zone:Read` permission to the Cloudflare API token
- Updated documentation to explicitly state that `Zone:Zone:Read` and `Zone:DNS:Edit` are **separate** permissions, both required

### Lesson Learned
Cloudflare permissions are granular - `Zone:DNS:Edit` does NOT include `Zone:Zone:Read`. You need both:
- `Zone:Zone:Read` - to query and look up zones
- `Zone:DNS:Edit` - to create/modify DNS records

---

## 2. Cloudflare DNS: Missing Zone:DNS:Edit or Zone Scope Issue

### Problem
```
Error: failed to create DNS record: Authentication error (10000)
```

After fixing the Zone:Zone:Read issue, DNS record creation still failed.

### Root Cause
The API token either:
- Was missing `Zone:DNS:Edit` permission
- Was not scoped to the correct zone in "Zone Resources"
- Had `DNS Settings:Edit` instead of `Zone:DNS:Edit` (different permissions)

### Solution
- Verified token has `Zone:DNS:Edit` (not `DNS Settings:Edit`)
- Ensured token was scoped to the zone under "Zone Resources" → "Include" → "Specific zone" → selected `alfonsograna.com`
- Updated documentation to clarify zone scoping requirements

### Lesson Learned
- Token must be scoped to the zone in "Zone Resources"
- `Zone:DNS:Edit` is different from `DNS Settings:Edit`
- Error (10000) usually means missing permissions or incorrect zone scoping

---

## 3. Supabase Migration: Wrong Command Syntax

### Problem
```
Usage: supabase db push [flags]
```

The GitHub Actions workflow was using:
```bash
npx supabase db push --project-id ${{ secrets.SUPABASE_PROJECT_ID }}
```

But the Supabase CLI doesn't accept `--project-id` flag for `db push`.

### Root Cause
The command syntax was incorrect. The `supabase db push` command expects the project to be linked first, or to use `--project-ref` with `link`, not `--project-id` with `push`.

### Solution
Changed the workflow to:
1. First link the project: `npx supabase link --project-ref ${{ secrets.SUPABASE_PROJECT_ID }} --password "$SUPABASE_DB_PASSWORD"`
2. Then push migrations: `npx supabase db push`

### Lesson Learned
Supabase CLI requires:
- `link` command to associate local project with remote (uses `--project-ref`)
- `db push` to apply migrations (no project flag needed after linking)

---

## 4. Supabase Migration: Password Authentication Failed

### Problem
```
failed to connect to postgres: failed SASL auth (FATAL: password authentication failed for user "postgres" (SQLSTATE 28P01))
```

The workflow couldn't authenticate to the Supabase database.

### Root Cause
Multiple issues:
1. `SUPABASE_ACCESS_TOKEN` was incorrectly set to the Supabase service role key instead of a personal access token
2. The database password wasn't being passed correctly to the `supabase link` command

### Solution
1. Clarified the distinction between:
   - `SUPABASE_ACCESS_TOKEN`: Personal access token from Account → Access Tokens (for CLI/API)
   - `SUPABASE_SERVICE_ROLE_KEY`: Project API key from Project Settings → API Keys (for app code)
2. Added `SUPABASE_DB_PASSWORD` as a GitHub secret
3. Updated workflow to pass password via environment variable and `--password` flag
4. Updated documentation to clearly explain the difference

### Lesson Learned
- Access Token ≠ Service Role Key
- Access Token: Personal token for CLI/API operations
- Service Role Key: Project API key for application code
- Database Password: Separate credential set when creating the project

---

## 5. Supabase Migration: Connection Timeouts and Circuit Breaker

### Problem
```
failed to connect to postgres: failed to receive message (unexpected EOF)
failed to connect to postgres: failed to receive message (timeout: context deadline exceeded)
Circuit breaker open: Unable to establish connection to upstream database (SQLSTATE XX000)
```

Connection attempts to Supabase database kept failing with various network/timeout errors.

### Root Cause
This was a Supabase infrastructure issue:
- Database might have been paused
- Connection pooler issues
- Temporary network problems on Supabase's side

### Solution
1. Added retry logic to the GitHub Actions workflow (3 attempts with 5-second delays)
2. Advised user to:
   - Check Supabase Dashboard for project status
   - Restart the Supabase project if paused
   - Check network restrictions
   - Wait and retry (temporary infrastructure issues)
3. Eventually, the connection worked after some time/project status changes

### Lesson Learned
- Some issues are infrastructure-related (Supabase side)
- Retry logic helps with transient connection issues
- Check project status in dashboard if connections fail

---

## 6. Email Routing Rules: Provider Update Limitations

### Problem
```
Error: failed updating email routing rule
required rule id missing

Error: failed to create email routing rule
Duplicated Zone rule (2014)
```

Terraform couldn't update existing email routing rules or create new ones (they already existed in Cloudflare).

### Root Cause
The Cloudflare Terraform provider has a known limitation with email routing rules:
- When updating, it doesn't properly send the rule ID to Cloudflare API
- When rules exist in Cloudflare but not in Terraform state, it tries to create duplicates

### Solution
1. Imported existing rules into Terraform state:
   ```bash
   terraform import 'module.email_routing[0].cloudflare_email_routing_rule.support' 'ZONE_ID/RULE_ID'
   terraform import 'module.email_routing[0].cloudflare_email_routing_rule.hello' 'ZONE_ID/RULE_ID'
   ```

2. When updates still failed due to provider limitations:
   - Removed rules from Terraform state: `terraform state rm ...`
   - Deleted rules manually from Cloudflare Dashboard
   - Let Terraform recreate them fresh: `terraform apply`

3. Removed `lifecycle { create_before_destroy = true }` as it didn't help with this issue

### Lesson Learned
- Cloudflare provider has limitations with email routing rule updates
- Sometimes deleting and recreating is the only viable solution
- For existing rules, import them first if you want Terraform to manage them
- Manual deletion + Terraform recreation works reliably

---

## 7. Google OAuth Variables: Required but Not Provided

### Problem
```
Error: No value for required variable
variable "google_client_id" is not set, and has no default value
variable "google_client_secret" is not set, and has no default value
```

Terraform required Google OAuth variables even though they weren't being used.

### Root Cause
The variables were defined as required (no default value) but weren't provided in `terraform.tfvars` since Google OAuth wasn't being used.

### Solution
Made the variables optional by adding default empty strings:
```hcl
variable "google_client_id" {
  description = "Google OAuth client ID"
  type        = string
  sensitive   = true
  default     = ""  # Added this
}
```

### Lesson Learned
- Optional features should have default values
- Makes the template more flexible for users who don't need all features

---

## 8. Vercel Deployment: Unknown Secret Error

### Problem
```
Error: Unknown Secret
Reference to unknown secret: supabase_url
```

Vercel deployment failed with "Unknown Secret" errors for environment variables.

### Root Cause
The `vercel.json` file was using `@secret` syntax to reference Vercel secrets:
```json
{
  "env": {
    "NEXT_PUBLIC_SUPABASE_URL": "@supabase_url"
  }
}
```

But Terraform was already managing these environment variables directly in Vercel, so the secrets didn't exist.

### Solution
Removed the `env` section from `vercel.json` since Terraform manages all environment variables in Vercel directly via the `vercel_project_environment_variable` resource.

### Lesson Learned
- Don't duplicate environment variable management
- Since Terraform sets variables in Vercel, `vercel.json` shouldn't also reference them
- Choose one method: either Terraform OR vercel.json, not both

---

## Summary of Solutions Applied

1. **Cloudflare Token Permissions**: Added both `Zone:Zone:Read` and `Zone:DNS:Edit`, ensured proper zone scoping
2. **Supabase CLI Commands**: Fixed command syntax (link first, then push)
3. **Supabase Authentication**: Clarified token types, added password to GitHub secrets
4. **Database Connections**: Added retry logic for transient issues
5. **Email Routing**: Delete and recreate approach for provider limitations
6. **Optional Variables**: Made Google OAuth variables optional with defaults
7. **Vercel Config**: Removed duplicate environment variable definitions

---

## Documentation Updates Made

All these issues led to comprehensive documentation updates:

1. **Cloudflare Token Setup**: Detailed step-by-step with explicit permission requirements
2. **Token Distinctions**: Clear explanations of Access Token vs Service Role Key
3. **Troubleshooting Sections**: Added to README, QUICK_START, and blog posts
4. **Time Estimates**: Updated from "10 minutes" to realistic "30-60 minutes"
5. **Common Errors**: Documented with solutions for each major issue

---

## Prevention Tips for Future Setups

1. **Read permission requirements carefully** - Cloudflare permissions are granular
2. **Verify token scoping** - Ensure tokens are scoped to correct zones/resources
3. **Distinguish token types** - Access tokens ≠ API keys ≠ service role keys
4. **Check provider limitations** - Some resources (like email routing) have known issues
5. **Test incrementally** - Don't try to set everything up at once
6. **Use documentation** - Keep troubleshooting notes updated as issues are discovered


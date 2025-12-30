# Merge Checklist: vbf/ → Main Template

This checklist outlines what should be merged from the `vbf/` deployment back to the main `free_personal_stack` template repository.

## ✅ New Files to Add

### Documentation
- [ ] `06_Build_Your_App.md` - Complete guide on building features from scratch
- [ ] `SETUP_HICCUPS.md` - Troubleshooting guide with all issues we encountered

### Configuration
- [ ] `vercel.json` - Fixed (removed env section that conflicted with Terraform)

## ✅ Updated Files to Merge

### GitHub Actions
- [ ] `.github/workflows/deploy.yml` - Fixed Supabase migration workflow:
  - Added `Link Supabase project` step with retry logic
  - Added `SUPABASE_DB_PASSWORD` secret
  - Changed from `--project-id` to `link --project-ref` + `db push`

### Documentation Improvements

#### Blog Posts / Guides
- [ ] `02_Create_Accounts.md` - Enhanced Cloudflare token setup:
  - Detailed step-by-step token creation
  - Explicit permission requirements (Zone:Zone:Read + Zone:DNS:Edit)
  - Zone Resources scoping instructions
  - Clarified Access Token vs Service Role Key distinction
  
- [ ] `03_Local_Setup.md` - More detailed setup:
  - Better time estimates (10-15 minutes instead of 5)
  - Clarified SUPABASE_DB_PASSWORD requirement
  - Better token distinction explanations

- [ ] `04_Deploy.md` - Updated time estimates (20-30 minutes)

- [ ] `README.md` - Improved:
  - Realistic time estimates (30-60 minutes)
  - Enhanced troubleshooting section
  - Better Cloudflare token instructions
  - Links to troubleshooting resources

- [ ] `QUICK_START.md` - Enhanced:
  - Realistic time estimates
  - Comprehensive troubleshooting section
  - Detailed Cloudflare token setup
  - Common errors and solutions

- [ ] `05_What_You_Got.md` - Should reference new Part 6 guide

### Terraform Configuration

- [ ] `terraform/variables.tf` - Made Google OAuth variables optional:
  ```hcl
  variable "google_client_id" {
    default = ""  # Added default
  }
  variable "google_client_secret" {
    default = ""  # Added default
  }
  ```

- [ ] `terraform/modules/email_routing/main.tf` - Removed lifecycle block (didn't help with update issues)

- [ ] `terraform/modules/vercel/main.tf` - Already uses resource instead of data (should be same)

- [ ] `terraform/README.md` - Enhanced Cloudflare token setup instructions (if updated)

### App Code

- [ ] Check if any app code improvements were made (probably minimal, but worth checking)

## ⚠️ Files to EXCLUDE (Do NOT merge)

These are deployment-specific and should stay in vbf/:

- `terraform/terraform.tfstate*` - State files (deployment-specific)
- `terraform/terraform.tfvars` - Contains actual credentials
- `.env.local` - Local environment variables
- `supabase/.temp/` - Temporary Supabase files

## 📋 Merge Process

### Option 1: Manual Copy (Recommended for first merge)

1. **Copy new files:**
   ```bash
   cp vbf/06_Build_Your_App.md .
   cp vbf/SETUP_HICCUPS.md .
   cp vbf/vercel.json .
   ```

2. **Update existing files:**
   - Review differences in each file
   - Merge improvements manually
   - Test that everything still works

3. **Commit and push:**
   ```bash
   git add .
   git commit -m "Merge improvements from vbf deployment

   - Add Build Your App guide (Part 6)
   - Add Setup Hiccups troubleshooting guide
   - Fix Supabase migration workflow
   - Improve Cloudflare token documentation
   - Make Google OAuth variables optional
   - Update time estimates to be more realistic
   - Enhance troubleshooting sections"
   git push origin main
   ```

### Option 2: Selective Git Merge

If vbf/ was a separate git repo/branch:

```bash
# Create a branch for the merge
git checkout -b merge-vbf-improvements

# Merge specific files
git checkout vbf -- .github/workflows/deploy.yml
git checkout vbf -- 06_Build_Your_App.md
git checkout vbf -- SETUP_HICCUPS.md
# ... etc

# Review and commit
git add .
git commit -m "Merge improvements from vbf deployment"
git push origin merge-vbf-improvements

# Create PR and review
```

## ✅ Verification Checklist

After merging, verify:

- [ ] GitHub Actions workflow works (test migration)
- [ ] Documentation is accurate
- [ ] Terraform variables are compatible
- [ ] All links in docs work
- [ ] No sensitive data committed
- [ ] Time estimates are realistic
- [ ] Troubleshooting sections are comprehensive

## 🎯 Priority Files (Must Merge)

High priority - these fix critical issues:

1. `.github/workflows/deploy.yml` - Fixes broken migrations
2. `terraform/variables.tf` - Makes template more flexible
3. `vercel.json` - Fixes deployment errors
4. `02_Create_Accounts.md` - Fixes Cloudflare setup issues
5. `SETUP_HICCUPS.md` - Prevents future issues

Medium priority - improves user experience:

6. `06_Build_Your_App.md` - Helps users after setup
7. `README.md` - Better expectations
8. `QUICK_START.md` - Better troubleshooting
9. `03_Local_Setup.md` - More detailed instructions

## 📝 Notes

- Review each file individually to ensure changes are appropriate
- Some changes might be vbf-specific (like subdomain handling) - skip those
- Test the template after merging to ensure it still works
- Consider creating a PR for review before merging to main


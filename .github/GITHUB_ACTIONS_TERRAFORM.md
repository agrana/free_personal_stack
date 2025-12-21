# Running Terraform in GitHub Actions

This repository now supports running Terraform entirely through GitHub Actions, eliminating the need for local Terraform setup!

## Quick Setup

1. **Create GitHub Environments** (Settings → Environments)
   - Create `production` environment
   - Optionally create `staging` environment

2. **Add Secrets** to each environment
   - See [SECRETS_CHECKLIST.md](SECRETS_CHECKLIST.md) for complete list
   - All secrets are stored securely in GitHub Environments

3. **Run Workflow**
   - Go to Actions → Terraform Infrastructure
   - Click "Run workflow"
   - Select command: `plan`, `apply`, or `destroy`

## How It Works

### Workflow File
- `.github/workflows/terraform.yml` - Defines the Terraform workflow
- Automatically runs `plan` on pushes to `main` or PRs
- Manual `apply`/`destroy` via workflow dispatch

### Environment Variables
- All Terraform variables are passed via `TF_VAR_*` environment variables
- Values come from GitHub Secrets in the selected environment
- No `terraform.tfvars` file needed!

### Environments
- **production** - Used when running from `main` branch
- **staging** - Used for other branches (optional)

## Benefits

✅ **No local setup** - Everything runs in GitHub Actions  
✅ **Secure** - Secrets stored in GitHub Environments  
✅ **Automated** - Plan runs automatically on PRs  
✅ **Protected** - Use environment protection rules for approvals  
✅ **Visible** - All infrastructure changes visible in Actions tab  

## Documentation

- **[TERRAFORM_SETUP.md](TERRAFORM_SETUP.md)** - Complete guide with all details
- **[SECRETS_CHECKLIST.md](SECRETS_CHECKLIST.md)** - Checklist of required secrets

## Example Workflow

1. **Fork repository**
2. **Set up secrets** in GitHub Environments
3. **Run `terraform plan`** to preview changes
4. **Run `terraform apply`** to create infrastructure
5. **Push code** → Vercel auto-deploys
6. **Database migrations** run automatically via `.github/workflows/deploy.yml`

That's it! Your entire infrastructure is managed from GitHub. 🚀


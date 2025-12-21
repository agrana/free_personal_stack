# Terraform Backend Setup

## Current State Storage

By default, Terraform stores state **locally** in `terraform/terraform.tfstate`. This works fine for local development but has issues with GitHub Actions.

## Problem with Local State + GitHub Actions

When running Terraform in GitHub Actions:
- ❌ Each workflow run starts with a fresh runner (no previous state)
- ❌ State file is lost after the job completes
- ❌ Terraform can't track existing resources
- ❌ Can cause conflicts and "resource already exists" errors

## Solution: Use a Remote Backend

### Option 1: Terraform Cloud (Recommended - Easiest)

**Free tier available** - Perfect for personal projects.

#### Setup Steps:

1. **Sign up** at [app.terraform.io](https://app.terraform.io) (free)

2. **Create an organization** (or use personal account)

3. **Create a workspace**:
   - Click "New Workspace"
   - Select "API-driven workflow"
   - Name it (e.g., `free-personal-stack-production`)
   - Copy the organization name

4. **Get API token**:
   - Settings → Tokens → Create token
   - Save the token

5. **Configure backend** in `terraform/providers.tf`:
   ```hcl
   terraform {
     # ... existing required_providers ...
     
     backend "remote" {
       organization = "your-org-name"
       workspaces {
         name = "your-workspace-name"
       }
     }
   }
   ```

6. **Add GitHub Secret**:
   - Go to GitHub repo → Settings → Secrets → Actions
   - Add secret: `TF_TOKEN_app_terraform_io` = your Terraform Cloud API token

7. **Initialize backend**:
   ```bash
   cd terraform
   terraform init
   ```
   - Confirm migration when prompted

**Done!** State is now stored in Terraform Cloud and persists across GitHub Actions runs.

#### Benefits:
- ✅ Free tier
- ✅ State persistence
- ✅ State locking (prevents conflicts)
- ✅ State history
- ✅ Works seamlessly with GitHub Actions

---

### Option 2: AWS S3 Backend (Advanced)

If you prefer AWS or already have an AWS account.

#### Setup Steps:

1. **Create S3 bucket** for state:
   ```bash
   aws s3 mb s3://your-terraform-state-bucket
   aws s3api put-bucket-versioning \
     --bucket your-terraform-state-bucket \
     --versioning-configuration Status=Enabled
   ```

2. **Create DynamoDB table** for locking:
   ```bash
   aws dynamodb create-table \
     --table-name terraform-state-lock \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST
   ```

3. **Configure backend** in `terraform/providers.tf`:
   ```hcl
   terraform {
     # ... existing required_providers ...
     
     backend "s3" {
       bucket         = "your-terraform-state-bucket"
       key            = "terraform.tfstate"
       region         = "us-east-1"
       dynamodb_table = "terraform-state-lock"
       encrypt        = true
     }
   }
   ```

4. **Add GitHub Secrets**:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION`

5. **Configure AWS credentials in workflow**:
   Add to `.github/workflows/terraform.yml`:
   ```yaml
   - name: Configure AWS credentials
     uses: aws-actions/configure-aws-credentials@v2
     with:
       aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
       aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
       aws-region: ${{ secrets.AWS_REGION }}
   ```

6. **Initialize backend**:
   ```bash
   cd terraform
   terraform init
   ```

---

### Option 3: GitHub Actions Artifacts (Not Recommended)

You can store state as a GitHub Actions artifact, but this is **not recommended** because:
- Artifacts expire after 90 days
- No locking mechanism
- Complex workflow setup

**Only use if:** You're just experimenting and don't need persistence.

---

## Migration from Local to Remote Backend

If you already have local state:

1. **Backup current state**:
   ```bash
   cp terraform/terraform.tfstate terraform/terraform.tfstate.local.backup
   ```

2. **Configure remote backend** in `providers.tf`

3. **Initialize and migrate**:
   ```bash
   cd terraform
   terraform init
   ```
   - Terraform will ask: "Do you want to copy existing state to the new backend?"
   - Type `yes`

4. **Verify**:
   ```bash
   terraform state list
   ```
   - Should show all your existing resources

5. **Test plan**:
   ```bash
   terraform plan
   ```
   - Should show "No changes" (since state was migrated)

**Important:** After migration, **don't commit** `terraform.tfstate` to git (add to `.gitignore` if not already).

---

## Recommended Setup for This Project

**For personal projects:** Use **Terraform Cloud** (Option 1)
- Free tier is perfect
- Easiest setup
- Works great with GitHub Actions

**For team/enterprise:** Use **AWS S3** (Option 2)
- More control
- Can integrate with existing AWS infrastructure
- Requires AWS account setup

---

## Troubleshooting

### "Backend initialization required"
- Run `terraform init` after configuring backend

### "Error acquiring state lock"
- Another Terraform run is in progress
- Wait for it to complete
- If stuck, manually unlock: `terraform force-unlock <lock-id>`

### "State file not found" after migration
- Check backend configuration is correct
- Verify credentials/permissions
- State is now in remote backend, not local file


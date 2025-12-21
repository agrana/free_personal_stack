# Storing Terraform State in GitHub Actions Artifacts

This is a **zero-setup** option that stores Terraform state as GitHub Actions artifacts - no external services required!

## How It Works

The workflow automatically:
1. **Downloads** state artifact before each run (if it exists)
2. **Runs** Terraform with the downloaded state
3. **Uploads** updated state back as artifact after successful runs

## Benefits

✅ **Zero configuration** - Works out of the box, no setup needed  
✅ **No external services** - Everything stored in GitHub  
✅ **Access controlled** - Only accessible to repository collaborators  
✅ **400 day retention** - State persists for over a year  
✅ **Private** - Artifacts are private by default

## Security Notes

⚠️ **Important:** While artifacts are stored securely by GitHub and encrypted at rest:
- Artifacts are accessible in **plaintext** to anyone with repository access
- Anyone who can download the artifact can read the state file contents
- Terraform state files contain sensitive data (resource IDs, configuration values)

**Default Setup (Unencrypted):**
- Artifacts are only accessible to people with repository access
- Download requires GitHub authentication
- Private repos = private artifacts
- Public repos = public artifacts (don't use artifacts for public repos!)

**Encrypted Setup (Recommended):**
- ✅ State files are encrypted with AES-256-CBC before upload
- ✅ Requires password to decrypt (stored in GitHub Secrets)
- ✅ Even if artifact is downloaded, contents are encrypted
- ✅ Much more secure than plaintext artifacts

**Setup Encryption:**
1. Add secret `TERRAFORM_STATE_PASSWORD` to your GitHub Environment
2. Use a strong, random password (generate with: `openssl rand -base64 32`)
3. The workflow automatically encrypts/decrypts state files

**For Maximum Security:**
- Use encrypted artifacts (already configured in workflow)
- Or use a remote backend with proper encryption (Terraform Cloud, AWS S3 with encryption)  

## Limitations

⚠️ **No state locking** - Concurrent runs could conflict  
⚠️ **Artifact expiration** - After 400 days, artifacts are deleted (configurable)  
⚠️ **No state history** - Only latest state is kept (not versioned)  

## How to Use

**No setup required!** The workflow already handles this.

Just:
1. Run the Terraform workflow
2. On first run, no state exists (normal)
3. After successful `apply`, state is saved
4. Next run automatically downloads previous state

## State Artifact Details

- **Name:** `terraform-state`
- **Contains:**
  - `terraform.tfstate` - Current state
  - `terraform.tfstate.backup` - Backup state (if exists)
- **Retention:** 400 days (configurable)
- **Location:** GitHub Actions → Artifacts

## Viewing State

1. Go to repository → **Actions**
2. Click on any completed workflow run
3. Scroll to **Artifacts** section
4. Download `terraform-state` to view

## Important Notes

### State Locking

This approach **doesn't provide state locking**. This means:
- ⚠️ Don't run multiple workflows simultaneously
- ⚠️ Wait for one workflow to finish before starting another
- ✅ The workflow is designed to prevent PR runs from modifying state

### First Run

On the first run:
- No state artifact exists (this is normal)
- Terraform will create new resources
- State is uploaded after successful completion

### State Recovery

If you lose the artifact:
- Artifacts expire after 400 days
- You can manually download and save important state files
- For production, consider using a remote backend (see [BACKEND_SETUP.md](BACKEND_SETUP.md))

## Encryption Setup (Default - Already Configured!)

The workflow automatically encrypts state files before upload and decrypts before use.

### Current Implementation

Uses `openssl` with AES-256-CBC encryption and PBKDF2 key derivation:
- **Encryption:** AES-256-CBC
- **Key Derivation:** PBKDF2 with 100,000 iterations
- **Password:** Stored in GitHub Secret `TERRAFORM_STATE_PASSWORD`

### Setup Steps

1. **Generate a strong password:**
   ```bash
   openssl rand -base64 32
   ```

2. **Add to GitHub Secrets:**
   - Go to repository → Settings → Environments → `production`
   - Add secret: `TERRAFORM_STATE_PASSWORD` = your generated password
   - Do the same for `staging` environment if you use it

3. **Done!** The workflow automatically handles encryption/decryption

### Alternative: SOPS (More Advanced)

If you prefer SOPS for encryption:

```yaml
- name: Encrypt State
  run: |
    sops -e terraform/terraform.tfstate > terraform/terraform.tfstate.encrypted
  env:
    SOPS_AGE_KEY: ${{ secrets.SOPS_AGE_KEY }}

- name: Upload Encrypted State
  uses: actions/upload-artifact@v4
  with:
    name: terraform-state
    path: terraform/terraform.tfstate.encrypted

- name: Decrypt State
  run: |
    sops -d terraform/terraform.tfstate.encrypted > terraform/terraform.tfstate
  env:
    SOPS_AGE_KEY: ${{ secrets.SOPS_AGE_KEY }}
```

### Option 2: Encrypted State in Git (Advanced)

If you want state in the repository itself:

1. Install SOPS and set up encryption key
2. Encrypt state file before committing
3. Decrypt in workflow before use

**Example:**
```yaml
- name: Decrypt State
  run: sops -d terraform/terraform.tfstate.encrypted > terraform/terraform.tfstate
  env:
    SOPS_AGE_KEY: ${{ secrets.SOPS_AGE_KEY }}

# ... terraform commands ...

- name: Encrypt State
  run: sops -e terraform/terraform.tfstate > terraform/terraform.tfstate.encrypted
```

**Pros:**
- State in repo (version controlled)
- Encrypted at rest

**Cons:**
- Requires SOPS setup
- State file size can grow large
- Still no locking mechanism

### Option 3: Git LFS (Large File Storage)

Store state in Git LFS (for large state files):

```bash
git lfs track "terraform/*.tfstate*"
git add .gitattributes
git commit -m "Track state files with Git LFS"
```

**Pros:**
- State in repo
- Handles large files

**Cons:**
- Git LFS has storage limits on free plans
- No encryption (unless combined with SOPS)
- No locking

## Recommendation

**For most users:** Use the artifact approach (already configured) - it's simple and works well.

**For production/critical:** Use Terraform Cloud backend (see [BACKEND_SETUP.md](BACKEND_SETUP.md)) for proper locking and state management.


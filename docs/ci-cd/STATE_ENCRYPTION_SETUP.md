# Terraform State Encryption Setup

The workflow encrypts Terraform state files before uploading to GitHub Actions artifacts, ensuring your state is secure even if artifacts are accessed.

## How It Works

1. **Before Upload:** State files are encrypted using AES-256-CBC with PBKDF2
2. **During Download:** Encrypted files are decrypted automatically
3. **Password:** Stored securely in GitHub Secrets

## Setup (One-Time)

### Step 1: Generate a Strong Password

Generate a secure, random password:

```bash
openssl rand -base64 32
```

Example output:
```
Kx8mP2vQ9rT4nL6jH8fD1sA3bC5eG7hI9jK0lM2nP4qR6sT8uV0wX2yZ4aB6cD
```

**Save this password securely!** You'll need it if you want to manually decrypt state files.

### Step 2: Add to GitHub Secrets

1. Go to your repository → **Settings** → **Environments**
2. Select the environment (e.g., `production`)
3. Click **"Add secret"**
4. Name: `TERRAFORM_STATE_PASSWORD`
5. Value: Paste the password you generated
6. Click **"Add secret"**
7. Repeat for `staging` environment if you use it

### Step 3: Verify Setup

1. Run a Terraform workflow (e.g., `plan`)
2. After completion, check the workflow logs:
   - Should see "State file encrypted successfully" (on upload)
   - Should see "State file decrypted successfully" (on download)

## How to Decrypt Manually

If you download an encrypted state file and need to decrypt it locally:

```bash
# Decrypt state file
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
  -in terraform.tfstate.encrypted \
  -out terraform.tfstate \
  -pass pass:"YOUR_PASSWORD_HERE"
```

Or use an environment variable:

```bash
export TERRAFORM_STATE_PASSWORD="your-password-here"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
  -in terraform.tfstate.encrypted \
  -out terraform.tfstate \
  -pass env:TERRAFORM_STATE_PASSWORD
```

## Encryption Details

- **Algorithm:** AES-256-CBC
- **Key Derivation:** PBKDF2 with 100,000 iterations
- **Salt:** Automatically generated (unique per file)
- **Security:** Strong encryption, suitable for sensitive data

## Troubleshooting

### "Bad decrypt" error

- Password mismatch - verify `TERRAFORM_STATE_PASSWORD` secret is correct
- File corrupted - try downloading artifact again
- Wrong encryption method - ensure you're using AES-256-CBC with PBKDF2

### State file not decrypting

- Check that `TERRAFORM_STATE_PASSWORD` secret exists in the environment
- Verify the secret value is correct (no extra spaces, correct encoding)
- Check workflow logs for decryption errors

### First run fails

- First run won't have an encrypted state file (normal)
- Terraform will create new state
- Subsequent runs will decrypt existing state

## Security Best Practices

1. **Use a strong password** - At least 32 characters, randomly generated
2. **Store password securely** - Never commit password to repository
3. **Rotate password periodically** - Generate new password and re-encrypt state
4. **Limit repository access** - Only give access to trusted collaborators
5. **Audit downloads** - Monitor who downloads artifacts

## Password Rotation

If you need to change the encryption password:

1. **Download current encrypted state** from artifacts
2. **Decrypt with old password**
3. **Update `TERRAFORM_STATE_PASSWORD` secret** with new password
4. **Re-encrypt with new password** (will happen automatically on next workflow run)

Or manually:

```bash
# Decrypt with old password
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
  -in terraform.tfstate.encrypted \
  -out terraform.tfstate \
  -pass pass:"OLD_PASSWORD"

# Encrypt with new password
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \
  -in terraform.tfstate \
  -out terraform.tfstate.encrypted \
  -pass pass:"NEW_PASSWORD"
```

Then update the secret and upload the new encrypted file.

---

**Your Terraform state is now encrypted and secure! 🔒**


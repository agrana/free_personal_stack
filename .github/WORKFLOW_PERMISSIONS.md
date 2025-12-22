# Workflow Permissions Setup

If you're getting "Resource not accessible by integration" errors, you need to enable workflow permissions in your repository settings.

## Fix Permission Errors

### Step 1: Check Repository Settings

1. Go to your repository → **Settings** → **Actions** → **General**
2. Scroll to **"Workflow permissions"**
3. Select: **"Read and write permissions"**
4. Check: **"Allow GitHub Actions to create and approve pull requests"** (optional, but recommended)
5. Click **"Save"**

### Step 2: Verify Workflow Permissions

The workflows in this repository require:
- `contents: write` - To commit setup files
- `issues: write` - To create setup issues

These are already configured in the workflow files, but the repository must allow workflows to use these permissions.

## Why This Happens

GitHub repositories can restrict workflow permissions for security. By default, some repositories only allow read-only access, which prevents workflows from:
- Creating issues
- Committing files
- Creating pull requests

## After Enabling Permissions

Once you enable "Read and write permissions":
1. Re-run the failed workflow
2. The workflow should now be able to create issues and commit files

## Alternative: Use Personal Access Token

If you can't change repository settings (e.g., organization policy), you can:
1. Create a Personal Access Token with `repo` and `write:issues` scopes
2. Add it as a secret: `PAT_TOKEN`
3. Update workflows to use `${{ secrets.PAT_TOKEN }}` instead of `${{ secrets.GITHUB_TOKEN }}`

However, this is less secure and not recommended unless necessary.


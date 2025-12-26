# Part 3: Configure for Deployment

**Time: 10-15 minutes** (may take longer if troubleshooting)

Configure everything directly in GitHub. No local setup needed.

## Step 1: Set GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name | Value | Where to Get It |
|------------|-------|-----------------|
| `SUPABASE_ACCESS_TOKEN` | Your **personal access token** (NOT the service role key!) | [Supabase Dashboard → Account → Access Tokens](https://supabase.com/dashboard/account/tokens) - Click "Generate new token" if needed |
| `SUPABASE_PROJECT_ID` | Your Supabase project ID | Supabase Dashboard → **Project Settings** → **General** → Reference ID |
| `SUPABASE_DATABASE_PASSWORD` | Your Supabase database password | The password you set when creating your Supabase project. If you forgot it, go to Project Settings → Database → Reset Database Password |

**Important:** 
- `SUPABASE_ACCESS_TOKEN` is different from your project API keys:
  - **Access Token** (for GitHub Actions): From Account settings → Access Tokens (personal token for CLI/API)
  - **Service Role Key** (for your app): From Project Settings → API Keys → service_role key
- `SUPABASE_DATABASE_PASSWORD` is the database password you set when creating the project (not the same as API keys)

**Note:** These secrets are used for database migrations. Terraform configuration happens in Part 4.

## Step 2: Prepare Your Configuration Values

You'll need these values in Part 4. Have them ready:

**From Part 2, you should have:**
- ✅ Vercel API token
- ✅ Supabase URL
- ✅ Supabase anon key
- ✅ Supabase service role key
- ✅ Supabase access token (for GitHub Secrets)
- ✅ Supabase project ID (for GitHub Secrets)
- ✅ Supabase database password (for GitHub Secrets)
- ✅ Cloudflare API token (if using custom domain)
- ✅ Your GitHub repo name (e.g., `YOUR_USERNAME/free_personal_stack`)

**Next:** [Part 4: Deploy from GitHub](./04_Deploy.md)


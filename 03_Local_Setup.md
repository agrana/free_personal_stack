# Part 3: Configure for Deployment

**Time: 5 minutes**

Configure everything directly in GitHub. No local setup needed.

## Step 1: Set GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name | Value | Where to Get It |
|------------|-------|-----------------|
| `SUPABASE_ACCESS_TOKEN` | Your Supabase access token | [Supabase Dashboard → Account → Access Tokens](https://supabase.com/dashboard/account/tokens) |
| `SUPABASE_PROJECT_ID` | Your Supabase project ID | Supabase Dashboard → Settings → General → Reference ID |

**Note:** These secrets are used for database migrations. Terraform configuration happens in Part 4.

## Step 2: Prepare Your Configuration Values

You'll need these values in Part 4. Have them ready:

**From Part 2, you should have:**
- ✅ Vercel API token
- ✅ Supabase URL
- ✅ Supabase anon key
- ✅ Supabase service role key
- ✅ Supabase access token (for GitHub Secrets above)
- ✅ Supabase project ID
- ✅ Cloudflare API token (if using custom domain)
- ✅ Your GitHub repo name (e.g., `YOUR_USERNAME/free_personal_stack`)

**Next:** [Part 4: Deploy from GitHub](./04_Deploy.md)


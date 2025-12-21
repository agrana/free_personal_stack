# Part 2: Create Your Accounts

**Time: 10 minutes**

You need accounts for three services. All have free tiers.

## Supabase (Database & Auth)

1. Go to [supabase.com](https://supabase.com)
2. Sign up (GitHub login works)
3. Click "New Project"
4. Fill in:
   - **Name**: Your project name
   - **Database Password**: **Save this securely** - you'll need it for GitHub Actions migrations!
   - **Region**: Closest to you
   - **Plan**: Free
5. Wait 1-2 minutes for provisioning

**Get credentials** (you'll need them in Part 3):

- **Project URL**: `https://xxxxx.supabase.co` (Project Settings → API → Project URL)
- **Anon Key**: `eyJ...` (Project Settings → API → anon/public key)
- **Service Role Key**: `eyJ...` (Project Settings → API → service_role key - click "Reveal")
- **Project ID**: Reference ID (Project Settings → General → Reference ID)
- **Access Token**: Personal token for CLI/GitHub Actions (Account Settings → Access Tokens → Generate new token) - **This is different from the service_role key!**
- **Database Password**: The password you set when creating the project (save it securely!). If you forgot it, go to Project Settings → Database → Reset Database Password

## Vercel (Hosting)

1. Go to [vercel.com](https://vercel.com)
2. Sign up with GitHub
3. Go to [Account Settings → Tokens](https://vercel.com/account/tokens)
4. Create new token
5. Copy the token (you'll need this for Terraform in Part 4)

## Cloudflare (DNS - Optional)

Only needed if you have a custom domain.

1. Go to [cloudflare.com](https://cloudflare.com)
2. Sign up
3. Add your domain (or skip if you don't have one)
4. Go to [API Tokens](https://dash.cloudflare.com/profile/api-tokens)
5. Click "Create Token"
6. Use "Custom token" or "Create custom token"
7. Configure **Token Permissions** (under "Permissions" section):
   - **Zone** → **Zone:Zone:Read** (REQUIRED - needed to look up zones)
   - **Zone** → **Zone:DNS:Edit** (REQUIRED - needed to create DNS records)
   - **Zone** → **Zone:Email Routing:Read** (optional - if using email routing)
   - **Zone** → **Zone:Email Routing:Edit** (optional - if using email routing)
   
   **Important:** 
   - `Zone:Zone:Read` is separate from DNS permissions. You need **both** `Zone:Zone:Read` (to query zones) and `Zone:DNS:Edit` (to modify DNS records).
   - Do NOT confuse `Zone:DNS:Edit` with `DNS Settings:Edit` - they are different permissions.
   
8. Configure **Zone Resources** (under "Resources" section):
   - Select **"Include"** → **"All zones"** (recommended)
   - OR select **"Include"** → **"Specific zone"** → Select your zone (e.g., `alfonsograna.com`)
   
   **Critical:** The token MUST be scoped to your zone. If you get "Authentication error (10000)" when creating DNS records, check that your zone is included in Zone Resources.
   
9. Click "Continue to summary" and then "Create Token"
10. Copy the token immediately (you won't be able to see it again)

**Next:** [Part 3: Configure for Deployment](./03_Local_Setup.md)


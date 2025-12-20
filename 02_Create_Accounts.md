# Part 2: Create Your Accounts

**Time: 10 minutes**

You need accounts for three services. All have free tiers.

## Supabase (Database & Auth)

1. Go to [supabase.com](https://supabase.com)
2. Sign up (GitHub login works)
3. Click "New Project"
4. Fill in:
   - **Name**: Your project name
   - **Database Password**: Save this securely
   - **Region**: Closest to you
   - **Plan**: Free
5. Wait 1-2 minutes for provisioning

**Save these credentials** (you'll need them in Part 3):
- Project URL: `https://xxxxx.supabase.co` (Settings → API → Project URL)
- Anon Key: `eyJ...` (Settings → API → anon/public key)
- Service Role Key: `eyJ...` (Settings → API → service_role key - click "Reveal")
- Project ID: Reference ID (Settings → General)

## Vercel (Hosting)

1. Go to [vercel.com](https://vercel.com)
2. Sign up with GitHub
3. Go to [Account Settings → Tokens](https://vercel.com/account/tokens)
4. Create new token
5. Copy the token

## Cloudflare (DNS - Optional)

Only needed if you have a custom domain.

1. Go to [cloudflare.com](https://cloudflare.com)
2. Sign up
3. Add your domain (or skip if you don't have one)
4. Go to [API Tokens](https://dash.cloudflare.com/profile/api-tokens)
5. Create token with permissions:
   - Zone:Zone:Read
   - Zone:DNS:Edit
   - Zone:Email Routing:Read (if using email)
   - Zone:Email Routing:Edit (if using email)
6. Copy the token

**Next:** [Part 3: Configure for Deployment](./03_Local_Setup.md)


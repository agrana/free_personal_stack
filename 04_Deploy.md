# Part 4: Deploy from GitHub

**Time: 20-30 minutes** (may take longer if troubleshooting permissions or connection issues)

Deploy everything directly from GitHub. No local setup required.

> **Note:** You'll use Terraform to configure everything, but you don't need to understand it. Just copy-paste the commands and fill in your values. Terraform handles all the infrastructure setup automatically.

## Option A: Deploy with GitHub Codespaces (Recommended)

GitHub Codespaces gives you a cloud-based terminal. No local installs needed.

### Step 1: Open Codespace

1. Go to your GitHub repo
2. Click the green **"Code"** button
3. Click **"Codespaces"** tab
4. Click **"Create codespace on main"**

Wait 1-2 minutes for the environment to start.

### Step 2: Configure Terraform

In the Codespace terminal, run:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

### Step 3: Edit terraform.tfvars

Open `terraform/terraform.tfvars` in the Codespace editor and fill in your values:

```hcl
# Domain (optional - skip if you don't have one)
domain_name = "yourdomain.com"
app_url     = "yourproject.vercel.app"

# Cloudflare (optional - skip if no domain)
cloudflare_api_token = "your-cloudflare-token"

# Vercel
vercel_api_token = "your-vercel-token"
vercel_project_name = "your-project-name"
github_repo = "YOUR_USERNAME/free_personal_stack"

# Supabase
supabase_url = "https://xxxxx.supabase.co"
supabase_anon_key = "eyJ..."
supabase_service_role_key = "eyJ..."
supabase_access_token = "your-supabase-access-token"
supabase_project_id = "your-project-id"

# Site URL
site_url = "https://yourdomain.com"  # or https://yourproject.vercel.app
```

**Get your values from Part 2:**
- `vercel_api_token`: Vercel Account → Tokens
- `supabase_url`: Supabase Dashboard → Settings → API → Project URL
- `supabase_anon_key`: Supabase Dashboard → Settings → API → anon/public key
- `supabase_service_role_key`: Supabase Dashboard → Settings → API → service_role key (click "Reveal")
- `supabase_access_token`: [Supabase Dashboard → Account → Access Tokens](https://supabase.com/dashboard/account/tokens)
- `supabase_project_id`: Supabase Dashboard → Settings → General → Reference ID
- `github_repo`: Your GitHub username/repo name (e.g., `agrana/free_personal_stack`)

### Step 4: Initialize Terraform

```bash
terraform init
```

### Step 5: Review Changes

```bash
terraform plan
```

Review what will be created:
- Vercel project creation
- Environment variables configuration
- DNS records (if domain provided)
- Email routing (if domain provided)

### Step 6: Apply Infrastructure

```bash
terraform apply
```

Type `yes` when prompted. This will:
- Create Vercel project
- Link it to your GitHub repo
- Set all environment variables in Vercel
- Configure DNS (if domain provided)

## Option B: Deploy with GitHub Actions

If you prefer automation, you can set up a GitHub Action to run Terraform.

**Note:** This requires additional setup. For fastest path, use Option A above.

## Step 7: Trigger First Deployment

After Terraform completes, your Vercel project is linked to GitHub. Any push will auto-deploy.

To trigger the first deployment:

1. In your Codespace (or locally if you have git), make a small change:
   ```bash
   echo "# Deployed" >> README.md
   git add README.md
   git commit -m "Initial deployment"
   git push origin main
   ```

Or simply wait - GitHub Actions will run migrations automatically on the next push.

## Step 8: Verify Deployment

1. **Check GitHub Actions**
   - Go to your repo → **Actions** tab
   - You should see "Deploy to Supabase" workflow running
   - Wait for it to complete (runs database migrations)

2. **Check Vercel**
   - Go to [Vercel Dashboard](https://vercel.com/dashboard)
   - Your project should appear
   - First deployment may take 2-3 minutes

3. **Visit Your App**
   - Go to your Vercel project
   - Click on the deployment
   - Copy the URL (e.g., `https://yourproject.vercel.app`)
   - Visit it in your browser

**Your app is now live and accessible from anywhere.**

## What Happens Automatically

Every time you push to `main`:
1. **GitHub Actions** runs database migrations
2. **Vercel** automatically builds and deploys your app
3. Environment variables are already configured (from Terraform)

**Next:** [Part 5: What You Got](./05_What_You_Got.md)


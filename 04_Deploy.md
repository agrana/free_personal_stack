# Part 4: Deploy from GitHub

**Time: 20-30 minutes** (may take longer if troubleshooting permissions or connection issues)

Deploy everything directly from GitHub. No local setup required.

> **Note:** You'll use Terraform to configure everything, but you don't need to understand it. Just copy-paste the commands and fill in your values. Terraform handles all the infrastructure setup automatically.

## 🚀 Option A: Deploy with GitHub Actions (Recommended - Fully Automated)

**No local setup required!** Run Terraform entirely through GitHub Actions.

1. **Set up GitHub Environments and Secrets**
   - See [docs/ci-cd/TERRAFORM_SETUP.md](docs/ci-cd/TERRAFORM_SETUP.md) for complete instructions
   - Add all required secrets to GitHub Environments
   - See [docs/ci-cd/SECRETS_CHECKLIST.md](docs/ci-cd/SECRETS_CHECKLIST.md) for the full list

2. **Run Terraform Workflow**
   - Go to **Actions** → **Terraform Infrastructure**
   - Click **"Run workflow"**
   - Select command: `plan` (to preview) or `apply` (to deploy)
   - Infrastructure is created automatically!

**Benefits:**
- ✅ No local Terraform installation needed
- ✅ All secrets stored securely in GitHub
- ✅ Automatic plans on PRs
- ✅ Protection rules for production deployments

See [docs/ci-cd/TERRAFORM_SETUP.md](docs/ci-cd/TERRAFORM_SETUP.md) for detailed setup instructions.

## Step 2: Trigger First Deployment

After Terraform completes, your Vercel project is linked to GitHub. Any push will auto-deploy.

To trigger the first deployment, make a small change directly in GitHub:

1. Go to your repository on GitHub
2. Click on any file (e.g., `README.md`)
3. Click the pencil icon to edit
4. Make a small change (add a space or comment)
5. Click "Commit changes" at the bottom

GitHub Actions will run migrations automatically and Vercel will deploy your app.

## Step 3: Verify Deployment

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


# Quick Start Guide

This guide will get you up and running with a new project. **First-time setup typically takes 30-60 minutes** depending on your familiarity with the tools and any troubleshooting needed. Subsequent projects will be much faster!

## 🚀 3-Step Setup

### 1. Create Repository

```bash
# Option A: Use GitHub template
gh repo create your-project-name --template your-username/free_personal_stack

# Option B: Clone and customize
git clone https://github.com/your-username/free_personal_stack.git your-project-name
cd your-project-name
```

### 2. Run Setup Script

```bash
./scripts/setup.sh
```

The script will ask for:

- Project name and domain
- API tokens (Cloudflare, Vercel, Supabase)
- Email addresses
- Optional services (Google OAuth, NextAuth)

### 3. Deploy Infrastructure

```bash
make apply
# or
./scripts/quick-start.sh apply
```

### 4. Set up the Next.js App

```bash
# Install dependencies (resolves TypeScript/linting errors)
npm install

# Set up environment variables
cp env.example .env.local
# Edit .env.local with your Supabase credentials

# Run database migrations
npx supabase db push

# Start development server
npm run dev
```

**Note**: TypeScript and linting errors are expected until dependencies are installed.

## 📋 Prerequisites Checklist

Before starting, ensure you have:

- [ ] **Terraform** installed (>= 1.0)
- [ ] **Node.js** installed (>= 18.0)
- [ ] **Supabase CLI** installed
- [ ] **Cloudflare account** with a domain
- [ ] **Vercel account** for hosting
- [ ] **Supabase account** for database
- [ ] **API tokens** ready (see below)

## 🔑 Required API Tokens

### Cloudflare API Token

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Click "Create Token" → "Create custom token"
3. Set **Permissions** (Zone section):
   - `Zone:Zone:Read` (REQUIRED - needed to look up zones)
   - `Zone:DNS:Edit` (REQUIRED - needed to create DNS records)
   - `Zone:Email Routing:Read` (optional - if using email routing)
   - `Zone:Email Routing:Edit` (optional - if using email routing)
   
   **Important:** 
   - `Zone:Zone:Read` and `Zone:DNS:Edit` are SEPARATE permissions - you need **both**
   - `Zone:DNS:Edit` is different from `DNS Settings:Edit` - select the correct one
   
4. Set **Zone Resources**:
   - Include → All zones (recommended)
   - OR Include → Specific zone → Select your zone
   
   **Critical:** Token must be scoped to your zone. "Authentication error (10000)" usually means missing Zone Resources.

### Vercel API Token

1. Go to [Vercel Account Tokens](https://vercel.com/account/tokens)
2. Create new token

### Supabase Access Token (Optional)

1. Go to [Supabase Dashboard](https://supabase.com/dashboard/account/tokens)
2. Create new access token

## 🛠️ Common Commands

```bash
# Development
npm run dev         # Start development server
npm run build       # Build for production
npm run start       # Start production server
npm run lint        # Run linting

# Database
npm run db:reset    # Reset database
npm run db:migrate  # Run migrations
npm run db:seed     # Seed database

# Infrastructure
make setup          # Interactive setup
make init           # Initialize Terraform
make plan           # Preview changes
make apply          # Deploy infrastructure
make destroy        # Remove infrastructure
make status         # Show current state

# Quick shortcuts
make i              # init
make p              # plan
make a              # apply
make d              # destroy
make s              # status
```

## 🏗️ What Gets Created

After running `make apply`, you'll have:

- **DNS Records**: Root domain, www, and api subdomains
- **Email Routing**: support@, contact@, hello@ forwarding
- **Vercel Integration**: Custom domain and GitHub connection
- **Supabase Setup**: Database and auth configured

After running `npm run dev`, you'll have:

- **Next.js App**: Running on http://localhost:3000
- **Authentication**: Sign up/sign in functionality
- **Todo List**: Working CRUD operations
- **Database**: Todos table with Row Level Security

## 🚨 Troubleshooting

### Common Setup Issues

#### Cloudflare API Token Issues

**Error: "no zone found" or "Authentication error (10000)"**
- **Cause**: Missing `Zone:Zone:Read` permission or token not scoped to your zone
- **Fix**: 
  1. Go to Cloudflare Dashboard → API Tokens
  2. Edit your token and ensure it has **both**:
     - `Zone:Zone:Read` (REQUIRED - separate from DNS permissions)
     - `Zone:DNS:Edit` (REQUIRED - for creating DNS records)
  3. Under "Zone Resources", ensure your zone is included (or select "All zones")
  4. `Zone:DNS:Edit` is different from `DNS Settings:Edit` - select the correct one

**Error: "failed to create DNS record: Authentication error (10000)"**
- **Cause**: Token missing `Zone:DNS:Edit` permission or not scoped to zone
- **Fix**: Verify token has `Zone:DNS:Edit` and is scoped to your zone in Zone Resources

#### Email Routing Rules Issues

**Error: "required rule id missing" or "Duplicated Zone rule"**
- **Cause**: Cloudflare provider has issues updating existing email routing rules
- **Fix**: 
  1. Go to Cloudflare Dashboard → Email Routing → Routing Rules
  2. Delete the existing rules manually
  3. Remove from Terraform state: `terraform state rm 'module.email_routing[0].cloudflare_email_routing_rule.NAME'`
  4. Run `terraform apply` to recreate them

#### Database Migration Issues

**Error: "failed to connect to postgres: failed SASL auth"**
- **Cause**: Wrong password or token type
- **Fix**: 
  - `SUPABASE_ACCESS_TOKEN` must be a **personal access token** (from Account → Access Tokens), NOT the service role key
  - `SUPABASE_DATABASE_PASSWORD` is the database password you set when creating the project

**Error: "Circuit breaker open" or connection timeouts**
- **Cause**: Supabase infrastructure issue or database paused
- **Fix**: 
  1. Check Supabase Dashboard for project status
  2. Try restarting the project
  3. Wait a few minutes and retry
  4. Check if your IP is blocked in Supabase network restrictions

#### Terraform Issues

- **"Zone not found"**: Domain not added to Cloudflare or wrong parent zone configured
- **"Project not found"**: Vercel project doesn't exist (Terraform will create it automatically now)
- **"Permission denied"**: API token lacks required permissions (see Cloudflare section above)

#### General Setup Issues

- Ensure all required fields are filled in `terraform.tfvars`
- Verify API tokens haven't expired
- Check that domain is added to Cloudflare before running Terraform
- For subdomains, ensure the parent zone exists in Cloudflare

### Expected First-Time Setup Time

- **Terraform setup**: 10-20 minutes (longer if troubleshooting permissions)
- **Database migrations**: 5-10 minutes (may need to troubleshoot connection issues)
- **Testing**: 5-10 minutes
- **Total**: 30-60 minutes for first setup

## 📚 Next Steps

After infrastructure is deployed and app is running:

1. **Test the todo list** - Create, update, and delete todos
2. **Test authentication** - Sign up and sign in
3. **Deploy to Vercel** - Connect your GitHub repo
4. **Set up environment variables** in Vercel dashboard
5. **Test email forwarding** with your domain
6. **Customize the app** for your project needs

## 🆘 Need Help?

- Check the [full README](README.md) for detailed documentation
- Review [troubleshooting section](README.md#troubleshooting)
- Open an [issue](https://github.com/your-username/free_personal_stack/issues) for bugs
- Start a [discussion](https://github.com/your-username/free_personal_stack/discussions) for questions

---

**Happy coding! 🚀**

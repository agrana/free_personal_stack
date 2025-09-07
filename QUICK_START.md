# Quick Start Guide

This guide will get you up and running with a new project in under 10 minutes.

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
2. Create custom token with permissions:
   - `Zone:Zone:Read`
   - `Zone:DNS:Edit`
   - `Zone:Email Routing:Read`
   - `Zone:Email Routing:Edit`

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

### Setup Script Issues

- Ensure all required fields are filled
- Verify API tokens have correct permissions
- Check that domain is in Cloudflare

### Terraform Issues

- Run `terraform validate` to check configuration
- Check API token permissions
- Verify domain ownership

### Common Errors

- **"Zone not found"**: Domain not added to Cloudflare
- **"Project not found"**: Vercel project doesn't exist
- **"Permission denied"**: API token lacks required permissions

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

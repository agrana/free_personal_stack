# Personal Project Template

A comprehensive template for quickly starting new personal projects with pre-configured infrastructure using Terraform. This template includes everything you need to get a modern web application up and running with DNS, hosting, database, and authentication services.

## 🚀 Quick Start

**⏱️ First-time setup typically takes 30-60 minutes** depending on your familiarity with the tools and any troubleshooting needed. The guides are broken into parts to make it easier to follow.

### 1. Use this template

```bash
# Create a new repository from this template
gh repo create your-project-name --template agrana/free_personal_stack

# Or clone and customize
git clone https://github.com/agrana/free_personal_stack.git your-project-name
cd your-project-name
```

### 2. Run the setup script

```bash
./scripts/setup.sh
```

This interactive script will guide you through configuring:

- Project details (name, domain, GitHub repo)
- API tokens (Cloudflare, Vercel, Supabase)
- Optional services (Google OAuth, NextAuth)

### 3. Deploy your infrastructure

```bash
./scripts/quick-start.sh apply
```

**Common issues:**
- If you get Cloudflare API errors, check the [troubleshooting section](#-troubleshooting) for token permissions
- Email routing rules may need to be deleted and recreated (see troubleshooting)

### 4. Set up the Next.js app

```bash
# Install dependencies (this will resolve TypeScript/linting errors)
npm install

# Set up environment variables
cp env.example .env.local
# Edit .env.local with your Supabase credentials

# Run database migrations
npx supabase link --project-ref YOUR_PROJECT_ID --password YOUR_DB_PASSWORD
npx supabase db push

# Start development server
npm run dev
```

**Note**: TypeScript and linting errors will appear until you run `npm install` to install the dependencies. This is normal for template repositories.

**Migration troubleshooting:** If migrations fail, see the [troubleshooting section](#-troubleshooting) for connection issues.

### 5. Set up Pre-commit Hooks (not so Optional)

```bash
# Install pre-commit for comprehensive hooks
pip install pre-commit
pre-commit install

# Or use the included npm scripts
npm run pre-commit        # Fast checks for staged files
npm run pre-commit-full   # Comprehensive checks
```

See [PRE_COMMIT_SETUP.md](PRE_COMMIT_SETUP.md) for detailed pre-commit configuration.

## 📋 Prerequisites

Before using this template, make sure you have:

- **Terraform** (>= 1.9) - [Install from terraform.io](https://terraform.io)
- **Node.js** (>= 20.0) - [Install from nodejs.org](https://nodejs.org)
- **Git** - For version control
- **Cloudflare Account** - For DNS and email routing
- **Vercel Account** - For hosting
- **Supabase Account** - For database and auth
- **Supabase CLI** - [Install from supabase.com](https://supabase.com/docs/guides/cli)

## 🏗️ What's Included

### Infrastructure Components

- **DNS Management** (Cloudflare)
  - Root domain and www subdomain
  - API subdomain
  - Email routing for support and contact

- **Hosting** (Vercel)
  - Custom domain configuration
  - Environment variable management
  - GitHub integration

- **Database & Auth** (Supabase - Optional)
  - PostgreSQL database
  - Authentication system
  - Real-time subscriptions
  - Storage buckets

- **Authentication** (Optional)
  - Google OAuth integration
  - NextAuth.js configuration

### Application Components

- **Next.js App** - Modern React framework with App Router
- **Supabase Integration** - Database, authentication, and real-time features
- **Tailwind CSS** - Utility-first CSS framework
- **TypeScript** - Type-safe JavaScript
- **Todo List Demo** - Working example with CRUD operations

## Architecture Overview

This template provides a complete full-stack architecture:

- **☁️ Cloudflare**: DNS management, CDN, and email routing
- **▲ Vercel**: Next.js hosting with serverless functions
- **🗄️ Supabase**: PostgreSQL database with authentication
- **🔐 Google Auth**: OAuth integration for social login
- **🏗️ Terraform**: Infrastructure as Code for all services

📊 **[View Detailed Architecture Diagrams →](./ARCHITECTURE.md)**  
🔄 **[View Simplified Component Flow →](./ARCHITECTURE_SIMPLE.md)**

### Project Structure

```
├── app/                      # Next.js App Router
│   ├── components/          # React components
│   ├── lib/                 # Utility functions
│   ├── types/               # TypeScript types
│   ├── auth/                # Authentication pages
│   ├── layout.tsx           # Root layout
│   ├── page.tsx             # Home page
│   └── globals.css          # Global styles
├── supabase/                 # Supabase configuration
│   ├── migrations/          # Database migrations
│   ├── seed.sql             # Seed data
│   └── config.toml          # Supabase config
├── terraform/                # Infrastructure as Code
│   ├── modules/             # Reusable Terraform modules
│   │   ├── dns/            # DNS configuration
│   │   ├── email_routing/  # Email forwarding
│   │   ├── vercel/         # Vercel project setup
│   │   └── supabase/       # Supabase configuration
│   ├── main.tf             # Main Terraform configuration
│   ├── variables.tf        # Variable definitions
│   ├── providers.tf        # Provider configurations
│   └── terraform.tfvars.example # Configuration template
├── scripts/                  # Automation scripts
│   ├── setup.sh            # Interactive setup script
│   └── quick-start.sh      # Terraform shortcuts
├── .github/                  # GitHub templates
├── .gitignore              # Git ignore rules
├── package.json            # Node.js dependencies
├── next.config.js          # Next.js configuration
├── tailwind.config.js      # Tailwind CSS configuration
├── vercel.json             # Vercel deployment config
├── env.example             # Environment variables template
└── README.md              # This file
```

## 🔧 Configuration

### Required API Tokens

#### Cloudflare API Token

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Click "Create Token" → "Create custom token"
3. Configure **Permissions** (Zone section):
   - `Zone:Zone:Read` (REQUIRED - needed to look up zones)
   - `Zone:DNS:Edit` (REQUIRED - needed to create DNS records)
   - `Zone:Email Routing:Read` (optional - if using email routing)
   - `Zone:Email Routing:Edit` (optional - if using email routing)
   
   **Important:** 
   - `Zone:Zone:Read` is separate from DNS permissions. You need **both** `Zone:Zone:Read` (to query zones) and `Zone:DNS:Edit` (to modify DNS records).
   - `Zone:DNS:Edit` is different from `DNS Settings:Edit` - make sure you select the correct permission.
   
4. Configure **Zone Resources**:
   - Select **"Include"** → **"All zones"** (recommended)
   - OR select **"Include"** → **"Specific zone"** → Select your zone
   
   **Critical:** The token MUST be scoped to your zone. If you get "Authentication error (10000)" when creating DNS records, verify your zone is included in Zone Resources.
   
5. Click "Continue to summary" → "Create Token"
6. Copy the token (you won't be able to see it again)

#### Vercel API Token

1. Go to [Vercel Account Tokens](https://vercel.com/account/tokens)
2. Create a new token
3. Copy the generated token

#### Supabase Access Token (Optional)

1. Go to [Supabase Dashboard](https://supabase.com/dashboard/account/tokens)
2. Create a new access token
3. Copy the generated token

### Environment Variables

The setup script will create a `terraform.tfvars` file with your configuration. This file contains sensitive information and should never be committed to version control.

## 🛠️ Usage

### Development Commands

```bash
# Start development server
npm run dev

# Build for production
npm run build

# Start production server
npm start

# Run linting
npm run lint

# Database operations
npm run db:reset    # Reset database
npm run db:migrate  # Run migrations
npm run db:seed     # Seed database
```

### Terraform Commands

Use the provided scripts for common operations:

```bash
# Initialize Terraform
./scripts/quick-start.sh init

# Plan infrastructure changes
./scripts/quick-start.sh plan

# Apply infrastructure changes
./scripts/quick-start.sh apply

# Destroy infrastructure
./scripts/quick-start.sh destroy

# Show current status
./scripts/quick-start.sh status
```

### Manual Terraform Commands

If you prefer to run Terraform commands directly:

```bash
cd terraform

# Initialize
terraform init

# Plan changes
terraform plan

# Apply changes
terraform apply

# Destroy infrastructure
terraform destroy
```

## 📚 Services Configuration

### Next.js Application

The included Next.js app demonstrates:

- **Authentication** - Sign up/sign in with Supabase Auth
- **Database Operations** - CRUD operations with todos
- **Real-time Updates** - Live data synchronization
- **TypeScript** - Type-safe development
- **Tailwind CSS** - Modern styling

### Cloudflare DNS

The template automatically configures:

- Root domain (`@`) → Your app URL
- WWW subdomain → Your app URL
- API subdomain → Your app URL

### Email Routing

Email forwarding is set up for:

- `support@yourdomain.com` → Your support email
- `contact@yourdomain.com` → Your contact email
- `hello@yourdomain.com` → Your contact email

### Vercel Integration

- Custom domain configuration
- GitHub repository connection
- Environment variable management
- Automatic deployments

### Supabase Database

The template includes:

- **Todos table** with Row Level Security
- **User authentication** with email/password
- **Database migrations** for schema management
- **Real-time subscriptions** for live updates

## 🔒 Security Best Practices

1. **Never commit sensitive data**:
   - `terraform.tfvars` is in `.gitignore`
   - Use environment variables in CI/CD

2. **API Token Security**:
   - Use least-privilege access
   - Rotate tokens regularly
   - Store securely in CI/CD systems

3. **Infrastructure Security**:
   - Review Terraform plans before applying
   - Use version control for infrastructure changes
   - Regular security updates

## 🚨 Troubleshooting

### Common Issues

#### Cloudflare API Token Problems

**Error: "no zone found" or "Authentication error (10000)"**
- **Problem**: Token missing `Zone:Zone:Read` permission or not scoped correctly
- **Solution**: 
  1. Go to Cloudflare Dashboard → API Tokens → Edit your token
  2. Add `Zone:Zone:Read` permission (separate from DNS permissions)
  3. Ensure token is scoped to your zone under "Zone Resources"
  4. You need **both** `Zone:Zone:Read` (to query zones) and `Zone:DNS:Edit` (to create records)

**Error: "failed to create DNS record: Authentication error (10000)"**
- **Problem**: Token missing `Zone:DNS:Edit` or not scoped to zone
- **Solution**: Verify token has `Zone:DNS:Edit` (not `DNS Settings:Edit`) and is included in Zone Resources

#### Email Routing Issues

**Error: "required rule id missing" or "Duplicated Zone rule" when updating email rules**
- **Problem**: Cloudflare provider has limitations updating existing email routing rules
- **Solution**: 
  1. Delete the rules in Cloudflare Dashboard → Email Routing
  2. Remove from Terraform state: `terraform state rm 'module.email_routing[0].cloudflare_email_routing_rule.RULE_NAME'`
  3. Run `terraform apply` to recreate them

#### Database Migration Problems

**Error: "failed to connect to postgres: failed SASL auth"**
- **Problem**: Wrong password or token type
- **Solution**: 
  - `SUPABASE_ACCESS_TOKEN` must be a **personal access token** (Account → Access Tokens), NOT service role key
  - `SUPABASE_DB_PASSWORD` is the database password, not an API key

**Error: "Circuit breaker open" or connection timeouts**
- **Problem**: Supabase infrastructure issue
- **Solution**: Check Supabase Dashboard for project status, try restarting the project, wait a few minutes

#### General Issues

1. **Domain Not in Cloudflare**
   - Ensure domain is added to Cloudflare first
   - Verify domain ownership
   - For subdomains, ensure parent zone exists

2. **Vercel Project Not Found**
   - Terraform will now create the project automatically
   - Verify GitHub repo connection in Terraform config

3. **API Token Expired**
   - Check tokens haven't expired
   - Create new tokens if needed

### Getting Help

- Check Terraform logs: `TF_LOG=DEBUG terraform apply`
- Verify API tokens with service providers
- Review detailed troubleshooting in [QUICK_START.md](QUICK_START.md)
- Check the blog posts (00-05) for step-by-step guidance

## 📝 Customization

### Adding New Services

1. Create a new module in `terraform/modules/`
2. Add variables to `terraform/variables.tf`
3. Update `terraform/main.tf` to use the module
4. Add configuration to setup script

### Modifying DNS Records

Edit `terraform/modules/dns/main.tf` to add or modify DNS records.

### Environment-Specific Configurations

Create environment-specific variable files:

- `terraform/terraform.tfvars.dev`
- `terraform/terraform.tfvars.prod`

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Terraform](https://terraform.io) for infrastructure as code
- [Cloudflare](https://cloudflare.com) for DNS and security
- [Vercel](https://vercel.com) for hosting
- [Supabase](https://supabase.com) for backend services


---

**Happy coding! 🚀**

For questions or support, please open an issue in the repository.

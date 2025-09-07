# Personal Project Template

A comprehensive template for quickly starting new personal projects with pre-configured infrastructure using Terraform. This template includes everything you need to get a modern web application up and running with DNS, hosting, database, and authentication services.

## 🚀 Quick Start

### 1. Use this template

```bash
# Create a new repository from this template
gh repo create your-project-name --template your-username/free_personal_stack

# Or clone and customize
git clone https://github.com/your-username/free_personal_stack.git your-project-name
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

### 4. Set up the Next.js app

```bash
# Install dependencies (this will resolve TypeScript/linting errors)
npm install

# Set up environment variables
cp env.example .env.local
# Edit .env.local with your Supabase credentials

# Run database migrations
npx supabase db push

# Start development server
npm run dev
```

**Note**: TypeScript and linting errors will appear until you run `npm install` to install the dependencies. This is normal for template repositories.

### 5. Set up Pre-commit Hooks (Optional)

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

- **Terraform** (>= 1.0) - [Install from terraform.io](https://terraform.io)
- **Node.js** (>= 18.0) - [Install from nodejs.org](https://nodejs.org)
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
2. Create a custom token with these permissions:
   - `Zone:Zone:Read`
   - `Zone:DNS:Edit`
   - `Zone:Email Routing:Read`
   - `Zone:Email Routing:Edit`
3. Set zone resources to include your domain

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

1. **API Token Permissions**
   - Verify token has required permissions
   - Check token hasn't expired

2. **Domain Not in Cloudflare**
   - Ensure domain is added to Cloudflare first
   - Verify domain ownership

3. **Email Routing Issues**
   - Email routing requires a paid Cloudflare plan
   - Check domain verification status

4. **Vercel Project Not Found**
   - Ensure project exists in Vercel
   - Check team ID if using team account

### Getting Help

- Check Terraform logs: `TF_LOG=DEBUG terraform apply`
- Verify API tokens with service providers
- Review Terraform documentation

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

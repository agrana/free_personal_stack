# Project Infrastructure

This directory contains Terraform configurations to manage your project's infrastructure, including Cloudflare DNS, email forwarding, Vercel hosting, and other services.

## Prerequisites

1. **Terraform**: Install Terraform (>= 1.0) from [terraform.io](https://terraform.io)
2. **Cloudflare Account**: You need a Cloudflare account with a domain
3. **Cloudflare API Token**: Generate an API token with appropriate permissions

## Setup

### 1. Get Cloudflare API Token

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Click "Create Token"
3. Use "Custom token" template
4. Set permissions:
   - `Zone:Zone:Read`
   - `Zone:DNS:Edit`
   - `Zone:Page Rules:Edit`
   - `Zone:Email Routing:Edit`
5. Set zone resources to include your domain
6. Copy the generated token

### 2. Configure Variables

1. Copy the example variables file:

   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Edit `terraform.tfvars` with your values:
   ```hcl
   domain_name = "yourdomain.com"
   app_url = "your-app.vercel.app"
   support_email = "your-support@gmail.com"
   contact_email = "your-contact@gmail.com"
   cloudflare_api_token = "your-api-token"
   vercel_api_token = "your-vercel-token"
   vercel_project_name = "your-project"
   github_repo = "your-username/your-repo"
   ```

### 3. Initialize and Deploy

```bash
# Initialize Terraform
terraform init

# Review the planned changes
terraform plan

# Apply the configuration
terraform apply
```

## What This Creates

### DNS Records

- `@` (root domain) → Your app URL
- `www` → Your app URL
- `api` → Your app URL

### Email Forwarding

- `support@yourdomain.com` → Your support email
- `contact@yourdomain.com` → Your contact email
- `hello@yourdomain.com` → Your contact email

### Page Rules

- Static assets caching (1 year TTL)
- Security headers and SSL enforcement

## Commands

```bash
# Plan changes
terraform plan

# Apply changes
terraform apply

# Destroy infrastructure (be careful!)
terraform destroy

# Show current state
terraform show

# List resources
terraform state list
```

## Environment Variables

You can also set the Cloudflare API token as an environment variable:

```bash
export CLOUDFLARE_API_TOKEN="your-api-token"
```

## Troubleshooting

### Common Issues

1. **API Token Permissions**: Ensure your token has the required permissions
2. **Domain Not in Cloudflare**: Make sure your domain is added to Cloudflare first
3. **Email Routing**: Email routing requires a paid Cloudflare plan

### Getting Help

- Check Terraform logs: `TF_LOG=DEBUG terraform apply`
- Verify Cloudflare API token: `curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" -H "Authorization: Bearer YOUR_TOKEN"`

## Security Notes

- Never commit `terraform.tfvars` to version control
- Use environment variables for sensitive data in CI/CD
- Regularly rotate API tokens
- Use least-privilege access for API tokens

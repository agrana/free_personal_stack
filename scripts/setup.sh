#!/bin/bash

# Personal Project Template Setup Script
# This script helps you quickly configure a new project from this template

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to prompt for input with default value
prompt_with_default() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    
    if [ -n "$default" ]; then
        read -p "$prompt [$default]: " input
        eval "$var_name=\${input:-$default}"
    else
        read -p "$prompt: " input
        eval "$var_name=\"$input\""
    fi
}

# Function to prompt for sensitive input
prompt_sensitive() {
    local prompt="$1"
    local var_name="$2"
    
    read -s -p "$prompt: " input
    echo
    eval "$var_name=\"$input\""
}

print_status "🚀 Welcome to the Personal Project Template Setup!"
echo
print_status "This script will help you configure your new project infrastructure."
echo

# Check prerequisites
print_status "Checking prerequisites..."

if ! command_exists terraform; then
    print_error "Terraform is not installed. Please install it from https://terraform.io"
    exit 1
fi

if ! command_exists git; then
    print_error "Git is not installed. Please install it first."
    exit 1
fi

print_success "Prerequisites check passed!"
echo

# Get project information
print_status "Let's gather some information about your project..."
echo

prompt_with_default "Project name (used for Vercel project)" "" PROJECT_NAME
prompt_with_default "Domain name (e.g., myproject.com)" "" DOMAIN_NAME
prompt_with_default "App URL (e.g., myproject.vercel.app)" "" APP_URL
prompt_with_default "GitHub repository (owner/repo)" "" GITHUB_REPO
prompt_with_default "Support email" "" SUPPORT_EMAIL
prompt_with_default "Contact email" "" CONTACT_EMAIL

echo
print_status "Now let's configure your API tokens..."
echo

prompt_sensitive "Cloudflare API Token" CLOUDFLARE_TOKEN
prompt_sensitive "Vercel API Token" VERCEL_TOKEN

echo
prompt_with_default "Vercel Team ID (leave empty for personal account)" "" VERCEL_TEAM_ID

# Optional services
echo
print_status "Optional services configuration..."
echo

read -p "Do you want to configure Supabase? (y/N): " USE_SUPABASE
if [[ $USE_SUPABASE =~ ^[Yy]$ ]]; then
    prompt_with_default "Supabase Project URL" "" SUPABASE_URL
    prompt_sensitive "Supabase Anonymous Key" SUPABASE_ANON_KEY
    prompt_sensitive "Supabase Service Role Key" SUPABASE_SERVICE_KEY
    prompt_sensitive "Supabase Access Token" SUPABASE_ACCESS_TOKEN
    prompt_with_default "Supabase Project ID" "" SUPABASE_PROJECT_ID
fi

read -p "Do you want to configure Google OAuth? (y/N): " USE_GOOGLE_OAUTH
if [[ $USE_GOOGLE_OAUTH =~ ^[Yy]$ ]]; then
    prompt_with_default "Google Client ID" "" GOOGLE_CLIENT_ID
    prompt_sensitive "Google Client Secret" GOOGLE_CLIENT_SECRET
fi

read -p "Do you want to configure NextAuth? (y/N): " USE_NEXTAUTH
if [[ $USE_NEXTAUTH =~ ^[Yy]$ ]]; then
    prompt_sensitive "NextAuth Secret" NEXTAUTH_SECRET
fi

# Create terraform.tfvars
print_status "Creating terraform.tfvars file..."

cat > terraform/terraform.tfvars << EOF
# Domain configuration
domain_name = "$DOMAIN_NAME"
app_url     = "$APP_URL"

# Email forwarding configuration
support_email = "$SUPPORT_EMAIL"
contact_email = "$CONTACT_EMAIL"

# Cloudflare API token
cloudflare_api_token = "$CLOUDFLARE_TOKEN"

# Vercel Configuration
vercel_api_token = "$VERCEL_TOKEN"
vercel_team_id   = "$VERCEL_TEAM_ID"
vercel_project_name = "$PROJECT_NAME"
github_repo      = "$GITHUB_REPO"
EOF

# Add optional configurations
if [[ $USE_SUPABASE =~ ^[Yy]$ ]]; then
    cat >> terraform/terraform.tfvars << EOF

# Supabase Configuration
supabase_url                = "$SUPABASE_URL"
supabase_anon_key          = "$SUPABASE_ANON_KEY"
supabase_service_role_key  = "$SUPABASE_SERVICE_KEY"
supabase_access_token = "$SUPABASE_ACCESS_TOKEN"
supabase_project_id   = "$SUPABASE_PROJECT_ID"
EOF
fi

if [[ $USE_GOOGLE_OAUTH =~ ^[Yy]$ ]]; then
    cat >> terraform/terraform.tfvars << EOF

# Google OAuth Configuration
google_client_id     = "$GOOGLE_CLIENT_ID"
google_client_secret = "$GOOGLE_CLIENT_SECRET"
EOF
fi

if [[ $USE_NEXTAUTH =~ ^[Yy]$ ]]; then
    cat >> terraform/terraform.tfvars << EOF

# NextAuth Configuration
nextauth_secret = "$NEXTAUTH_SECRET"
EOF
fi

print_success "terraform.tfvars created!"

# Install Node.js dependencies
print_status "Installing Node.js dependencies..."
npm install

print_success "Dependencies installed!"

# Initialize Terraform
print_status "Initializing Terraform..."
cd terraform
terraform init

print_success "Terraform initialized!"

# Show next steps
echo
print_success "🎉 Setup completed successfully!"
echo
print_status "Next steps:"
echo "1. Review your configuration in terraform/terraform.tfvars"
echo "2. Set up environment variables: cp env.example .env.local"
echo "3. Run 'terraform plan' to see what will be created"
echo "4. Run 'terraform apply' to create your infrastructure"
echo "5. Run 'npm run dev' to start the development server"
echo
print_status "Useful commands:"
echo "  terraform plan     - Preview changes"
echo "  terraform apply    - Apply changes"
echo "  terraform destroy  - Destroy infrastructure"
echo
print_warning "Remember to never commit terraform.tfvars to version control!"
echo
print_status "Happy coding! 🚀"

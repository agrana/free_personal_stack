#!/bin/bash

# GitHub Secrets Checker Script
# This script helps you verify which secrets are configured in your GitHub repository

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_status "🔍 GitHub Secrets Checklist"
echo
print_status "Required secrets for CI/CD deployment:"
echo

# List of required secrets
REQUIRED_SECRETS=(
    "SUPABASE_ACCESS_TOKEN:Required for running Supabase migrations"
    "SUPABASE_PROJECT_ID:Required for Supabase CLI operations"
    "VERCEL_TOKEN:Required for Vercel deployments"
    "VERCEL_ORG_ID:Required for Vercel organization access"
    "VERCEL_PROJECT_ID:Required for Vercel project deployment"
    "NEXT_PUBLIC_SUPABASE_URL:Required for application runtime"
    "NEXT_PUBLIC_SUPABASE_ANON_KEY:Required for Supabase client"
    "SUPABASE_SERVICE_ROLE_KEY:Required for admin operations"
    "NEXT_PUBLIC_SITE_URL:Required for production site URL"
)

print_status "To add secrets, go to:"
echo "  https://github.com/$(git config --get remote.origin.url | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/settings/secrets/actions"
echo

print_status "Secret Checklist:"
echo

for secret_info in "${REQUIRED_SECRETS[@]}"; do
    secret_name="${secret_info%%:*}"
    secret_desc="${secret_info#*:}"
    echo "  [ ] $secret_name"
    echo "      → $secret_desc"
done

echo
print_warning "Note: This script cannot verify secrets are actually set."
print_warning "You need to check manually in GitHub repository settings."
echo
print_status "Quick links:"
echo "  - Supabase Tokens: https://supabase.com/dashboard/account/tokens"
echo "  - Vercel Tokens: https://vercel.com/account/tokens"
echo "  - Supabase Data API (Project URL): Settings → Data API"
echo "  - Supabase API Keys: Settings → API Keys"
echo


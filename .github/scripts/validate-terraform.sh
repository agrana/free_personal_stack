#!/bin/bash
# Terraform validation script
# Validates Terraform configuration before committing

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-$REPO_ROOT/terraform}"

cd "$TERRAFORM_DIR"

echo "🔍 Validating Terraform configuration..."
echo "Working directory: $TERRAFORM_DIR"
echo ""

# Check if terraform is installed
if ! command -v terraform &> /dev/null; then
    echo "❌ Error: terraform command not found"
    echo "   Install Terraform: https://www.terraform.io/downloads"
    exit 1
fi

# Generate NextAuth secret if not set
if [ -z "$TF_VAR_nextauth_secret" ]; then
    echo "⚠️  TF_VAR_nextauth_secret not set, generating temporary value..."
    export TF_VAR_nextauth_secret=$(openssl rand -base64 32)
fi

# Initialize Terraform (only if .terraform doesn't exist)
if [ ! -d ".terraform" ]; then
    echo "📦 Initializing Terraform..."
    if ! terraform init -backend=false -input=false > /dev/null 2>&1; then
        echo "❌ Error: terraform init failed"
        terraform init -backend=false -input=false
        exit 1
    fi
else
    echo "📦 Terraform already initialized, skipping init"
fi

# Format check
echo "🎨 Checking Terraform formatting..."
if ! terraform fmt -check -recursive > /dev/null 2>&1; then
    echo "❌ Error: Terraform files are not properly formatted"
    echo "   Run 'terraform fmt -recursive' to fix formatting"
    terraform fmt -check -recursive
    exit 1
fi
echo "✅ Formatting is correct"

# Validate
echo "✅ Validating Terraform configuration..."
if ! terraform validate -no-color; then
    echo "❌ Error: Terraform validation failed"
    exit 1
fi

echo ""
echo "✅ All Terraform validation checks passed!"


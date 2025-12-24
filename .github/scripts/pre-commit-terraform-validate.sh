#!/bin/bash
# Custom Terraform validation hook for pre-commit
# This ensures nextauth_secret is set before validation

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-$REPO_ROOT/terraform}"

cd "$TERRAFORM_DIR"

# Generate NextAuth secret if not set
if [ -z "$TF_VAR_nextauth_secret" ]; then
    export TF_VAR_nextauth_secret=$(openssl rand -base64 32 2>/dev/null || echo "temp-secret-for-validation")
fi

# Run terraform validate
terraform validate -json


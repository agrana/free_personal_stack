#!/bin/bash

# Comprehensive Pre-commit Check Script
# This script runs all quality checks before committing

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

# Function to run command with error handling
run_check() {
    local check_name="$1"
    local command="$2"
    
    print_status "Running $check_name..."
    
    if eval "$command"; then
        print_success "$check_name passed"
        return 0
    else
        print_error "$check_name failed"
        return 1
    fi
}

# Start of checks
echo
print_status "🔍 Running comprehensive pre-commit checks..."
echo

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    print_warning "Not in a git repository, skipping git-related checks"
fi

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    print_error "node_modules not found. Please run 'npm install' first."
    exit 1
fi

# Initialize counters
total_checks=0
passed_checks=0
failed_checks=0

# 1. TypeScript type checking
total_checks=$((total_checks + 1))
if run_check "TypeScript type checking" "npm run type-check"; then
    passed_checks=$((passed_checks + 1))
else
    failed_checks=$((failed_checks + 1))
fi

# 2. ESLint checking
total_checks=$((total_checks + 1))
if run_check "ESLint checking" "npm run lint"; then
    passed_checks=$((passed_checks + 1))
else
    failed_checks=$((failed_checks + 1))
fi

# 3. Prettier format checking
total_checks=$((total_checks + 1))
if run_check "Prettier format checking" "npm run format:check"; then
    passed_checks=$((passed_checks + 1))
else
    failed_checks=$((failed_checks + 1))
fi

# 4. Next.js build check (if Next.js files exist)
if [ -f "next.config.js" ] || [ -f "next.config.ts" ]; then
    total_checks=$((total_checks + 1))
    if run_check "Next.js build check" "npm run build"; then
        passed_checks=$((passed_checks + 1))
    else
        failed_checks=$((failed_checks + 1))
    fi
fi

# 5. Terraform validation (if terraform directory exists)
if [ -d "terraform" ]; then
    # Change to terraform directory for all terraform commands
    cd terraform
    
    # Terraform format check
    total_checks=$((total_checks + 1))
    if run_check "Terraform format check" "terraform fmt -check -recursive"; then
        passed_checks=$((passed_checks + 1))
    else
        failed_checks=$((failed_checks + 1))
        print_warning "Run 'npm run terraform:fmt' to fix formatting issues"
    fi
    
    # Terraform validation
    total_checks=$((total_checks + 1))
    if run_check "Terraform validation" "terraform validate"; then
        passed_checks=$((passed_checks + 1))
    else
        failed_checks=$((failed_checks + 1))
    fi
    
    # Terraform security check (if tfsec is available)
    if command_exists tfsec; then
        total_checks=$((total_checks + 1))
        if run_check "Terraform security scan" "tfsec ."; then
            passed_checks=$((passed_checks + 1))
        else
            failed_checks=$((failed_checks + 1))
        fi
    else
        print_warning "tfsec not found, skipping Terraform security scan"
    fi
    
    # Terraform linting (if tflint is available)
    if command_exists tflint; then
        total_checks=$((total_checks + 1))
        if run_check "Terraform linting" "tflint"; then
            passed_checks=$((passed_checks + 1))
        else
            failed_checks=$((failed_checks + 1))
        fi
    else
        print_warning "tflint not found, skipping Terraform linting"
    fi
    
    # Return to project root
    cd ..
fi

# 6. Supabase migration check (if supabase directory exists and Docker is running)
if [ -d "supabase" ]; then
    if command_exists supabase && command_exists docker; then
        # Check if Docker is running
        if docker info >/dev/null 2>&1; then
            total_checks=$((total_checks + 1))
            if run_check "Supabase migration check" "supabase db diff --schema public"; then
                passed_checks=$((passed_checks + 1))
            else
                failed_checks=$((failed_checks + 1))
            fi
        else
            print_warning "Docker not running, skipping Supabase migration check"
        fi
    else
        print_warning "Supabase CLI or Docker not found, skipping migration check"
    fi
fi

# 7. Test suite (if tests exist)
if [ -d "tests" ] || [ -d "__tests__" ] || [ -d "test" ] || [ -f "jest.config.js" ] || [ -f "vitest.config.js" ]; then
    total_checks=$((total_checks + 1))
    if run_check "Test suite" "npm test"; then
        passed_checks=$((passed_checks + 1))
    else
        failed_checks=$((failed_checks + 1))
    fi
else
    print_warning "No test suite found, skipping tests"
fi

# 8. Security audit (optional)
if [ "${SKIP_AUDIT:-false}" != "true" ]; then
    total_checks=$((total_checks + 1))
    if run_check "Security audit" "npm audit --audit-level moderate"; then
        passed_checks=$((passed_checks + 1))
    else
        failed_checks=$((failed_checks + 1))
        print_warning "Security audit found issues. Run 'npm audit fix' to resolve."
    fi
fi

# Summary
echo
print_status "📊 Pre-commit check summary:"
echo "  Total checks: $total_checks"
echo "  Passed: $passed_checks"
echo "  Failed: $failed_checks"

if [ $failed_checks -eq 0 ]; then
    print_success "🎉 All checks passed! Ready to commit."
    exit 0
else
    print_error "❌ $failed_checks check(s) failed. Please fix the issues before committing."
    echo
    print_status "💡 Quick fixes:"
    echo "  • Run 'npm run lint:fix' to fix linting issues"
    echo "  • Run 'npm run format' to fix formatting issues"
    echo "  • Run 'npm run type-check' to see TypeScript errors"
    echo "  • Run 'npm run terraform:fmt' to fix Terraform formatting"
    echo "  • Run 'npm run terraform:validate' to check Terraform syntax"
    echo "  • Run 'npm audit fix' to fix security issues"
    exit 1
fi

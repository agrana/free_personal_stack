#!/bin/bash
# Quick Start Script — APP-002 hardened entrypoints.
# apply/destroy are disabled; they must not bypass the plan ownership guard.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

show_usage() {
    echo "Usage: $0 [COMMAND]"
    echo
    echo "Commands:"
    echo "  init     - Initialize Terraform (backend=false recommended locally)"
    echo "  plan     - Plan infrastructure changes (read-only intent)"
    echo "  status   - Show current infrastructure status"
    echo "  help     - Show this help message"
    echo
    echo "Disabled by APP-002 (do not bypass):"
    echo "  apply    - blocked until APP-005 remote verification + plan guard"
    echo "  destroy  - blocked; shared project/resources must not be destroyed here"
}

if [ ! -d "terraform" ]; then
    print_error "terraform directory not found. Run from the project root."
    exit 1
fi

case "${1:-help}" in
    init)
        print_status "Initializing Terraform..."
        (cd terraform && terraform init -backend=false)
        print_success "Terraform initialized (backend=false)."
        ;;
    plan)
        print_status "Planning infrastructure changes..."
        print_warning "APP-002: validate JSON plan with ./scripts/validate-terraform-plan.sh before any apply."
        (cd terraform && terraform plan)
        ;;
    apply)
        print_error "APP-002: terraform apply is disabled."
        print_error "Re-enable only after APP-005 enforces remote account/permission checks and the offline plan ownership guard."
        exit 1
        ;;
    destroy)
        print_error "APP-002: terraform destroy is disabled."
        print_error "Shared Supabase project and cross-app DNS/mail resources must not be destroyed by this template."
        exit 1
        ;;
    status)
        print_status "Current infrastructure status:"
        (cd terraform && terraform show)
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        print_error "Unknown command: $1"
        show_usage
        exit 1
        ;;
esac

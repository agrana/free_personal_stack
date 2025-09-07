#!/bin/bash

# Quick Start Script for Personal Project Template
# This script provides shortcuts for common terraform operations

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

# Function to show usage
show_usage() {
    echo "Usage: $0 [COMMAND]"
    echo
    echo "Commands:"
    echo "  init     - Initialize Terraform"
    echo "  plan     - Plan infrastructure changes"
    echo "  apply    - Apply infrastructure changes"
    echo "  destroy  - Destroy infrastructure"
    echo "  status   - Show current infrastructure status"
    echo "  help     - Show this help message"
    echo
    echo "Examples:"
    echo "  $0 init"
    echo "  $0 plan"
    echo "  $0 apply"
}

# Check if terraform directory exists
if [ ! -d "terraform" ]; then
    print_error "terraform directory not found. Please run this script from the project root."
    exit 1
fi

# Check if terraform.tfvars exists
if [ ! -f "terraform/terraform.tfvars" ]; then
    print_error "terraform.tfvars not found. Please run ./scripts/setup.sh first."
    exit 1
fi

# Change to terraform directory
cd terraform

# Handle commands
case "${1:-help}" in
    init)
        print_status "Initializing Terraform..."
        terraform init
        print_success "Terraform initialized!"
        ;;
    plan)
        print_status "Planning infrastructure changes..."
        terraform plan
        ;;
    apply)
        print_status "Applying infrastructure changes..."
        terraform apply
        print_success "Infrastructure applied!"
        ;;
    destroy)
        print_warning "This will destroy all infrastructure. Are you sure? (y/N)"
        read -r response
        if [[ $response =~ ^[Yy]$ ]]; then
            print_status "Destroying infrastructure..."
            terraform destroy
            print_success "Infrastructure destroyed!"
        else
            print_status "Destroy cancelled."
        fi
        ;;
    status)
        print_status "Current infrastructure status:"
        terraform show
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        print_error "Unknown command: $1"
        echo
        show_usage
        exit 1
        ;;
esac

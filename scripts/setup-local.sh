#!/bin/bash

# Local Development Setup Script
# This script sets up your local development environment

# Don't exit on error - we want to handle Docker gracefully
set +e

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
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_status "🚀 Setting up local development environment..."

# Check if .env.local exists
if [ -f ".env.local" ]; then
    print_warning ".env.local already exists. Skipping creation."
else
    print_status "Creating .env.local from template..."
    cp env.example .env.local
    print_success ".env.local created!"
    print_warning "Please edit .env.local with your Supabase credentials"
fi

# Install dependencies if node_modules doesn't exist
if [ ! -d "node_modules" ]; then
    print_status "Installing npm dependencies..."
    npm install
    print_success "Dependencies installed!"
else
    print_status "Dependencies already installed. Skipping..."
fi

# Check for migrations - use remote Supabase (no Docker needed)
print_status "Database migrations..."
print_status "Note: You can use your remote Supabase project (no Docker required)."
echo

# Check if Docker is available (for local Supabase option)
DOCKER_AVAILABLE=false
if command -v docker &> /dev/null; then
    if docker info &> /dev/null 2>&1; then
        DOCKER_AVAILABLE=true
    fi
fi

if [ "$DOCKER_AVAILABLE" = true ]; then
    read -p "Run migrations on (R)emote Supabase or (L)ocal Supabase? [R/l]: " MIGRATION_TYPE
    MIGRATION_TYPE=${MIGRATION_TYPE:-R}
else
    print_warning "Docker not available. Using remote Supabase (no Docker needed)."
    MIGRATION_TYPE="R"
fi

if [[ $MIGRATION_TYPE =~ ^[Ll]$ ]]; then
    # Local Supabase (requires Docker)
    print_status "Setting up local Supabase..."
    if command -v supabase &> /dev/null || command -v npx &> /dev/null; then
        if ! npx supabase status &> /dev/null 2>&1; then
            print_status "Starting Supabase locally..."
            npx supabase start 2>&1 | grep -v "Unknown config field" || true
            if [ ${PIPESTATUS[0]} -eq 0 ]; then
                print_success "Supabase started locally!"
            fi
        fi
        print_status "Running migrations on local Supabase..."
        npx supabase db push 2>&1
        print_success "Migrations applied to local Supabase!"
    fi
else
    # Remote Supabase (no Docker needed)
    print_status "To run migrations on your remote Supabase project:"
    echo "  npx supabase db push --project-ref your-project-id"
    echo
    print_status "Or use Supabase Dashboard → SQL Editor to run migrations manually."
    print_status "Your app will use the remote Supabase project configured in .env.local"
fi

echo
print_success "🎉 Local development environment ready!"
echo
print_status "Next steps:"
echo "1. Make sure .env.local has your Supabase credentials (from remote project)"
echo "2. Run 'npm run dev' to start the development server"
echo "3. Visit http://localhost:3000"
echo
if [ "$DOCKER_AVAILABLE" = false ]; then
    print_status "💡 Tip: You're using remote Supabase (no Docker needed). This is the recommended setup!"
fi
echo


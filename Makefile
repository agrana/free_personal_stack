# Personal Project Template Makefile
# Provides convenient commands for infrastructure management

.PHONY: help setup init plan apply destroy status clean

# Default target
help: ## Show this help message
	@echo "Personal Project Template - Available Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Run the interactive setup script
	@echo "🚀 Running project setup..."
	@./scripts/setup.sh

init: ## Initialize Terraform
	@echo "🔧 Initializing Terraform..."
	@cd terraform && terraform init

plan: ## Plan infrastructure changes
	@echo "📋 Planning infrastructure changes..."
	@cd terraform && terraform plan

apply: ## Apply infrastructure changes
	@echo "🚀 Applying infrastructure changes..."
	@cd terraform && terraform apply

destroy: ## Destroy infrastructure (with confirmation)
	@echo "⚠️  This will destroy all infrastructure. Are you sure? (y/N)"
	@read -r response; \
	if [ "$$response" = "y" ] || [ "$$response" = "Y" ]; then \
		echo "🗑️  Destroying infrastructure..."; \
		cd terraform && terraform destroy; \
	else \
		echo "❌ Destroy cancelled."; \
	fi

status: ## Show current infrastructure status
	@echo "📊 Current infrastructure status:"
	@cd terraform && terraform show

clean: ## Clean up Terraform files (use with caution)
	@echo "🧹 Cleaning up Terraform files..."
	@cd terraform && rm -rf .terraform .terraform.lock.hcl terraform.tfstate*

validate: ## Validate Terraform configuration (format, init, validate)
	@echo "✅ Validating Terraform configuration..."
	@.github/scripts/validate-terraform.sh

format: ## Format Terraform files
	@echo "🎨 Formatting Terraform files..."
	@cd terraform && terraform fmt -recursive

# Quick shortcuts
i: init ## Alias for init
p: plan ## Alias for plan
a: apply ## Alias for apply
d: destroy ## Alias for destroy
s: status ## Alias for status

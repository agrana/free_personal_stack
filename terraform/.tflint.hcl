# TFLint configuration for terraform directory
# This file configures TFLint for Terraform code quality checks

config {
  # Disable rules that are too strict for templates
  disabled_by_default = false
  
  # Format output
  format = "compact"
  
  # Force plugin installation
  force = false
}

# Disable unused declarations rule for template variables
rule "terraform_unused_declarations" {
  enabled = false
}

# TFLint configuration
# This file configures TFLint for Terraform code quality checks

config {
  # Enable module inspection
  module = true
  
  # Enable plugin inspection
  plugin = true
  
  # Disable rules that are too strict for templates
  disabled_by_default = false
  
  # Format output
  format = "compact"
  
  # Force plugin installation
  force = false
}

# AWS provider rules
plugin "aws" {
  enabled = true
  version = "0.28.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
  
  # Disable specific rules that might be too strict for templates
  deep_check = false
}

# General rules
rule "terraform_comment_syntax" {
  enabled = true
}

rule "terraform_deprecated_index" {
  enabled = true
}

rule "terraform_deprecated_interpolation" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = false  # Disabled for templates
}

rule "terraform_documented_variables" {
  enabled = false  # Disabled for templates
}

rule "terraform_documented_variables" {
  enabled = false  # Disabled for templates
}

rule "terraform_module_pinned_source" {
  enabled = true
}

rule "terraform_module_version" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true
  
  # Naming conventions
  format = "snake_case"
  
  # Check resources
  resource {
    format = "snake_case"
  }
  
  # Check data sources
  data {
    format = "snake_case"
  }
  
  # Check variables
  variable {
    format = "snake_case"
  }
  
  # Check outputs
  output {
    format = "snake_case"
  }
  
  # Check locals
  local {
    format = "snake_case"
  }
}

rule "terraform_required_providers" {
  enabled = true
}

rule "terraform_required_version" {
  enabled = true
}

rule "terraform_standard_module_structure" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = false
}

rule "terraform_unused_required_providers" {
  enabled = true
}

rule "terraform_workspace_remote" {
  enabled = true
}

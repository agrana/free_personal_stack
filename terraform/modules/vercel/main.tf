terraform {
  required_providers {
    vercel = {
      source  = "vercel/vercel"
      version = "~> 1.0"
    }
  }
}

# Data source to reference existing Vercel project
data "vercel_project" "main" {
  name    = var.project_name
  team_id = var.team_id != "" ? var.team_id : null
}

# Custom Domain
resource "vercel_project_domain" "main" {
  project_id = data.vercel_project.main.id
  domain     = var.domain_name

  # Import existing domain - ignore changes to match current state
  lifecycle {
    ignore_changes = [
      redirect,
      redirect_status_code
    ]
  }
}

# Environment Variables
resource "vercel_project_environment_variable" "env_vars" {
  for_each = var.environment_variables

  project_id = data.vercel_project.main.id
  team_id    = var.team_id != "" ? var.team_id : null
  key        = each.key
  value      = each.value.value
  target     = each.value.target
}

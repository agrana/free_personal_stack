terraform {
  required_providers {
    vercel = {
      source  = "vercel/vercel"
      version = "~> 1.0"
    }
  }
}

# Per-app Vercel project. Protected against accidental terraform destroy (APP-002).
resource "vercel_project" "main" {
  name      = var.project_name
  team_id   = var.team_id != "" ? var.team_id : null
  framework = "nextjs"

  git_repository = {
    type = "github"
    repo = var.github_repo
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "vercel_project_domain" "main" {
  project_id = vercel_project.main.id
  domain     = var.domain_name

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      redirect,
      redirect_status_code
    ]
  }
}

resource "vercel_project_environment_variable" "env_vars" {
  for_each = var.environment_variables

  project_id = vercel_project.main.id
  team_id    = var.team_id != "" ? var.team_id : null
  key        = each.key
  value      = each.value.value
  target     = each.value.target
}

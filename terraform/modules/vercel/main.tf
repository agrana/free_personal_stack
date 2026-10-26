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
  # Iterate allowlisted NAMES only. Index sensitive VALUES by key — never
  # for_each over a map whose keys inherited sensitivity from secret values.
  for_each = var.environment_variable_keys

  project_id = vercel_project.main.id
  team_id    = var.team_id != "" ? var.team_id : null
  key        = each.key
  value      = var.environment_variables[each.key].value
  target     = var.environment_variables[each.key].target

  lifecycle {
    precondition {
      condition     = var.supabase_public_keys_safe
      error_message = "Public Supabase key required with public-class syntax (sb_publishable_* and/or legacy anon JWT). Privileged sb_secret_*/service_role/supabase_admin values are rejected even when unequal to supabase_service_role_key. Modern and legacy may coexist with different values."
    }
  }
}

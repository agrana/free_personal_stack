# Local variables for common values
locals {
  domain_name = var.domain_name
  zone_id     = data.cloudflare_zone.main.id
  
  # Environment variables for Vercel
  vercel_env_vars = {
    NEXT_PUBLIC_SUPABASE_URL = {
      value  = var.supabase_url
      target = ["production", "preview", "development"]
    }
    NEXT_PUBLIC_SUPABASE_ANON_KEY = {
      value  = var.supabase_anon_key
      target = ["production", "preview", "development"]
    }
    SUPABASE_SERVICE_ROLE_KEY = {
      value  = var.supabase_service_role_key
      target = ["production", "preview", "development"]
    }
    NEXT_PUBLIC_SITE_URL = {
      value  = var.site_url != "" ? var.site_url : "https://${var.domain_name}"
      target = ["production", "preview", "development"]
    }
  }
}

# Data source to get the zone ID for the domain
data "cloudflare_zone" "main" {
  name = local.domain_name
}

# DNS Module
module "dns" {
  source = "./modules/dns"

  zone_id     = local.zone_id
  domain_name = local.domain_name
  app_url     = var.app_url
}

# Email Routing Module
module "email_routing" {
  source = "./modules/email_routing"

  zone_id       = local.zone_id
  domain_name   = local.domain_name
  support_email = var.support_email
  contact_email = var.contact_email
}

# Vercel Module
module "vercel" {
  source = "./modules/vercel"

  project_name        = var.vercel_project_name
  domain_name         = local.domain_name
  github_repo         = var.github_repo
  team_id             = var.vercel_team_id
  environment_variables = local.vercel_env_vars
}

# Supabase Module
module "supabase" {
  source = "./modules/supabase"

  project_id = var.supabase_project_id
}

# Outputs
output "vercel_project_url" {
  description = "The URL of the Vercel project"
  value       = module.vercel.project_url
}

output "vercel_domain_url" {
  description = "The custom domain URL"
  value       = module.vercel.domain_url
}

# Supabase Outputs
output "supabase_project_url" {
  description = "The URL of the Supabase project"
  value       = module.supabase.project_url
}

output "supabase_api_url" {
  description = "The API URL of the Supabase project"
  value       = module.supabase.api_url
}

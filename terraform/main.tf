# Local variables for common values
locals {
  domain_name = var.domain_name
  # Extract parent zone from domain (e.g., "fpt.alfonsograna.com" -> "alfonsograna.com")
  parent_zone = join(".", slice(split(".", var.domain_name), length(split(".", var.domain_name)) - 2, length(split(".", var.domain_name))))
  # Use created zone ID if creating, otherwise use existing zone ID
  zone_id = var.create_cloudflare_zone ? cloudflare_zone.new[0].id : data.cloudflare_zone.existing[0].id

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

# Reference existing Cloudflare zone (parent domain)
# For subdomains, use the parent zone (e.g., for fpt.example.com, use example.com zone)
# For root domains, use the domain itself as the zone
data "cloudflare_zone" "existing" {
  count = var.create_cloudflare_zone ? 0 : 1
  name  = local.parent_zone
}

# Create new Cloudflare zone if it doesn't exist
resource "cloudflare_zone" "new" {
  count      = var.create_cloudflare_zone ? 1 : 0
  zone       = local.parent_zone
  plan       = "free"
  account_id = var.cloudflare_account_id != "" ? var.cloudflare_account_id : null
}

# DNS Module
module "dns" {
  source = "./modules/dns"

  zone_id     = local.zone_id
  domain_name = local.domain_name
  parent_zone = local.parent_zone
  app_url     = var.app_url
}

# Email Routing Module (optional - requires account-level API token permissions)
module "email_routing" {
  source = "./modules/email_routing"
  count  = var.enable_email_routing && var.support_email_destination != "" && var.contact_email_destination != "" ? 1 : 0

  zone_id                       = local.zone_id
  domain_name                   = local.domain_name
  support_email_destination     = var.support_email_destination
  contact_email_destination     = var.contact_email_destination
  manage_email_routing_settings = var.manage_email_routing_settings
}

# Vercel Module
module "vercel" {
  source = "./modules/vercel"

  project_name          = var.vercel_project_name
  domain_name           = local.domain_name
  github_repo           = var.github_repo
  team_id               = var.vercel_team_id
  environment_variables = local.vercel_env_vars
}

# Supabase Module
module "supabase" {
  source = "./modules/supabase"

  project_id              = var.supabase_project_id
  supabase_access_token   = var.supabase_access_token
  enable_project_deletion = var.enable_supabase_project_deletion
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

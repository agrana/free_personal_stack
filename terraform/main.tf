# Local variables for common values
locals {
  domain_name = var.domain_name
  zone_id     = data.cloudflare_zone.main.id
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

  project_name = var.vercel_project_name
  domain_name  = local.domain_name
  github_repo  = var.github_repo
  team_id      = var.vercel_team_id
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

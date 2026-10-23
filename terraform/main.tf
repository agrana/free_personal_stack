# APP-002: template safe for a single existing shared Supabase project.
# Each app owns only its distinct Vercel project + approved hostname.
# Shared DNS zone, mail routing, and Supabase project lifecycle are out of scope.

locals {
  approved_hostname = var.domain_name != "" ? var.domain_name : var.approved_hostname
  parent_zone = (
    var.cloudflare_zone_name != "" ? var.cloudflare_zone_name : join(
      ".",
      slice(
        split(".", local.approved_hostname),
        length(split(".", local.approved_hostname)) - 2,
        length(split(".", local.approved_hostname))
      )
    )
  )
  dns_enabled = var.enable_dns && var.dns_provider == "cloudflare"
  app_url     = var.app_url != "" ? var.app_url : "${var.vercel_project_name}.vercel.app"
  github_repo = var.github_repo != "" ? var.github_repo : "unknown/unknown"
  site_url    = var.site_url != "" ? var.site_url : "https://${local.approved_hostname}"

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
      value  = local.site_url
      target = ["production", "preview", "development"]
    }
  }
}

check "dns_fail_closed" {
  assert {
    condition     = !var.enable_dns || var.dns_provider == "cloudflare"
    error_message = "APP-002: enable_dns requires dns_provider=cloudflare. unresolved fails closed; cloudfront is not implemented in this template (do not pretend Cloudflare covers it)."
  }
}

check "no_shared_zone_ownership" {
  assert {
    condition     = var.create_cloudflare_zone == false
    error_message = "APP-002: apps must not create or own the shared DNS zone."
  }
}

check "no_supabase_project_deletion" {
  assert {
    condition     = var.enable_supabase_project_deletion == false
    error_message = "APP-002: shared Supabase project deletion is forbidden."
  }
}

check "per_app_state_key_present" {
  assert {
    condition     = length(var.deployment_state_key) > 0
    error_message = "APP-002: deployment_state_key is required; supply a unique per-app key."
  }
}

# Existing zone lookup only — never create a zone.
data "cloudflare_zone" "existing" {
  count = local.dns_enabled ? 1 : 0
  name  = local.parent_zone
}

module "dns" {
  source = "./modules/dns"
  count  = local.dns_enabled ? 1 : 0

  zone_id     = data.cloudflare_zone.existing[0].id
  domain_name = local.approved_hostname
  parent_zone = local.parent_zone
  app_url     = local.app_url
}

# Mail routing disabled by default; module retained but not selected unless explicitly enabled.
# Shared mail routing is not app-owned (APP-002). Prefer leave enable_email_routing=false.
module "email_routing" {
  source = "./modules/email_routing"
  # Shared mail routing is not app-owned; keep disabled unless explicitly forced with DNS.
  count = var.enable_email_routing && local.dns_enabled && var.support_email_destination != "" && var.contact_email_destination != "" ? 1 : 0

  zone_id                       = data.cloudflare_zone.existing[0].id
  domain_name                   = local.approved_hostname
  support_email_destination     = var.support_email_destination
  contact_email_destination     = var.contact_email_destination
  manage_email_routing_settings = var.manage_email_routing_settings
}

module "vercel" {
  source = "./modules/vercel"

  project_name          = var.vercel_project_name
  domain_name           = local.approved_hostname
  github_repo           = local.github_repo
  team_id               = var.vercel_team_id
  environment_variables = local.vercel_env_vars
}

# Shared Supabase project — reference/outputs only.
module "supabase" {
  source = "./modules/supabase"

  project_id = var.supabase_project_id
}

output "deployment_state_key" {
  description = "Per-app state key (must be unique; used by workflows/backends)"
  value       = var.deployment_state_key
}

output "approved_hostname" {
  description = "Explicitly approved hostname for this app"
  value       = local.approved_hostname
}

output "vercel_project_url" {
  description = "The URL of the Vercel project"
  value       = module.vercel.project_url
}

output "vercel_domain_url" {
  description = "The custom domain URL"
  value       = module.vercel.domain_url
}

output "supabase_project_url" {
  description = "The URL of the shared Supabase project (reference)"
  value       = module.supabase.project_url
}

output "supabase_api_url" {
  description = "The API URL of the shared Supabase project (reference)"
  value       = module.supabase.api_url
}

output "dns_enabled" {
  description = "Whether DNS management is enabled for this app"
  value       = local.dns_enabled
}

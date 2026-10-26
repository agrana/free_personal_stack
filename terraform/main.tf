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

  # Prefer modern publishable input when nonempty; legacy anon as fallback.
  # Modern sb_publishable_* and legacy anon JWT may differ for the same project.
  # Never synthesize public env vars from privileged service-role material.
  supabase_publishable_trimmed = trimspace(var.supabase_publishable_key)
  supabase_anon_trimmed        = trimspace(var.supabase_anon_key)
  public_supabase_key = (
    local.supabase_publishable_trimmed != ""
    ? local.supabase_publishable_trimmed
    : local.supabase_anon_trimmed
  )

  # Hard public-key screening (JWT role decode is type screening, not crypto verify).
  supabase_anon_jwt_parts = split(".", local.supabase_anon_trimmed)
  supabase_anon_is_jwt = (
    length(local.supabase_anon_jwt_parts) == 3 &&
    startswith(local.supabase_anon_trimmed, "eyJ")
  )
  supabase_anon_payload_b64 = local.supabase_anon_is_jwt ? local.supabase_anon_jwt_parts[1] : ""
  supabase_anon_payload_std = replace(replace(local.supabase_anon_payload_b64, "-", "+"), "_", "/")
  supabase_anon_pad         = (4 - length(local.supabase_anon_payload_std) % 4) % 4
  supabase_anon_payload_padded = (
    local.supabase_anon_pad == 0 ? local.supabase_anon_payload_std : (
      local.supabase_anon_pad == 1 ? "${local.supabase_anon_payload_std}=" : (
        local.supabase_anon_pad == 2 ? "${local.supabase_anon_payload_std}==" : "${local.supabase_anon_payload_std}==="
      )
    )
  )
  supabase_anon_payload = try(jsondecode(base64decode(local.supabase_anon_payload_padded)), tomap({}))
  supabase_anon_role    = try(tostring(local.supabase_anon_payload["role"]), "")

  supabase_publishable_jwt_parts = split(".", local.supabase_publishable_trimmed)
  supabase_publishable_is_jwt = (
    length(local.supabase_publishable_jwt_parts) == 3 &&
    startswith(local.supabase_publishable_trimmed, "eyJ")
  )
  supabase_publishable_payload_b64 = local.supabase_publishable_is_jwt ? local.supabase_publishable_jwt_parts[1] : ""
  supabase_publishable_payload_std = replace(replace(local.supabase_publishable_payload_b64, "-", "+"), "_", "/")
  supabase_publishable_pad         = (4 - length(local.supabase_publishable_payload_std) % 4) % 4
  supabase_publishable_payload_padded = (
    local.supabase_publishable_pad == 0 ? local.supabase_publishable_payload_std : (
      local.supabase_publishable_pad == 1 ? "${local.supabase_publishable_payload_std}=" : (
        local.supabase_publishable_pad == 2 ? "${local.supabase_publishable_payload_std}==" : "${local.supabase_publishable_payload_std}==="
      )
    )
  )
  supabase_publishable_payload = try(jsondecode(base64decode(local.supabase_publishable_payload_padded)), tomap({}))
  supabase_publishable_role    = try(tostring(local.supabase_publishable_payload["role"]), "")

  supabase_publishable_ok = (
    local.supabase_publishable_trimmed == "" ||
    can(regex("^sb_publishable_[A-Za-z0-9_-]{16,}$", local.supabase_publishable_trimmed)) ||
    (local.supabase_publishable_is_jwt && local.supabase_publishable_role == "anon")
  )
  supabase_anon_ok = (
    local.supabase_anon_trimmed == "" ||
    can(regex("^sb_publishable_[A-Za-z0-9_-]{16,}$", local.supabase_anon_trimmed)) ||
    (local.supabase_anon_is_jwt && local.supabase_anon_role == "anon")
  )
  supabase_public_keys_present = (
    local.supabase_publishable_trimmed != "" || local.supabase_anon_trimmed != ""
  )
  supabase_public_not_service = (
    local.supabase_publishable_trimmed != var.supabase_service_role_key &&
    local.supabase_anon_trimmed != var.supabase_service_role_key &&
    local.public_supabase_key != var.supabase_service_role_key
  )
  # Reject privileged JWT/role even when unequal to the supplied service_role value.
  supabase_public_not_privileged_role = (
    local.supabase_publishable_role != "service_role" &&
    local.supabase_publishable_role != "supabase_admin" &&
    local.supabase_anon_role != "service_role" &&
    local.supabase_anon_role != "supabase_admin" &&
    !startswith(local.supabase_publishable_trimmed, "sb_secret_") &&
    !startswith(local.supabase_anon_trimmed, "sb_secret_")
  )
  supabase_public_keys_safe = (
    local.supabase_public_keys_present &&
    local.supabase_publishable_ok &&
    local.supabase_anon_ok &&
    local.supabase_public_not_service &&
    local.supabase_public_not_privileged_role
  )

  # Metadata-only boundary: whether a modern publishable input was supplied is not a
  # secret value. Used solely to select allowlisted env KEY names for for_each.
  # Never apply nonsensitive() to secret VALUES or to the full environment map.
  supabase_publishable_key_present = nonsensitive(local.supabase_publishable_trimmed != "")

  # Stable, config-controlled KEY names only (never secret material). for_each must
  # iterate this set — keys(local.vercel_env_vars) is sensitive when values are.
  vercel_env_var_keys = toset(
    concat(
      [
        "NEXT_PUBLIC_SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "NEXT_PUBLIC_SITE_URL",
      ],
      local.supabase_publishable_key_present ? ["NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"] : []
    )
  )

  vercel_env_vars_base = {
    NEXT_PUBLIC_SUPABASE_URL = {
      value  = var.supabase_url
      target = ["production", "preview", "development"]
    }
    # Auth-helpers / typed contract still consume ANON_KEY; map resolved public key.
    NEXT_PUBLIC_SUPABASE_ANON_KEY = {
      value  = local.public_supabase_key
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

  # Values remain sensitive; indexed by allowlisted keys above — do not nonsensitive().
  vercel_env_vars = merge(
    local.vercel_env_vars_base,
    local.supabase_publishable_key_present ? {
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = {
        value  = local.supabase_publishable_trimmed
        target = ["production", "preview", "development"]
      }
    } : {}
  )
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

  project_name              = var.vercel_project_name
  domain_name               = local.approved_hostname
  github_repo               = local.github_repo
  team_id                   = var.vercel_team_id
  environment_variable_keys = local.vercel_env_var_keys
  environment_variables     = local.vercel_env_vars
  supabase_public_keys_safe = local.supabase_public_keys_safe
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

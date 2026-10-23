variable "approved_hostname" {
  description = "Explicitly approved FQDN for this app only (APP-002). Required; no shared root/www/api defaults."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.approved_hostname)) && length(var.approved_hostname) <= 253
    error_message = "approved_hostname must be a lowercase FQDN without URL, path, port, or wildcard."
  }
}

# Compatibility alias used by older docs; prefer approved_hostname.
variable "domain_name" {
  description = "Deprecated alias for approved_hostname. Leave empty to use approved_hostname."
  type        = string
  default     = ""
}

variable "app_url" {
  description = "Vercel app URL (e.g., myproject.vercel.app). Required when enable_dns is true."
  type        = string
  default     = ""
}

variable "enable_dns" {
  description = "Manage DNS for approved_hostname. Default false (fail closed). Requires dns_provider=cloudflare."
  type        = bool
  default     = false
}

variable "dns_provider" {
  description = <<-EOT
    DNS provider selection matching app.config dns.choice:
    - unresolved: fail closed if enable_dns=true
    - cloudflare: only supported DNS implementation in this template
    - cloudfront: NOT implemented here; enable_dns must stay false until a later CloudFront slice
  EOT
  type        = string
  default     = "unresolved"

  validation {
    condition     = contains(["unresolved", "cloudflare", "cloudfront"], var.dns_provider)
    error_message = "dns_provider must be unresolved, cloudflare, or cloudfront."
  }
}

variable "support_email_destination" {
  description = "Unused unless enable_email_routing=true (default false; shared mail routing not owned by apps)."
  type        = string
  default     = ""
}

variable "contact_email_destination" {
  description = "Unused unless enable_email_routing=true (default false; shared mail routing not owned by apps)."
  type        = string
  default     = ""
}

variable "enable_email_routing" {
  description = "Enable Cloudflare email routing. Default false — apps do not own shared mail routing (APP-002)."
  type        = bool
  default     = false
}

variable "manage_email_routing_settings" {
  description = "Manage email routing settings. Kept for compatibility; email routing stays disabled by default."
  type        = bool
  default     = false
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token (required only when enable_dns=true with dns_provider=cloudflare)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (optional). Apps do not create or own the shared DNS zone."
  type        = string
  default     = ""
  nullable    = true
}

variable "create_cloudflare_zone" {
  description = "MUST remain false. Template apps must not create or own the shared DNS zone (APP-002)."
  type        = bool
  default     = false

  validation {
    condition     = var.create_cloudflare_zone == false
    error_message = "create_cloudflare_zone must be false; shared zone ownership is forbidden (APP-002)."
  }
}

variable "cloudflare_zone_name" {
  description = "Existing Cloudflare zone name to look up when enable_dns=true (data source only; never created)."
  type        = string
  default     = ""
}

variable "vercel_api_token" {
  description = "Vercel API token (get from https://vercel.com/account/tokens)"
  type        = string
  sensitive   = true
}

variable "vercel_team_id" {
  description = "Vercel team ID (optional, leave empty for personal account)"
  type        = string
  default     = ""
}

variable "vercel_project_name" {
  description = "Distinct per-app Vercel project name. REQUIRED — must match app.config deployment.identity. No shared default."
  type        = string

  validation {
    condition     = length(var.vercel_project_name) > 0 && can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.vercel_project_name))
    error_message = "vercel_project_name is required and must be a safe lowercase slug matching deployment.identity."
  }
}

variable "github_repo" {
  description = "GitHub repository in format 'owner/repo'"
  type        = string
  default     = ""
}

variable "deployment_state_key" {
  description = <<-EOT
    Per-app isolated Terraform/state key. REQUIRED. Must match app.config deployment.stateKey.
    Do not reuse another app's key — shared keys can clobber state (APP-002).
  EOT
  type        = string

  validation {
    condition     = length(var.deployment_state_key) > 0 && can(regex("^[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$", var.deployment_state_key))
    error_message = "deployment_state_key is required; supply a unique per-app key."
  }
}

variable "supabase_url" {
  description = "Shared Supabase project URL (reference / env wiring only)"
  type        = string
  sensitive   = true
}

variable "supabase_anon_key" {
  description = "Supabase anonymous key"
  type        = string
  sensitive   = true
}

variable "supabase_service_role_key" {
  description = "Supabase service role key"
  type        = string
  sensitive   = true
}

variable "google_client_id" {
  description = "Google OAuth client ID"
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_client_secret" {
  description = "Google OAuth client secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "nextauth_secret" {
  description = "NextAuth secret key"
  type        = string
  sensitive   = true
  default     = ""
  nullable    = true
}

variable "supabase_access_token" {
  description = "Supabase access token (optional for reference-only module; not used for project deletion)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "supabase_project_id" {
  description = "Existing shared Supabase project ID (reference only)"
  type        = string
}

variable "enable_supabase_project_deletion" {
  description = "REMOVED capability (APP-002). Must remain false — shared project must never be deleted by this template."
  type        = bool
  default     = false

  validation {
    condition     = var.enable_supabase_project_deletion == false
    error_message = "enable_supabase_project_deletion must be false; shared Supabase project deletion is forbidden (APP-002)."
  }
}

variable "site_url" {
  description = "Production site URL (defaults to https://{approved_hostname})"
  type        = string
  default     = ""
}

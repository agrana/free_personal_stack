variable "domain_name" {
  description = "The domain name for your project (e.g., myproject.com)"
  type        = string
}

variable "app_url" {
  description = "The URL where your app is hosted (e.g., myproject.vercel.app). If empty, inferred from vercel_project_name"
  type        = string
  default     = ""
}

variable "support_email_destination" {
  description = "Real email address where support@domain emails should be forwarded (e.g., your-email@gmail.com)"
  type        = string
  default     = ""
}

variable "contact_email_destination" {
  description = "Real email address where contact@domain and hello@domain emails should be forwarded (e.g., your-email@gmail.com)"
  type        = string
  default     = ""
}

variable "enable_email_routing" {
  description = "Enable Cloudflare email routing module (manages routing rules and optionally settings)"
  type        = bool
  default     = true
}

variable "manage_email_routing_settings" {
  description = "Manage email routing settings (enabling/disabling). Requires account-level API token permissions. Set to false if you only have zone-level permissions (routing rules can still be managed)."
  type        = bool
  default     = false
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with all required permissions (Zone:Zone:Read, Zone:DNS:Edit, Zone:Email Routing:Read, Zone:Email Routing:Edit)"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID - required for some accounts. Find it in Cloudflare Dashboard → Account Settings, or in the URL when viewing your account. Leave empty for personal accounts (may not work for all account types)"
  type        = string
  default     = ""
  nullable    = true
}

variable "create_cloudflare_zone" {
  description = "Create a new Cloudflare zone if it doesn't exist. Set to false if the zone already exists in your Cloudflare account."
  type        = bool
  default     = false
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
  description = "Name of the Vercel project (if empty, inferred from domain_name by removing dots and special chars)"
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository in format 'owner/repo'. In GitHub Actions, can be inferred from github.repository context"
  type        = string
  default     = ""
}

# Supabase Configuration
variable "supabase_url" {
  description = "Supabase project URL"
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

# Google OAuth Configuration
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

# NextAuth Configuration
variable "nextauth_secret" {
  description = "NextAuth secret key"
  type        = string
  sensitive   = true
}

# Supabase Configuration
variable "supabase_access_token" {
  description = "Supabase access token (get from https://supabase.com/dashboard/account/tokens)"
  type        = string
  sensitive   = true
}

variable "supabase_project_id" {
  description = "Supabase project ID"
  type        = string
}

variable "enable_supabase_project_deletion" {
  description = <<-EOT
    Enable automatic Supabase project deletion via CLI when running terraform destroy.
    Requires Supabase CLI installed (npm install -g supabase or via npx) and supabase_access_token set.
    
    ⚠️  WARNING: When enabled (default: true), running terraform destroy will:
    - Delete your Supabase project permanently
    - Delete all data in the project
    - This action CANNOT be undone!
    
    Set to false if you want to manage Supabase project deletion manually.
  EOT
  type        = bool
  default     = true
}

variable "site_url" {
  description = "Production site URL (defaults to https://{domain_name})"
  type        = string
  default     = ""
}

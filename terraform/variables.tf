variable "domain_name" {
  description = "The domain name for your project (e.g., myproject.com)"
  type        = string
}

variable "app_url" {
  description = "The URL where your app is hosted (e.g., myproject.vercel.app)"
  type        = string
}

variable "support_email" {
  description = "Email address to forward support emails to"
  type        = string
}

variable "contact_email" {
  description = "Email address to forward contact emails to"
  type        = string
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with all required permissions (Zone:Zone:Read, Zone:DNS:Edit, Zone:Email Routing:Read, Zone:Email Routing:Edit)"
  type        = string
  sensitive   = true
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
  description = "Name of the Vercel project"
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository in format 'owner/repo'"
  type        = string
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
}

variable "google_client_secret" {
  description = "Google OAuth client secret"
  type        = string
  sensitive   = true
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

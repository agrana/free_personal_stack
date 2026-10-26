variable "project_name" {
  description = "Name of the Vercel project"
  type        = string
}

variable "domain_name" {
  description = "Custom domain for the Vercel project"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository in format 'owner/repo'"
  type        = string
}

variable "team_id" {
  description = "Vercel team ID (optional, leave empty for personal account)"
  type        = string
  default     = ""
}

variable "environment_variable_keys" {
  description = <<-EOT
    Allowlisted environment variable NAMES only — used as for_each instance keys.
    Must be a non-sensitive set of stable config-controlled names. Never pass secret
    VALUES here; values are looked up separately from environment_variables.
  EOT
  type        = set(string)

  validation {
    condition = length(setsubtract(var.environment_variable_keys, toset([
      "NEXT_PUBLIC_SUPABASE_URL",
      "NEXT_PUBLIC_SUPABASE_ANON_KEY",
      "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
      "SUPABASE_SERVICE_ROLE_KEY",
      "NEXT_PUBLIC_SITE_URL",
    ]))) == 0
    error_message = "environment_variable_keys may only contain the template allowlisted public/server env NAMES."
  }
}

variable "environment_variables" {
  description = "Environment variable VALUES keyed by allowlisted NAME (may be sensitive; not used as for_each)."
  type = map(object({
    value  = string
    target = list(string)
  }))
  default = {}
}

variable "supabase_public_keys_safe" {
  description = "Hard gate from root: required public Supabase key present, public-class syntax, not privileged."
  type        = bool
}

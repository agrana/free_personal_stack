variable "project_id" {
  description = "The ID of the Supabase project"
  type        = string
}

variable "supabase_access_token" {
  description = "Supabase access token (required for project deletion via CLI). Get from https://supabase.com/dashboard/account/tokens"
  type        = string
  sensitive   = true
  default     = ""
}

variable "enable_project_deletion" {
  description = "Enable automatic project deletion via CLI when running terraform destroy. Requires Supabase CLI and access token."
  type        = bool
  default     = false
}

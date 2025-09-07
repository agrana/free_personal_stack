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

variable "environment_variables" {
  description = "Environment variables for the Vercel project"
  type = map(object({
    value  = string
    target = list(string)
  }))
  default = {}
}

variable "zone_id" {
  description = "Cloudflare Zone ID for the domain"
  type        = string
}

variable "domain_name" {
  description = "The domain name for SavedTube"
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

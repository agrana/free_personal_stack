variable "zone_id" {
  description = "Cloudflare Zone ID for the domain"
  type        = string
}

variable "domain_name" {
  description = "The domain name for SavedTube"
  type        = string
}

variable "support_email_destination" {
  description = "Real email address where support@domain emails should be forwarded (e.g., your-email@gmail.com)"
  type        = string
}

variable "contact_email_destination" {
  description = "Real email address where contact@domain and hello@domain emails should be forwarded (e.g., your-email@gmail.com)"
  type        = string
}

variable "manage_email_routing_settings" {
  description = "Manage email routing settings (enabling/disabling). Requires account-level API token permissions. Set to false if you only have zone-level permissions."
  type        = bool
  default     = true
}

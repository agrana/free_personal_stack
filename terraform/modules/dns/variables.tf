variable "zone_id" {
  description = "Cloudflare Zone ID for the domain"
  type        = string
}

variable "domain_name" {
  description = "The domain name for SavedTube"
  type        = string
}

variable "app_url" {
  description = "The URL where the SavedTube app is hosted"
  type        = string
}

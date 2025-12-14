variable "zone_id" {
  description = "Cloudflare Zone ID for the parent domain"
  type        = string
}

variable "domain_name" {
  description = "The full domain name (can be subdomain)"
  type        = string
}

variable "parent_zone" {
  description = "The parent zone name (e.g., 'example.com' for 'fpt.example.com')"
  type        = string
}

variable "app_url" {
  description = "The URL where the app is hosted"
  type        = string
}

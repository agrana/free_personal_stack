terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# DNS Records
# Extract subdomain name (e.g., "fpt.alfonsograna.com" -> "fpt" if parent is "alfonsograna.com")
locals {
  # Split domain and parent zone into parts
  domain_parts = split(".", var.domain_name)
  parent_parts = split(".", var.parent_zone)

  # Check if domain_name is a subdomain of parent_zone
  is_subdomain = length(local.domain_parts) > length(local.parent_parts)

  # For subdomains: extract the subdomain part (e.g., "fpt" from "fpt.alfonsograna.com")
  # For root domains: use the domain name itself
  record_name = local.is_subdomain ? join(".", slice(local.domain_parts, 0, length(local.domain_parts) - length(local.parent_parts))) : var.domain_name
}

resource "cloudflare_record" "root" {
  zone_id         = var.zone_id
  name            = local.record_name
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "Subdomain pointing to app URL"
}

resource "cloudflare_record" "www" {
  zone_id         = var.zone_id
  name            = "www.${local.record_name}"
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "WWW subdomain pointing to app URL"
}

resource "cloudflare_record" "api" {
  zone_id         = var.zone_id
  name            = "api.${local.record_name}"
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "API subdomain"
}

# Page Rules temporarily disabled due to provider bug
# TODO: Re-enable when Cloudflare provider is fixed

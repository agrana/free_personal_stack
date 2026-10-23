terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# APP-002: only the explicitly approved hostname record.
# No root/www/api defaults. No zone ownership. CloudFront is not implemented here.
locals {
  domain_parts = split(".", var.domain_name)
  parent_parts = split(".", var.parent_zone)
  is_subdomain = length(local.domain_parts) > length(local.parent_parts)
  record_name  = local.is_subdomain ? join(".", slice(local.domain_parts, 0, length(local.domain_parts) - length(local.parent_parts))) : var.domain_name
}

resource "cloudflare_record" "app" {
  zone_id         = var.zone_id
  name            = local.record_name
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "Approved app hostname only (APP-002)"
}

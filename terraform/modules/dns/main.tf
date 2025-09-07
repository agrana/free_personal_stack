terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# DNS Records
resource "cloudflare_record" "root" {
  zone_id         = var.zone_id
  name            = var.domain_name
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "Root domain pointing to app URL"
}

resource "cloudflare_record" "www" {
  zone_id         = var.zone_id
  name            = "www"
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "WWW subdomain pointing to app URL"
}

resource "cloudflare_record" "api" {
  zone_id         = var.zone_id
  name            = "api"
  content         = var.app_url
  type            = "CNAME"
  proxied         = true
  allow_overwrite = false
  comment         = "API subdomain"
}

# Page Rules temporarily disabled due to provider bug
# TODO: Re-enable when Cloudflare provider is fixed

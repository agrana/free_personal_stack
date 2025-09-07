terraform {
  required_version = ">= 1.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    vercel = {
      source  = "vercel/vercel"
      version = "~> 1.0"
    }
    supabase = {
      source  = "supabase/supabase"
      version = "~> 1.0"
    }
  }
}

# Single Cloudflare Provider with all permissions
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# Vercel Provider
provider "vercel" {
  api_token = var.vercel_api_token
}

# Supabase Provider
provider "supabase" {
  access_token = var.supabase_access_token
}

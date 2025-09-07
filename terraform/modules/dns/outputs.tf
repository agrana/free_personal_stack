output "dns_records" {
  description = "Created DNS records"
  value = {
    root = cloudflare_record.root.hostname
    www  = cloudflare_record.www.hostname
    api  = cloudflare_record.api.hostname
  }
}

# Page rules outputs temporarily disabled
# output "page_rules" {
#   description = "Created page rules"
#   value = {
#     cache_static_assets = cloudflare_page_rule.cache_static_assets.id
#     security_headers    = cloudflare_page_rule.security_headers.id
#   }
# }

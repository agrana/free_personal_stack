output "zone_id" {
  description = "Cloudflare Zone ID for the domain"
  value       = data.cloudflare_zone.main.id
}

output "domain_name" {
  description = "The configured domain name"
  value       = local.domain_name
}

output "dns_records" {
  description = "Created DNS records"
  value       = module.dns.dns_records
}

# Page rules outputs temporarily disabled
# output "page_rules" {
#   description = "Created page rules"
#   value       = module.dns.page_rules
# }

output "email_forwarding_rules" {
  description = "Created email forwarding rules"
  value       = module.email_routing.email_forwarding_rules
}

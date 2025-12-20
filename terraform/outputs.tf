output "zone_id" {
  description = "Cloudflare Zone ID for the parent domain"
  value       = var.create_cloudflare_zone ? cloudflare_zone.new[0].id : data.cloudflare_zone.existing[0].id
}

output "zone_created" {
  description = "Whether a new zone was created (true) or existing zone was used (false)"
  value       = var.create_cloudflare_zone
}

output "parent_zone" {
  description = "Parent zone name"
  value       = local.parent_zone
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
  value       = length(module.email_routing) > 0 ? module.email_routing[0].email_forwarding_rules : {}
}

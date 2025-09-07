output "email_forwarding_rules" {
  description = "Created email forwarding rules"
  value = {
    support = "support@${var.domain_name} -> ${var.support_email}"
    contact = "contact@${var.domain_name} -> ${var.contact_email}"
    hello   = "hello@${var.domain_name} -> ${var.contact_email}"
  }
}

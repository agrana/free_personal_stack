output "email_forwarding_rules" {
  description = "Created email forwarding rules"
  value = {
    support = "support@${var.domain_name} -> ${var.support_email_destination}"
    contact = "contact@${var.domain_name} -> ${var.contact_email_destination}"
    hello   = "hello@${var.domain_name} -> ${var.contact_email_destination}"
  }
}

terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# Enable Email Routing for the domain
# Note: This resource requires account-level API token permissions
# If you only have zone-level permissions, set manage_email_routing_settings = false
# The routing rules can still be managed with zone-level permissions
resource "cloudflare_email_routing_settings" "email_routing" {
  count   = var.manage_email_routing_settings ? 1 : 0
  zone_id = var.zone_id
  enabled = true

  lifecycle {
    # Ignore changes to enabled state - settings may be managed outside Terraform
    ignore_changes = [enabled]
  }
}

# Email forwarding rules
resource "cloudflare_email_routing_rule" "support" {
  zone_id = var.zone_id
  name    = "Support emails"
  enabled = true

  matcher {
    type  = "literal"
    field = "to"
    value = "support@${var.domain_name}"
  }

  action {
    type  = "forward"
    value = [var.support_email_destination]
  }
}

resource "cloudflare_email_routing_rule" "contact" {
  zone_id = var.zone_id
  name    = "Contact emails"
  enabled = true

  matcher {
    type  = "literal"
    field = "to"
    value = "contact@${var.domain_name}"
  }

  action {
    type  = "forward"
    value = [var.contact_email_destination]
  }
}

resource "cloudflare_email_routing_rule" "hello" {
  zone_id = var.zone_id
  name    = "Hello emails"
  enabled = true

  matcher {
    type  = "literal"
    field = "to"
    value = "hello@${var.domain_name}"
  }

  action {
    type  = "forward"
    value = [var.contact_email_destination]
  }
}

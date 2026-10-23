output "dns_records" {
  description = "Approved hostname DNS record only (APP-002)"
  value = {
    app = cloudflare_record.app.hostname
  }
}

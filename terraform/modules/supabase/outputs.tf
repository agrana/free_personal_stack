output "project_id" {
  description = "The ID of the shared Supabase project (reference only)"
  value       = var.project_id
}

output "project_url" {
  description = "The URL of the shared Supabase project"
  value       = "https://${var.project_id}.supabase.co"
}

output "api_url" {
  description = "The API URL of the shared Supabase project"
  value       = "https://${var.project_id}.supabase.co/rest/v1/"
}

output "project_lifecycle_note" {
  description = "Lifecycle policy for the shared project"
  value       = "APP-002: shared Supabase project is reference-only; create/delete/reset hooks removed."
}

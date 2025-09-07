output "project_id" {
  description = "The ID of the Supabase project"
  value       = var.project_id
}

output "project_url" {
  description = "The URL of the Supabase project"
  value       = "https://${var.project_id}.supabase.co"
}

output "api_url" {
  description = "The API URL of the Supabase project"
  value       = "https://${var.project_id}.supabase.co/rest/v1/"
}

output "database_url" {
  description = "The database URL"
  value       = "postgresql://postgres:[password]@db.${var.project_id}.supabase.co:5432/postgres"
}

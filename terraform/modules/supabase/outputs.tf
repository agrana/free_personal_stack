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

output "project_deletion_enabled" {
  description = "Whether automatic project deletion is enabled"
  value       = var.enable_project_deletion
}

output "project_deletion_note" {
  description = "Note about project deletion"
  value = var.enable_project_deletion ? (
    "Automatic project deletion enabled. Running terraform destroy will delete the Supabase project."
  ) : (
    "Automatic project deletion disabled. To enable, set enable_supabase_project_deletion = true in terraform.tfvars. You'll need Supabase CLI installed and supabase_access_token set."
  )
}

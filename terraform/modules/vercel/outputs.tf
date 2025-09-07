output "project_id" {
  description = "The ID of the Vercel project"
  value       = data.vercel_project.main.id
}

output "project_url" {
  description = "The URL of the Vercel project"
  value       = "https://${data.vercel_project.main.name}.vercel.app"
}

output "domain_url" {
  description = "The custom domain URL"
  value       = "https://${vercel_project_domain.main.domain}"
}

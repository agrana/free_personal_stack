terraform {
  required_providers {
    supabase = {
      source  = "supabase/supabase"
      version = "~> 1.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}

# TODO: Explore using Supabase CLI to create projects automatically via Terraform
# Currently, projects must be created manually in the Supabase Dashboard because:
# 1. The Supabase Terraform provider doesn't support project creation/deletion
# 2. API keys cannot be retrieved programmatically (security restriction)
# 
# Future enhancement: Use null_resource with Supabase CLI to:
# - Create projects: `supabase projects create --name ... --org-id ...`
# - Delete projects: `supabase projects delete --project-ref ...`
# This would allow full infrastructure lifecycle management via Terraform.

# Resource to handle Supabase project deletion via CLI
# This allows `terraform destroy` to clean up the Supabase project
resource "null_resource" "delete_supabase_project" {
  count = var.enable_project_deletion && var.project_id != "" ? 1 : 0

  # This runs on destroy
  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      # Check if Supabase CLI is installed
      if ! command -v supabase &> /dev/null && ! command -v npx &> /dev/null; then
        echo "Warning: Supabase CLI not found. Install it with: npm install -g supabase"
        echo "Project ${self.triggers.project_id} must be deleted manually from the dashboard."
        echo "Dashboard URL: https://supabase.com/dashboard/project/${self.triggers.project_id}/settings/general"
        exit 0
      fi

      # Try to delete project via CLI
      export SUPABASE_ACCESS_TOKEN="${self.triggers.access_token}"
      
      if command -v supabase &> /dev/null; then
        supabase projects delete --project-ref "${self.triggers.project_id}" --non-interactive || true
      elif command -v npx &> /dev/null; then
        npx supabase projects delete --project-ref "${self.triggers.project_id}" --non-interactive || true
      fi
      
      echo "Supabase project deletion attempted. Check dashboard if needed: https://supabase.com/dashboard/project/${self.triggers.project_id}/settings/general"
    EOT

    on_failure = continue  # Don't fail destroy if CLI deletion fails
  }

  triggers = {
    project_id    = var.project_id
    access_token  = var.supabase_access_token
  }
}

# Since we're managing an existing project, we'll use a data source approach
# The Supabase provider doesn't have a direct data source for projects,
# so we'll create a simple module that can be extended later

# For now, we'll just output the project information
# This can be extended to manage specific Supabase resources as needed

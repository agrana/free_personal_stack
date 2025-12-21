# Supabase Module

This module manages Supabase project resources.

## Current Limitations

**Project Creation:**
- Projects must be created manually in the Supabase Dashboard
- The Supabase Terraform provider doesn't support project creation
- API keys cannot be retrieved programmatically (security restriction)

**Project Deletion:**
- Can be automated via Supabase CLI (optional, requires `enable_project_deletion = true`)
- Uses `null_resource` with `local-exec` provisioner to call Supabase CLI on destroy
- Falls back gracefully if CLI is not installed

## Future Enhancements

**TODO: Explore using Supabase CLI for full lifecycle management:**

1. **Project Creation:**
   ```bash
   supabase projects create --name "project-name" --org-id "org-id" --db-password "password"
   ```
   - Would allow `terraform apply` to create projects
   - Challenge: API keys still need manual retrieval

2. **Project Deletion:**
   ```bash
   supabase projects delete --project-ref "project-id" --non-interactive
   ```
   - Already implemented via `null_resource` when `enable_project_deletion = true`

3. **Project Information:**
   - Could use `supabase projects list` to fetch project details
   - Could potentially extract some metadata

## Usage

### Basic (Manual Project Management)

```hcl
module "supabase" {
  source = "./modules/supabase"
  
  project_id = "your-project-id"
}
```

### With Automatic Deletion

```hcl
module "supabase" {
  source = "./modules/supabase"
  
  project_id              = "your-project-id"
  supabase_access_token   = var.supabase_access_token
  enable_project_deletion = true  # WARNING: terraform destroy will delete the project!
}
```

**Requirements for automatic deletion:**
- Supabase CLI installed (`npm install -g supabase` or via `npx`)
- `supabase_access_token` must be set
- `enable_project_deletion = true` in module call

## Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `project_id` | Supabase project ID | Yes | - |
| `supabase_access_token` | Access token for CLI operations | No | `""` |
| `enable_project_deletion` | Enable automatic deletion on destroy | No | `false` |

## Outputs

| Output | Description |
|--------|-------------|
| `project_id` | The Supabase project ID |
| `project_url` | The project URL |
| `api_url` | The REST API URL |
| `database_url` | Database connection URL template |
| `project_deletion_enabled` | Whether deletion is enabled |
| `project_deletion_note` | Note about deletion status |

## Important Notes

⚠️ **WARNING:** If `enable_project_deletion = true`, running `terraform destroy` will:
- Delete your Supabase project
- Delete all data in the project
- This action cannot be undone

Make sure you have backups before enabling this feature!


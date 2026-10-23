# Supabase Module (reference-only)

APP-002: the template targets a **single existing shared** Supabase project.

- No project create, delete, reset, or destroy hooks
- No `null_resource` / `local-exec` provisioners
- No secret-bearing triggers
- Outputs expose the configured `project_id` for wiring only

Per-app isolation is via `database.namespace` (APP-001 / APP-003 migrations), not project lifecycle.

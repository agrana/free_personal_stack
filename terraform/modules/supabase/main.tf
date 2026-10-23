terraform {
  required_providers {
    supabase = {
      source  = "supabase/supabase"
      version = "~> 1.0"
    }
  }
}

# APP-002: shared Supabase project is reference-only.
# No create/delete/reset hooks and no secret-bearing triggers.
# Per-app isolation is via database.namespace (APP-001 / APP-003), not project lifecycle.

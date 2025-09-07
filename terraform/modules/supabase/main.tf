terraform {
  required_providers {
    supabase = {
      source  = "supabase/supabase"
      version = "~> 1.0"
    }
  }
}

# Since we're managing an existing project, we'll use a data source approach
# The Supabase provider doesn't have a direct data source for projects,
# so we'll create a simple module that can be extended later

# For now, we'll just output the project information
# This can be extended to manage specific Supabase resources as needed

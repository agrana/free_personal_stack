-- Disposable local test bootstrap ONLY.
-- Establishes a compatible public CREATE baseline and Supabase-like roles for
-- podman Postgres. Never apply this against a live shared project; live sites
-- must already satisfy the operator prerequisites checked by reusable
-- provisioning (01_control_and_roles.sql).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticator') THEN
    CREATE ROLE authenticator NOLOGIN;
  END IF;
END
$$;

-- Test-only membership wiring. Reusable provisioning must not alter these.
GRANT anon TO authenticator;
GRANT authenticated TO authenticator;
GRANT service_role TO authenticator;

-- Compatible baseline required by provision_app (fail-closed if missing).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Per-app protected control objects.
-- Placeholders __CONTROL_SCHEMA__ and __DATA_SCHEMA__ are substituted by the
-- library before execution. Owned by the bootstrap provisioner login.
--
-- ENABLE+FORCE RLS on every control table. SECURITY DEFINER helpers authorize
-- from session_user against ownership_binding and use current_user policies so
-- FORCE RLS still allows the function owner to write the ledger.

CREATE TABLE __CONTROL_SCHEMA__.ownership_binding (
  namespace text PRIMARY KEY,
  repository_id text NOT NULL,
  project_ref text NOT NULL,
  owner_role text NOT NULL,
  migrator_role text NOT NULL UNIQUE,
  runtime_role text NOT NULL UNIQUE,
  provisioner_role text NOT NULL,
  bound_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ownership_namespace_format CHECK (
    namespace ~ '^[a-z][a-z0-9_]{0,53}$'
  ),
  CONSTRAINT ownership_repository_format CHECK (
    repository_id ~ '^[1-9][0-9]{0,18}$'
  ),
  CONSTRAINT ownership_project_format CHECK (
    project_ref ~ '^[a-z0-9][a-z0-9_-]{0,62}$'
  )
);

REVOKE ALL ON TABLE __CONTROL_SCHEMA__.ownership_binding FROM PUBLIC;
ALTER TABLE __CONTROL_SCHEMA__.ownership_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE __CONTROL_SCHEMA__.ownership_binding FORCE ROW LEVEL SECURITY;

CREATE TABLE __CONTROL_SCHEMA__.ledger (
  migration_id text PRIMARY KEY,
  checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ledger_migration_id_format CHECK (
    migration_id ~ '^[0-9]{4}_[a-z0-9_]+$'
  )
);

REVOKE ALL ON TABLE __CONTROL_SCHEMA__.ledger FROM PUBLIC;
ALTER TABLE __CONTROL_SCHEMA__.ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE __CONTROL_SCHEMA__.ledger FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION __CONTROL_SCHEMA__.migrator_is_registered()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = __CONTROL_SCHEMA__, pg_temp
AS $fn$
  SELECT EXISTS (
    SELECT 1
    FROM __CONTROL_SCHEMA__.ownership_binding b
    WHERE b.migrator_role = session_user
      AND b.namespace = '__DATA_SCHEMA__'
  );
$fn$;

REVOKE ALL ON FUNCTION __CONTROL_SCHEMA__.migrator_is_registered() FROM PUBLIC;

CREATE OR REPLACE FUNCTION __CONTROL_SCHEMA__.record_applied(
  p_migration_id text,
  p_checksum text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = __CONTROL_SCHEMA__, pg_temp
AS $fn$
DECLARE
  expected_migrator text;
BEGIN
  SELECT b.migrator_role
    INTO expected_migrator
  FROM __CONTROL_SCHEMA__.ownership_binding b
  WHERE b.namespace = '__DATA_SCHEMA__';

  IF expected_migrator IS NULL OR session_user IS DISTINCT FROM expected_migrator THEN
    RAISE EXCEPTION 'managed_database:AUTH_DENIED';
  END IF;

  IF p_migration_id IS NULL OR p_migration_id !~ '^[0-9]{4}_[a-z0-9_]+$' THEN
    RAISE EXCEPTION 'managed_database:MIGRATION_ID_INVALID';
  END IF;
  IF p_checksum IS NULL OR p_checksum !~ '^[a-f0-9]{64}$' THEN
    RAISE EXCEPTION 'managed_database:MIGRATION_CHECKSUM';
  END IF;

  INSERT INTO __CONTROL_SCHEMA__.ledger (migration_id, checksum_sha256)
  VALUES (p_migration_id, p_checksum);
END;
$fn$;

REVOKE ALL ON FUNCTION __CONTROL_SCHEMA__.record_applied(text, text) FROM PUBLIC;

-- Row policies: never authorize from a caller-settable namespace GUC alone.
CREATE POLICY ownership_binding_owner_all
  ON __CONTROL_SCHEMA__.ownership_binding
  FOR ALL
  USING (
    current_user = provisioner_role
    OR session_user = provisioner_role
  )
  WITH CHECK (
    current_user = provisioner_role
    OR session_user = provisioner_role
  );

CREATE POLICY ownership_binding_migrator_select
  ON __CONTROL_SCHEMA__.ownership_binding
  FOR SELECT
  USING (session_user = migrator_role);

CREATE POLICY ledger_owner_all
  ON __CONTROL_SCHEMA__.ledger
  FOR ALL
  USING (
    current_user = (
      SELECT b.provisioner_role
      FROM __CONTROL_SCHEMA__.ownership_binding b
      WHERE b.namespace = '__DATA_SCHEMA__'
    )
  )
  WITH CHECK (
    current_user = (
      SELECT b.provisioner_role
      FROM __CONTROL_SCHEMA__.ownership_binding b
      WHERE b.namespace = '__DATA_SCHEMA__'
    )
  );

CREATE POLICY ledger_migrator_select
  ON __CONTROL_SCHEMA__.ledger
  FOR SELECT
  USING (__CONTROL_SCHEMA__.migrator_is_registered());

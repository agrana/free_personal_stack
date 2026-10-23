-- Trusted reviewed provisioning for APP-003 (not an app migration).
-- Run as bootstrap/superuser only. Separated from app migrator DDL on purpose:
-- arbitrary SQL cannot be made safe by regex; privilege boundaries are the control.
--
-- Does NOT: REVOKE CREATE ON public FROM PUBLIC, or alter authenticator role
-- memberships. Those are operator prerequisites / disposable-test baseline only.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_migration_owner') THEN
    CREATE ROLE app_migration_owner NOLOGIN BYPASSRLS;
  END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS app_migration_control AUTHORIZATION app_migration_owner;
REVOKE ALL ON SCHEMA app_migration_control FROM PUBLIC;
GRANT USAGE ON SCHEMA app_migration_control TO app_migration_owner;

CREATE TABLE IF NOT EXISTS app_migration_control.registered_apps (
  app_namespace text PRIMARY KEY,
  owner_role text NOT NULL,
  migrator_role text NOT NULL UNIQUE,
  runtime_role text NOT NULL UNIQUE,
  registered_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT registered_namespace_format CHECK (
    app_namespace ~ '^[a-z][a-z0-9_]{0,53}$'
  )
);

ALTER TABLE app_migration_control.registered_apps OWNER TO app_migration_owner;
REVOKE ALL ON TABLE app_migration_control.registered_apps FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app_migration_control.realtime_allowlist (
  app_namespace text NOT NULL
    REFERENCES app_migration_control.registered_apps (app_namespace),
  table_name text NOT NULL,
  PRIMARY KEY (app_namespace, table_name),
  CONSTRAINT realtime_table_format CHECK (table_name ~ '^[a-z][a-z0-9_]{0,62}$')
);

ALTER TABLE app_migration_control.realtime_allowlist OWNER TO app_migration_owner;
REVOKE ALL ON TABLE app_migration_control.realtime_allowlist FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app_migration_control.ledger (
  app_namespace text NOT NULL,
  migration_id text NOT NULL,
  checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (app_namespace, migration_id),
  CONSTRAINT migration_id_format CHECK (migration_id ~ '^[0-9]{4}_[a-z0-9_]+$')
);

ALTER TABLE app_migration_control.ledger OWNER TO app_migration_owner;
REVOKE ALL ON TABLE app_migration_control.ledger FROM PUBLIC;
ALTER TABLE app_migration_control.ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_migration_control.ledger FORCE ROW LEVEL SECURITY;

-- SECURITY DEFINER so RLS can check registration without granting SELECT on
-- registered_apps to migrators.
CREATE OR REPLACE FUNCTION app_migration_control.migrator_owns_namespace(
  p_namespace text
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = app_migration_control, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM app_migration_control.registered_apps r
    WHERE r.app_namespace = p_namespace
      AND r.migrator_role = session_user
  );
$$;

ALTER FUNCTION app_migration_control.migrator_owns_namespace(text)
  OWNER TO app_migration_owner;
REVOKE ALL ON FUNCTION app_migration_control.migrator_owns_namespace(text) FROM PUBLIC;

-- Bind SELECT to trusted login + registration — never user-settable GUC alone.
DROP POLICY IF EXISTS ledger_select_own ON app_migration_control.ledger;
CREATE POLICY ledger_select_own ON app_migration_control.ledger
  FOR SELECT
  USING (
    session_user = (app_namespace || '_migrator')
    AND app_migration_control.migrator_owns_namespace(app_namespace)
  );

CREATE OR REPLACE FUNCTION app_migration_control.record_applied(
  p_namespace text,
  p_migration_id text,
  p_checksum text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app_migration_control, pg_temp
AS $$
BEGIN
  IF session_user IS DISTINCT FROM (p_namespace || '_migrator') THEN
    RAISE EXCEPTION 'only the app migrator login role may record migrations';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM app_migration_control.registered_apps r
    WHERE r.app_namespace = p_namespace
      AND r.migrator_role = session_user
  ) THEN
    RAISE EXCEPTION 'namespace is not registered for this migrator';
  END IF;
  INSERT INTO app_migration_control.ledger (app_namespace, migration_id, checksum_sha256)
  VALUES (p_namespace, p_migration_id, p_checksum);
END;
$$;

ALTER FUNCTION app_migration_control.record_applied(text, text, text) OWNER TO app_migration_owner;
REVOKE ALL ON FUNCTION app_migration_control.record_applied(text, text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION app_migration_control.provision_app(
  p_namespace text,
  p_migrator_password text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app_migration_control, pg_temp
AS $$
DECLARE
  owner_role text := p_namespace || '_owner';
  migrator_role text := p_namespace || '_migrator';
  runtime_role text := p_namespace || '_runtime';
  existing_owner name;
  existing_reg record;
BEGIN
  IF has_schema_privilege('public', 'public', 'CREATE') THEN
    RAISE EXCEPTION
      'operator prerequisite: PUBLIC must not have CREATE on schema public '
      'before APP-003 provisioning; reusable provisioning will not alter '
      'shared public grants';
  END IF;

  -- NAMEDATALEN-1 = 63; longest suffix is '_migrator' (9) => max namespace 54.
  IF p_namespace !~ '^[a-z][a-z0-9_]{0,53}$' OR length(p_namespace) > 54 THEN
    RAISE EXCEPTION
      'invalid namespace (max 54 chars so role names fit in 63-byte identifiers)';
  END IF;
  IF p_namespace = ANY (ARRAY[
    'public', 'shared', 'auth', 'extensions', 'graphql_public', 'realtime',
    'storage', 'supabase_functions', 'supabase_migrations', 'information_schema',
    'app_migration_control', 'pg_catalog'
  ]) OR p_namespace LIKE 'pg_%' THEN
    RAISE EXCEPTION 'reserved namespace';
  END IF;
  IF length(owner_role) > 63 OR length(migrator_role) > 63 OR length(runtime_role) > 63 THEN
    RAISE EXCEPTION 'derived role name exceeds PostgreSQL identifier limit';
  END IF;

  SELECT * INTO existing_reg
  FROM app_migration_control.registered_apps
  WHERE app_namespace = p_namespace;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = owner_role) THEN
    IF existing_reg IS NULL OR existing_reg.owner_role IS DISTINCT FROM owner_role THEN
      RAISE EXCEPTION
        'role collision: % exists and is not registered for namespace %',
        owner_role, p_namespace;
    END IF;
  ELSE
    EXECUTE format('CREATE ROLE %I NOLOGIN', owner_role);
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = migrator_role) THEN
    IF existing_reg IS NULL OR existing_reg.migrator_role IS DISTINCT FROM migrator_role THEN
      RAISE EXCEPTION
        'role collision: % exists and is not registered for namespace % '
        '(refusing to reset an unknown role password)',
        migrator_role, p_namespace;
    END IF;
    EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', migrator_role, p_migrator_password);
  ELSE
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', migrator_role, p_migrator_password);
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
    IF existing_reg IS NULL OR existing_reg.runtime_role IS DISTINCT FROM runtime_role THEN
      RAISE EXCEPTION
        'role collision: % exists and is not registered for namespace %',
        runtime_role, p_namespace;
    END IF;
  ELSE
    EXECUTE format('CREATE ROLE %I NOLOGIN', runtime_role);
  END IF;

  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = p_namespace) THEN
    SELECT r.rolname INTO existing_owner
    FROM pg_namespace n
    JOIN pg_roles r ON r.oid = n.nspowner
    WHERE n.nspname = p_namespace;
    IF existing_owner IS DISTINCT FROM owner_role THEN
      RAISE EXCEPTION
        'schema ownership collision: % owned by % (expected %)',
        p_namespace, existing_owner, owner_role;
    END IF;
  ELSE
    EXECUTE format('CREATE SCHEMA %I AUTHORIZATION %I', p_namespace, owner_role);
  END IF;

  EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', p_namespace);
  EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', p_namespace, migrator_role);
  EXECUTE format(
    'GRANT USAGE ON SCHEMA %I TO %I, authenticated, anon, service_role',
    p_namespace,
    runtime_role
  );
  EXECUTE format('GRANT %I TO %I', owner_role, migrator_role);
  -- Migration-created tables are owned by the migrator; publication changes run as
  -- app_migration_owner via SECURITY DEFINER and need membership in that role.
  EXECUTE format('GRANT %I TO app_migration_owner', migrator_role);
  EXECUTE format('ALTER ROLE %I SET search_path TO %I, pg_temp', migrator_role, p_namespace);

  EXECUTE format('GRANT USAGE ON SCHEMA app_migration_control TO %I', migrator_role);
  EXECUTE format(
    'GRANT SELECT ON TABLE app_migration_control.ledger TO %I',
    migrator_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION app_migration_control.migrator_owns_namespace(text) TO %I',
    migrator_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION app_migration_control.record_applied(text, text, text) TO %I',
    migrator_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION app_migration_control.add_to_realtime(text, text) TO %I',
    migrator_role
  );

  -- Per-migrator: no CREATE on public. Does not change PUBLIC or other apps.
  EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I', migrator_role);

  INSERT INTO app_migration_control.registered_apps (
    app_namespace, owner_role, migrator_role, runtime_role
  ) VALUES (p_namespace, owner_role, migrator_role, runtime_role)
  ON CONFLICT (app_namespace) DO UPDATE
    SET owner_role = EXCLUDED.owner_role,
        migrator_role = EXCLUDED.migrator_role,
        runtime_role = EXCLUDED.runtime_role;
END;
$$;

REVOKE ALL ON FUNCTION app_migration_control.provision_app(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_migration_control.provision_app(text, text) TO CURRENT_USER;

-- Trusted bootstrap-only allowlist registration (not granted to app migrators).
CREATE OR REPLACE FUNCTION app_migration_control.allow_realtime_projection(
  p_namespace text,
  p_table text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app_migration_control, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM app_migration_control.registered_apps WHERE app_namespace = p_namespace
  ) THEN
    RAISE EXCEPTION 'namespace is not registered';
  END IF;
  IF p_table !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid table name for realtime allowlist';
  END IF;
  INSERT INTO app_migration_control.realtime_allowlist (app_namespace, table_name)
  VALUES (p_namespace, p_table)
  ON CONFLICT DO NOTHING;
END;
$$;

ALTER FUNCTION app_migration_control.allow_realtime_projection(text, text)
  OWNER TO app_migration_owner;
REVOKE ALL ON FUNCTION app_migration_control.allow_realtime_projection(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_migration_control.allow_realtime_projection(text, text)
  TO CURRENT_USER;

CREATE OR REPLACE FUNCTION app_migration_control.add_to_realtime(
  p_namespace text,
  p_table text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF session_user IS DISTINCT FROM (p_namespace || '_migrator') THEN
    RAISE EXCEPTION 'only the app migrator may register realtime tables';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM app_migration_control.registered_apps r
    JOIN app_migration_control.realtime_allowlist a
      ON a.app_namespace = r.app_namespace
    WHERE r.app_namespace = p_namespace
      AND r.migrator_role = session_user
      AND a.table_name = p_table
  ) THEN
    RAISE EXCEPTION
      'table is not on the trusted realtime allowlist '
      '(app migrations cannot publish alternate tables)';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = p_namespace
      AND c.relname = p_table
      AND c.relkind IN ('r', 'p')
  ) THEN
    RAISE EXCEPTION 'table not found in app namespace';
  END IF;
  BEGIN
    EXECUTE format(
      'ALTER PUBLICATION supabase_realtime ADD TABLE %I.%I',
      p_namespace,
      p_table
    );
  EXCEPTION
    WHEN duplicate_object THEN
      NULL;
  END;
END;
$$;

ALTER FUNCTION app_migration_control.add_to_realtime(text, text) OWNER TO app_migration_owner;
REVOKE ALL ON FUNCTION app_migration_control.add_to_realtime(text, text) FROM PUBLIC;

-- Scoped SECURITY DEFINER guard: registered migrator sessions and registered
-- app namespaces only. Unrelated preexisting owners may still create approved
-- SECURITY DEFINER routines outside registered namespaces.
CREATE OR REPLACE FUNCTION app_migration_control.reject_security_definer()
RETURNS event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app_migration_control, pg_temp
AS $$
DECLARE
  rec record;
  is_definer boolean;
  session_is_migrator boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM app_migration_control.registered_apps r
    WHERE r.migrator_role = session_user
  ) INTO session_is_migrator;

  FOR rec IN
    SELECT objid, object_type, schema_name
    FROM pg_event_trigger_ddl_commands()
    WHERE object_type IN ('function', 'procedure')
  LOOP
    SELECT p.prosecdef INTO is_definer
    FROM pg_proc p
    WHERE p.oid = rec.objid;
    IF NOT COALESCE(is_definer, false) THEN
      CONTINUE;
    END IF;
    IF rec.schema_name = 'app_migration_control' THEN
      CONTINUE;
    END IF;
    IF session_is_migrator
       OR EXISTS (
         SELECT 1
         FROM app_migration_control.registered_apps r
         WHERE r.app_namespace = rec.schema_name
       ) THEN
      RAISE EXCEPTION
        'SECURITY DEFINER routines are forbidden for registered app migrators '
        'and inside registered app namespaces';
    END IF;
  END LOOP;
END;
$$;

ALTER FUNCTION app_migration_control.reject_security_definer()
  OWNER TO app_migration_owner;

DROP EVENT TRIGGER IF EXISTS app_migration_reject_security_definer;
CREATE EVENT TRIGGER app_migration_reject_security_definer
  ON ddl_command_end
  WHEN TAG IN ('CREATE FUNCTION', 'CREATE PROCEDURE')
  EXECUTE FUNCTION app_migration_control.reject_security_definer();

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
  ALTER PUBLICATION supabase_realtime OWNER TO app_migration_owner;
END
$$;

CREATE TABLE IF NOT EXISTS app_marker (
  app_name text PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app_marker (app_name)
VALUES ('app_two')
ON CONFLICT (app_name) DO NOTHING;

ALTER TABLE app_marker ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_marker FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS app_marker_runtime_select ON app_marker;
CREATE POLICY app_marker_runtime_select ON app_marker
  FOR SELECT TO app_two_runtime
  USING (true);

GRANT SELECT ON app_marker TO app_two_runtime;

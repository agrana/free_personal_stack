CREATE TABLE IF NOT EXISTS app_marker (
  app_name text PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app_marker (app_name)
VALUES ('app_beta')
ON CONFLICT (app_name) DO NOTHING;

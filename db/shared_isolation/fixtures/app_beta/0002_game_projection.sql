CREATE TABLE IF NOT EXISTS game_private_state (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL,
  secret_payload text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS game_public_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW game_sanitized_projection
WITH (security_barrier = true, security_invoker = true)
AS
SELECT
  id,
  owner_user_id,
  created_at,
  (length(secret_payload) > 0) AS has_secret
FROM game_private_state;

ALTER TABLE game_private_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_public_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS game_private_select_own ON game_private_state;
CREATE POLICY game_private_select_own ON game_private_state
  FOR SELECT TO authenticated
  USING (
    owner_user_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
    AND coalesce(current_setting('request.jwt.claim.app_namespace', true), '') = 'app_beta'
  );

DROP POLICY IF EXISTS game_private_write_own ON game_private_state;
CREATE POLICY game_private_write_own ON game_private_state
  FOR ALL TO authenticated
  USING (
    owner_user_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
    AND coalesce(current_setting('request.jwt.claim.app_namespace', true), '') = 'app_beta'
  )
  WITH CHECK (
    owner_user_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
    AND coalesce(current_setting('request.jwt.claim.app_namespace', true), '') = 'app_beta'
  );

DROP POLICY IF EXISTS game_events_read ON game_public_events;
CREATE POLICY game_events_read ON game_public_events
  FOR SELECT TO authenticated
  USING (
    coalesce(current_setting('request.jwt.claim.app_namespace', true), '') = 'app_beta'
  );

GRANT SELECT, INSERT, UPDATE, DELETE ON game_private_state TO authenticated;
GRANT SELECT, INSERT ON game_public_events TO authenticated;
GRANT SELECT ON game_sanitized_projection TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON game_private_state TO service_role;
GRANT SELECT ON game_sanitized_projection TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON game_private_state TO app_beta_migrator;
GRANT SELECT, INSERT ON game_public_events TO app_beta_migrator;

SELECT app_migration_control.add_to_realtime('app_beta', 'game_public_events');

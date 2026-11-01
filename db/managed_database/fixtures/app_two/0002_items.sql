CREATE TABLE IF NOT EXISTS items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL,
  note text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE items FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS items_select_own ON items;
CREATE POLICY items_select_own ON items
  FOR SELECT TO app_two_runtime
  USING (
    owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
  );

DROP POLICY IF EXISTS items_insert_own ON items;
CREATE POLICY items_insert_own ON items
  FOR INSERT TO app_two_runtime
  WITH CHECK (
    owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
  );

DROP POLICY IF EXISTS items_update_own ON items;
CREATE POLICY items_update_own ON items
  FOR UPDATE TO app_two_runtime
  USING (
    owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
  )
  WITH CHECK (
    owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
  );

DROP POLICY IF EXISTS items_delete_own ON items;
CREATE POLICY items_delete_own ON items
  FOR DELETE TO app_two_runtime
  USING (
    owner_id::text = coalesce(current_setting('request.jwt.claim.sub', true), '')
  );

GRANT SELECT, INSERT, UPDATE, DELETE ON items TO app_two_runtime;

-- Simulates pre-existing shared-project objects (SavedTube-like). App migrations
-- must not alter these. SavedTube code/history is not inspected by APP-003.

CREATE SCHEMA IF NOT EXISTS supabase_migrations;

CREATE TABLE IF NOT EXISTS supabase_migrations.schema_migrations (
  version text PRIMARY KEY,
  name text
);

INSERT INTO supabase_migrations.schema_migrations (version, name)
VALUES ('20240101000001', 'initial_schema')
ON CONFLICT (version) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.preexisting_shared (
  id int PRIMARY KEY,
  note text NOT NULL,
  owner_user_id uuid
);

INSERT INTO public.preexisting_shared (id, note)
VALUES (1, 'do-not-touch')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE public.preexisting_shared ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS preexisting_owner_all ON public.preexisting_shared;
CREATE POLICY preexisting_owner_all ON public.preexisting_shared
  FOR ALL TO authenticated
  USING (true)
  WITH CHECK (true);

GRANT SELECT ON public.preexisting_shared TO authenticated;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'preexisting_shared'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.preexisting_shared;
  END IF;
END
$$;

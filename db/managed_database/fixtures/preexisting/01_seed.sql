-- Unrelated preexisting private objects that managed provisioning must preserve.
-- Applied only on disposable local test databases.

CREATE SCHEMA IF NOT EXISTS legacy_other_app AUTHORIZATION CURRENT_USER;

CREATE TABLE IF NOT EXISTS legacy_other_app.private_history (
  id bigint PRIMARY KEY,
  note text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO legacy_other_app.private_history (id, note)
VALUES (1, 'unrelated-preexisting')
ON CONFLICT (id) DO NOTHING;

REVOKE ALL ON SCHEMA legacy_other_app FROM PUBLIC;
REVOKE ALL ON TABLE legacy_other_app.private_history FROM PUBLIC;

CREATE TABLE IF NOT EXISTS public.preexisting_marker (
  id int PRIMARY KEY,
  payload text NOT NULL
);

INSERT INTO public.preexisting_marker (id, payload)
VALUES (1, 'leave-me-alone')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE public.preexisting_marker ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS preexisting_marker_deny ON public.preexisting_marker;
CREATE POLICY preexisting_marker_deny ON public.preexisting_marker
  FOR ALL TO PUBLIC
  USING (false)
  WITH CHECK (false);

GRANT SELECT ON public.preexisting_marker TO PUBLIC;

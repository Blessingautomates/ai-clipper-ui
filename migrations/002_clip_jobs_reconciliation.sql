-- ToolStackAI Video Clipper: reconcile the audited EXISTING table.
-- Prepared locally; not applied. See docs/002_clip_jobs_reconciliation.md.
-- Apply this file directly, NOT 001 followed by 002, on the audited database.
-- Stop submissions and all workers; capture a restorable backup first.
-- No jobs are deleted, reassigned, reset to pending, or converted from non-array
-- payloads. Incompatible data/schema aborts the entire transaction.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog;
LOCK TABLE public.clip_jobs IN ACCESS EXCLUSIVE MODE;

DO $preflight$
DECLARE
  problem text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class
    WHERE oid = 'public.clip_jobs'::regclass AND relkind = 'r') THEN
    RAISE EXCEPTION '002 requires the audited ordinary public.clip_jobs table';
  END IF;

  SELECT string_agg(e.name, ', ' ORDER BY e.name) INTO problem
  FROM (VALUES
    ('id', 'uuid'::regtype, true), ('user_id', 'uuid'::regtype, true),
    ('video_url', 'text'::regtype, true), ('status', 'text'::regtype, true),
    ('clips', 'jsonb'::regtype, true), ('error_message', 'text'::regtype, true),
    ('created_at', 'timestamptz'::regtype, true),
    ('attempts', 'integer'::regtype, false),
    ('processing_started_at', 'timestamptz'::regtype, false),
    ('transcript_source', 'text'::regtype, false),
    ('updated_at', 'timestamptz'::regtype, false)
  ) AS e(name, type_oid, required)
  LEFT JOIN pg_attribute a ON a.attrelid = 'public.clip_jobs'::regclass
    AND a.attname = e.name AND a.attnum > 0 AND NOT a.attisdropped
  WHERE (e.required AND a.attname IS NULL)
    OR (a.attname IS NOT NULL AND
      (a.atttypid <> e.type_oid OR a.atttypmod <> -1
       OR a.attgenerated <> '' OR a.attidentity <> ''));
  IF problem IS NOT NULL THEN
    RAISE EXCEPTION 'Missing or incompatible columns (no implicit casts): %', problem;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.clip_jobs'::regclass AND contype = 'p'
      AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
        WHERE attrelid = 'public.clip_jobs'::regclass AND attname = 'id')]::smallint[])
  THEN RAISE EXCEPTION 'Expected primary key on id is missing'; END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.clip_jobs'::regclass AND contype = 'f'
      AND confrelid = 'auth.users'::regclass AND confdeltype = 'c' AND convalidated
      AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
        WHERE attrelid = 'public.clip_jobs'::regclass AND attname = 'user_id')]::smallint[]
      AND confkey = ARRAY[(SELECT attnum FROM pg_attribute
        WHERE attrelid = 'auth.users'::regclass AND attname = 'id')]::smallint[])
  THEN RAISE EXCEPTION 'Expected validated user_id -> auth.users(id) CASCADE FK differs'; END IF;

  -- Only the table-specific trigger installed below is allowed on a rerun.
  -- Do not overwrite shared touch_updated_at() or unknown trigger side effects.
  IF EXISTS (SELECT 1 FROM pg_trigger t
    WHERE t.tgrelid = 'public.clip_jobs'::regclass AND NOT t.tgisinternal
      AND NOT (t.tgname = 'clip_jobs_002_touch_updated_at'
        AND t.tgfoid = COALESCE(to_regprocedure('public.clip_jobs_002_touch_updated_at()')::oid, 0::oid)))
  THEN RAISE EXCEPTION 'Unexpected clip_jobs trigger requires review'; END IF;

  -- Prevent silently replacing an incompatible queue contract or leaving an
  -- older overload exposed through PostgREST. 001 is historical, not a prerequisite.
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname IN
      ('claim_job', 'reap_stale_jobs', 'clip_jobs_002_touch_updated_at')
      AND NOT (p.prokind = 'f' AND (
        (p.proname = 'claim_job' AND p.pronargs = 0
          AND p.prorettype = 'public.clip_jobs'::regtype AND p.proretset)
        OR (p.proname = 'reap_stale_jobs' AND p.proargtypes = '1186 23'::oidvector
          AND p.prorettype = 'integer'::regtype AND NOT p.proretset)
        OR (p.proname = 'clip_jobs_002_touch_updated_at' AND p.pronargs = 0
          AND p.prorettype = 'trigger'::regtype AND NOT p.proretset))))
  THEN RAISE EXCEPTION 'Unexpected RPC/trigger signature; inspect before changing it'; END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role' AND rolbypassrls)
    OR EXISTS (SELECT 1 FROM pg_roles
      WHERE rolname IN ('anon', 'authenticated') AND (rolbypassrls OR rolsuper))
  THEN RAISE EXCEPTION 'Supabase role/RLS assumptions differ'; END IF;

  -- Empty in supplied audit. If rows have arrived, retain them or fail closed;
  -- never guess owners/statuses or manufacture a processing lease.
  IF EXISTS (SELECT 1 FROM public.clip_jobs
    WHERE user_id IS NULL OR created_at IS NULL OR btrim(COALESCE(video_url, '')) = ''
      OR status IS NULL OR status NOT IN ('pending', 'processing', 'completed', 'failed')
      OR (clips IS NOT NULL AND jsonb_typeof(clips) <> 'array'))
  THEN RAISE EXCEPTION 'Ownerless/invalid jobs require explicit reconciliation; no rows changed'; END IF;
  IF EXISTS (SELECT 1 FROM public.clip_jobs j WHERE j.status = 'processing'
    AND (to_jsonb(j)->>'processing_started_at' IS NULL
      OR to_jsonb(j)->>'attempts' IS NULL))
  THEN RAISE EXCEPTION 'Existing processing jobs lack retry metadata; let work finish or review individually'; END IF;
END;
$preflight$;

-- Private transaction-local preservation assertion, not a durable backup.
CREATE TEMP TABLE clip_jobs_002_before ON COMMIT DROP AS
SELECT id, to_jsonb(j) AS before_row FROM public.clip_jobs j;

ALTER TABLE public.clip_jobs
  ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS processing_started_at timestamptz,
  ADD COLUMN IF NOT EXISTS transcript_source text,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz;

DROP TRIGGER IF EXISTS clip_jobs_002_touch_updated_at ON public.clip_jobs;
-- Preserve populated timestamps on reruns. This is a baseline, not recovered history.
UPDATE public.clip_jobs SET updated_at = created_at WHERE updated_at IS NULL;
UPDATE public.clip_jobs SET clips = '[]'::jsonb WHERE clips IS NULL;

ALTER TABLE public.clip_jobs
  ALTER COLUMN id SET DEFAULT gen_random_uuid(),
  ALTER COLUMN user_id DROP DEFAULT,
  ALTER COLUMN user_id SET NOT NULL,
  ALTER COLUMN video_url DROP DEFAULT,
  ALTER COLUMN video_url SET NOT NULL,
  ALTER COLUMN status SET DEFAULT 'pending',
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN clips SET DEFAULT '[]'::jsonb,
  ALTER COLUMN clips SET NOT NULL,
  ALTER COLUMN error_message DROP DEFAULT,
  ALTER COLUMN error_message DROP NOT NULL,
  ALTER COLUMN transcript_source DROP DEFAULT,
  ALTER COLUMN transcript_source DROP NOT NULL,
  ALTER COLUMN processing_started_at DROP DEFAULT,
  ALTER COLUMN processing_started_at DROP NOT NULL,
  ALTER COLUMN created_at SET DEFAULT now(),
  ALTER COLUMN created_at SET NOT NULL,
  ALTER COLUMN updated_at SET DEFAULT now(),
  ALTER COLUMN updated_at SET NOT NULL,
  ALTER COLUMN attempts SET DEFAULT 0,
  ALTER COLUMN attempts SET NOT NULL;

-- Recreate only checks owned by this reconciliation, so names cannot mask
-- obsolete definitions. Constraint validation preserves all accepted payloads.
ALTER TABLE public.clip_jobs
  DROP CONSTRAINT IF EXISTS clip_jobs_status_check,
  DROP CONSTRAINT IF EXISTS clip_jobs_attempts_check,
  DROP CONSTRAINT IF EXISTS clip_jobs_clips_array_check,
  DROP CONSTRAINT IF EXISTS clip_jobs_video_url_check;
ALTER TABLE public.clip_jobs
  ADD CONSTRAINT clip_jobs_status_check
    CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  ADD CONSTRAINT clip_jobs_attempts_check CHECK (attempts >= 0),
  ADD CONSTRAINT clip_jobs_clips_array_check CHECK (jsonb_typeof(clips) = 'array'),
  ADD CONSTRAINT clip_jobs_video_url_check CHECK (btrim(video_url) <> '');

-- Queue selection, ownership/RLS plus CASCADE lookup, and stale recovery.
-- Small audited table: transactional index builds, not CONCURRENTLY.
CREATE INDEX IF NOT EXISTS clip_jobs_status_created_at_idx
  ON public.clip_jobs USING btree (status, created_at, id);
CREATE INDEX IF NOT EXISTS clip_jobs_user_id_idx
  ON public.clip_jobs USING btree (user_id);
CREATE INDEX IF NOT EXISTS clip_jobs_processing_started_at_idx
  ON public.clip_jobs USING btree (processing_started_at) WHERE status = 'processing';

-- IF NOT EXISTS must not silently accept a wrong/conflicting index definition.
DO $indexes$
BEGIN
  IF EXISTS (
    SELECT 1 FROM (VALUES
      ('clip_jobs_status_created_at_idx', ARRAY['status', 'created_at', 'id'], NULL::text),
      ('clip_jobs_user_id_idx', ARRAY['user_id'], NULL::text),
      ('clip_jobs_processing_started_at_idx', ARRAY['processing_started_at'],
        '(status = ''processing''::text)')
    ) AS e(name, columns, predicate)
    LEFT JOIN pg_class c ON c.oid = to_regclass('public.' || e.name)
    LEFT JOIN pg_index i ON i.indexrelid = c.oid
    LEFT JOIN pg_am am ON am.oid = c.relam
    WHERE i.indrelid IS DISTINCT FROM 'public.clip_jobs'::regclass
      OR NOT i.indisvalid OR NOT i.indisready OR i.indisunique
      OR am.amname IS DISTINCT FROM 'btree'
      OR i.indnatts <> cardinality(e.columns) OR i.indnkeyatts <> cardinality(e.columns)
      OR i.indexprs IS NOT NULL OR EXISTS (SELECT 1 FROM unnest(i.indoption) o WHERE o <> 0)
      OR pg_get_expr(i.indpred, i.indrelid) IS DISTINCT FROM e.predicate
      OR ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
        ORDER BY k.pos) IS DISTINCT FROM e.columns
  ) THEN RAISE EXCEPTION 'Queue index definition/name collision; transaction rolled back'; END IF;
END;
$indexes$;

CREATE OR REPLACE FUNCTION public.clip_jobs_002_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $touch$
BEGIN
  NEW.updated_at := pg_catalog.now();
  RETURN NEW;
END;
$touch$;
REVOKE ALL ON FUNCTION public.clip_jobs_002_touch_updated_at() FROM PUBLIC, anon, authenticated, service_role;
-- Trigger invocation does not require callers to EXECUTE the trigger function.
CREATE TRIGGER clip_jobs_002_touch_updated_at BEFORE UPDATE ON public.clip_jobs
FOR EACH ROW EXECUTE FUNCTION public.clip_jobs_002_touch_updated_at();

-- Worker already accepts either a dict or list. SETOF returns [] for an empty
-- queue and a one-element list for a claim, avoiding an all-NULL composite row.
-- INVOKER needs no owner elevation: service_role has BYPASSRLS and limited ACLs.
CREATE OR REPLACE FUNCTION public.claim_job()
RETURNS SETOF public.clip_jobs LANGUAGE sql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog
AS $claim$
  WITH candidate AS (
    SELECT j.id FROM public.clip_jobs j WHERE j.status = 'pending'
    ORDER BY j.created_at, j.id LIMIT 1 FOR UPDATE SKIP LOCKED
  )
  UPDATE public.clip_jobs j
    SET status = 'processing', processing_started_at = pg_catalog.now(),
        attempts = j.attempts + 1, error_message = NULL
    FROM candidate c WHERE j.id = c.id RETURNING j.*;
$claim$;

CREATE OR REPLACE FUNCTION public.reap_stale_jobs(
  stale_after interval DEFAULT '30 minutes', max_attempts integer DEFAULT 3
)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $reap$
DECLARE reaped integer;
BEGIN
  IF stale_after IS NULL OR stale_after <= interval '0 seconds'
    OR max_attempts IS NULL OR max_attempts < 1 THEN
    RAISE EXCEPTION 'stale_after and max_attempts must be positive' USING ERRCODE = '22023';
  END IF;
  WITH stale AS (
    SELECT j.id FROM public.clip_jobs j
    WHERE j.status = 'processing'
      AND j.processing_started_at < pg_catalog.now() - stale_after
    ORDER BY j.processing_started_at, j.id FOR UPDATE SKIP LOCKED
  )
  UPDATE public.clip_jobs j
    SET status = CASE WHEN j.attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
        error_message = CASE WHEN j.attempts >= max_attempts
          THEN COALESCE(j.error_message, 'Abandoned after ' || j.attempts || ' attempt(s)')
          ELSE j.error_message END,
        processing_started_at = NULL
    FROM stale s WHERE j.id = s.id;
  GET DIAGNOSTICS reaped = ROW_COUNT;
  RETURN reaped;
END;
$reap$;
REVOKE ALL ON FUNCTION public.claim_job(), public.reap_stale_jobs(interval, integer)
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.claim_job(), public.reap_stale_jobs(interval, integer) TO service_role;

ALTER TABLE public.clip_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clip_jobs NO FORCE ROW LEVEL SECURITY;

-- Exact old USING/WITH CHECK expressions were not supplied with the audit
-- summary. Emit catalog definitions before removal; never assume USING(true).
-- All table policies must be replaced: permissive policies combine with OR.
DO $policies$
DECLARE p record;
BEGIN
  FOR p IN SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS using_expr,
      pg_get_expr(polwithcheck, polrelid) AS check_expr
    FROM pg_policy WHERE polrelid = 'public.clip_jobs'::regclass
  LOOP
    RAISE NOTICE 'Replacing clip_jobs policy %, cmd %, USING %, WITH CHECK %',
      p.polname, p.polcmd, p.using_expr, p.check_expr;
    EXECUTE format('DROP POLICY %I ON public.clip_jobs', p.polname);
  END LOOP;
END;
$policies$;
CREATE POLICY clip_jobs_select_own ON public.clip_jobs FOR SELECT TO authenticated
  USING ((SELECT auth.uid()) = user_id);
CREATE POLICY clip_jobs_insert_own ON public.clip_jobs FOR INSERT TO authenticated
  WITH CHECK ((SELECT auth.uid()) = user_id);

-- Table REVOKE alone does not remove independently granted column permissions.
REVOKE ALL PRIVILEGES ON TABLE public.clip_jobs FROM PUBLIC, anon, authenticated, service_role;
DO $column_acls$
DECLARE columns text;
BEGIN
  SELECT string_agg(format('%I', attname), ', ' ORDER BY attnum) INTO columns
    FROM pg_attribute WHERE attrelid = 'public.clip_jobs'::regclass
      AND attnum > 0 AND NOT attisdropped;
  EXECUTE format('REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), REFERENCES (%1$s)
    ON public.clip_jobs FROM PUBLIC, anon, authenticated, service_role', columns);
END;
$column_acls$;
GRANT USAGE ON SCHEMA public TO authenticated, service_role;
GRANT SELECT ON TABLE public.clip_jobs TO authenticated, service_role;
GRANT INSERT (user_id, video_url) ON public.clip_jobs TO authenticated;
GRANT UPDATE (status, clips, error_message, transcript_source, attempts, processing_started_at)
  ON public.clip_jobs TO service_role;

DO $preservation$
BEGIN
  IF EXISTS (SELECT 1 FROM public.clip_jobs j
    FULL JOIN pg_temp.clip_jobs_002_before b ON b.id = j.id
    WHERE j.id IS NULL OR b.id IS NULL
      OR (to_jsonb(j) - ARRAY['attempts', 'processing_started_at', 'transcript_source', 'updated_at'])
        IS DISTINCT FROM (jsonb_set(b.before_row, '{clips}',
          CASE WHEN b.before_row->'clips' = 'null'::jsonb THEN '[]'::jsonb
            ELSE b.before_row->'clips' END)
          - ARRAY['attempts', 'processing_started_at', 'transcript_source', 'updated_at'])
      OR NOT (to_jsonb(j) @> (b.before_row - ARRAY['clips', 'updated_at']))
      OR (b.before_row->>'updated_at' IS NOT NULL
        AND to_jsonb(j)->'updated_at' IS DISTINCT FROM b.before_row->'updated_at'))
  THEN RAISE EXCEPTION 'Job membership or preserved field changed; transaction rolled back'; END IF;

  -- Effective ACLs include role inheritance. Fail closed if an inherited grant
  -- survived direct revocation; do not alter shared role memberships globally.
  IF has_table_privilege('anon', 'public.clip_jobs', 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
    OR has_any_column_privilege('anon', 'public.clip_jobs', 'SELECT, INSERT, UPDATE, REFERENCES')
    OR has_table_privilege('authenticated', 'public.clip_jobs', 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
    OR has_any_column_privilege('authenticated', 'public.clip_jobs', 'UPDATE, REFERENCES')
    OR has_table_privilege('service_role', 'public.clip_jobs', 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
    OR has_any_column_privilege('service_role', 'public.clip_jobs', 'INSERT, REFERENCES')
    OR EXISTS (SELECT 1 FROM pg_attribute a
      WHERE a.attrelid = 'public.clip_jobs'::regclass AND a.attnum > 0 AND NOT a.attisdropped
        AND ((a.attname NOT IN ('user_id', 'video_url')
          AND has_column_privilege('authenticated', a.attrelid, a.attnum, 'INSERT'))
        OR (a.attname NOT IN ('status', 'clips', 'error_message', 'transcript_source', 'attempts', 'processing_started_at')
          AND has_column_privilege('service_role', a.attrelid, a.attnum, 'UPDATE'))))
    OR EXISTS (SELECT 1 FROM pg_proc p CROSS JOIN (VALUES ('anon'), ('authenticated')) r(name)
      WHERE p.oid IN ('public.claim_job()'::regprocedure,
        'public.reap_stale_jobs(interval,integer)'::regprocedure)
        AND has_function_privilege(r.name, p.oid, 'EXECUTE'))
  THEN RAISE EXCEPTION 'Excessive inherited privileges remain; transaction rolled back'; END IF;
END;
$preservation$;

NOTIFY pgrst, 'reload schema';
COMMIT;

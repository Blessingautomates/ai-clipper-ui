-- DRAFT ONLY: proposed migration 002, NOT an applied/deployable migration.
-- Kept under docs/drafts/ so migration runners do not discover it.
-- Based on confirmed live differences dated 2026-11-06, not migration 001.
-- Companion review/rollback plan: 002_clip_jobs_reconciliation.md.
--
-- Proposed scope: add the four missing queue columns; normalize SQL NULL clips
-- to []; establish status/clips defaults and NOT NULL; install a table-specific
-- updated_at trigger; preserve every job ID, ownership, status and non-NULL clip
-- payload. Preserve the confirmed ON DELETE CASCADE FK and current RLS settings.
-- RPC/index/policy/table-grant changes await the consolidated inspection results.
--
-- The guard intentionally aborts execution. Remove it only during an explicitly
-- approved finalization, after backup, data decisions and staging verification.
-- This draft has NOT been executed against any database.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $draft_guard$
BEGIN
  RAISE EXCEPTION
    'DRAFT 002: do not execute. Review inspection outputs and preservation/rollback plan first.';
END;
$draft_guard$;

-- Quiesce job submissions and ALL workers before an approved execution.
-- This short maintenance transaction blocks writes; it does not delete jobs.
LOCK TABLE public.clip_jobs IN ACCESS EXCLUSIVE MODE;

DO $preflight$
DECLARE
  schema_problem text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_class
    WHERE oid = to_regclass('public.clip_jobs')
      AND relkind IN ('r', 'p')
      AND relrowsecurity
      AND NOT relforcerowsecurity
  ) THEN
    RAISE EXCEPTION 'clip_jobs relation/RLS differs from confirmed baseline';
  END IF;

  -- No implicit casts or guesses about live types.
  SELECT string_agg(e.column_name, ', ' ORDER BY e.column_name)
    INTO schema_problem
  FROM (
    VALUES
      ('id', 'uuid'::regtype),
      ('user_id', 'uuid'::regtype),
      ('video_url', 'text'::regtype),
      ('status', 'text'::regtype),
      ('clips', 'jsonb'::regtype),
      ('error_message', 'text'::regtype),
      ('created_at', 'timestamptz'::regtype)
  ) AS e(column_name, type_oid)
  LEFT JOIN pg_attribute a
    ON a.attrelid = 'public.clip_jobs'::regclass
   AND a.attname = e.column_name
   AND a.attnum > 0 AND NOT a.attisdropped
  WHERE a.attname IS NULL OR a.atttypid <> e.type_oid;
  IF schema_problem IS NOT NULL THEN
    RAISE EXCEPTION 'Missing/incompatible existing columns: %', schema_problem;
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = 'public.clip_jobs'::regclass
      AND attname IN (
        'attempts', 'processing_started_at', 'transcript_source', 'updated_at'
      )
      AND attnum > 0 AND NOT attisdropped
  ) THEN
    RAISE EXCEPTION 'Expected-new columns already exist; revise draft, do not overwrite';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.clip_jobs'::regclass
      AND conname = 'clip_jobs_pkey' AND contype = 'p'
      AND conkey = ARRAY[
        (SELECT attnum FROM pg_attribute
         WHERE attrelid = 'public.clip_jobs'::regclass AND attname = 'id')
      ]::smallint[]
  ) THEN
    RAISE EXCEPTION 'Expected primary key on id is not present';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.clip_jobs'::regclass
      AND conname = 'clip_jobs_user_id_fkey' AND contype = 'f'
      AND confrelid = 'auth.users'::regclass
      AND confdeltype = 'c' AND convalidated
      AND conkey = ARRAY[
        (SELECT attnum FROM pg_attribute
         WHERE attrelid = 'public.clip_jobs'::regclass AND attname = 'user_id')
      ]::smallint[]
      AND confkey = ARRAY[
        (SELECT attnum FROM pg_attribute
         WHERE attrelid = 'auth.users'::regclass AND attname = 'id')
      ]::smallint[]
  ) THEN
    RAISE EXCEPTION 'Confirmed user_id -> auth.users(id) CASCADE FK differs';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgrelid = 'public.clip_jobs'::regclass AND NOT tgisinternal
  ) THEN
    RAISE EXCEPTION 'Existing application triggers require explicit review before backfills';
  END IF;

  IF to_regprocedure('public.clip_jobs_002_touch_updated_at()') IS NOT NULL THEN
    RAISE EXCEPTION 'Proposed trigger function already exists; inspect before replacement';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.clip_jobs'::regclass
      AND conname IN (
        'clip_jobs_status_check', 'clip_jobs_attempts_check',
        'clip_jobs_clips_array_check'
      )
  ) THEN
    RAISE EXCEPTION 'Proposed check names already exist; compare definitions first';
  END IF;

  -- NULL status is NOT inferred as pending: that could reprocess historical jobs.
  IF EXISTS (SELECT 1 FROM public.clip_jobs WHERE status IS NULL) THEN
    RAISE EXCEPTION 'NULL status jobs need an approved per-job disposition before NOT NULL';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.clip_jobs
    WHERE status NOT IN ('pending', 'processing', 'completed', 'failed')
  ) THEN
    RAISE EXCEPTION 'Unknown statuses require an approved mapping; no automatic reset';
  END IF;
  IF EXISTS (SELECT 1 FROM public.clip_jobs WHERE status = 'processing') THEN
    RAISE EXCEPTION 'Resolve existing processing jobs explicitly before introducing retry timestamps';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.clip_jobs
    WHERE clips IS NOT NULL AND jsonb_typeof(clips) <> 'array'
  ) THEN
    RAISE EXCEPTION 'Non-array clips require a preservation plan; no payload conversion';
  END IF;
END;
$preflight$;

-- Full existing rows remain private inside this transaction, never in output.
-- This TEMP snapshot verifies preservation, but is NOT a durable rollback backup.
-- A verified external backup is required before removing the execution guard.
CREATE TEMP TABLE clip_jobs_002_before ON COMMIT DROP AS
SELECT id, to_jsonb(j) AS before_row FROM public.clip_jobs j;

ALTER TABLE public.clip_jobs
  ADD COLUMN attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN processing_started_at timestamptz,
  ADD COLUMN transcript_source text,
  ADD COLUMN updated_at timestamptz;

-- Legacy attempt count is unknown: zero is a queue baseline, not reconstructed
-- history. Legacy updated_at is derived from created_at, not a historical claim.
-- Processing timestamps and transcript sources remain NULL; do not invent them.
UPDATE public.clip_jobs
SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP);

-- The only proposed change to an existing field is SQL NULL clips -> [].
-- Preserve every existing non-NULL array, including older clip object schemas.
UPDATE public.clip_jobs SET clips = '[]'::jsonb WHERE clips IS NULL;

ALTER TABLE public.clip_jobs
  ALTER COLUMN status SET DEFAULT 'pending',
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN clips SET DEFAULT '[]'::jsonb,
  ALTER COLUMN clips SET NOT NULL,
  ALTER COLUMN updated_at SET DEFAULT now(),
  ALTER COLUMN updated_at SET NOT NULL;

ALTER TABLE public.clip_jobs
  ADD CONSTRAINT clip_jobs_status_check
    CHECK (status IN ('pending', 'processing', 'completed', 'failed')) NOT VALID,
  ADD CONSTRAINT clip_jobs_attempts_check
    CHECK (attempts >= 0) NOT VALID,
  ADD CONSTRAINT clip_jobs_clips_array_check
    CHECK (jsonb_typeof(clips) = 'array') NOT VALID;
ALTER TABLE public.clip_jobs VALIDATE CONSTRAINT clip_jobs_status_check;
ALTER TABLE public.clip_jobs VALIDATE CONSTRAINT clip_jobs_attempts_check;
ALTER TABLE public.clip_jobs VALIDATE CONSTRAINT clip_jobs_clips_array_check;

-- Do not replace public.touch_updated_at(): it might be used by other tables.
CREATE FUNCTION public.clip_jobs_002_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $touch$
BEGIN
  NEW.updated_at := pg_catalog.now();
  RETURN NEW;
END;
$touch$;
REVOKE ALL ON FUNCTION public.clip_jobs_002_touch_updated_at()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.clip_jobs_002_touch_updated_at() TO service_role;

CREATE TRIGGER clip_jobs_002_touch_updated_at
BEFORE UPDATE ON public.clip_jobs
FOR EACH ROW EXECUTE FUNCTION public.clip_jobs_002_touch_updated_at();

-- Assert exact ID membership and preservation of ALL pre-existing fields.
-- Only new columns and the explicitly proposed NULL-clips normalization differ.
DO $preservation$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.clip_jobs j
    FULL JOIN pg_temp.clip_jobs_002_before b ON b.id = j.id
    WHERE j.id IS NULL OR b.id IS NULL
       OR (
         to_jsonb(j) - ARRAY[
           'attempts', 'processing_started_at', 'transcript_source', 'updated_at'
         ]
       ) IS DISTINCT FROM (
         CASE WHEN b.before_row->'clips' = 'null'::jsonb
           THEN jsonb_set(b.before_row, '{clips}', '[]'::jsonb, false)
           ELSE b.before_row
         END
       )
  ) THEN
    RAISE EXCEPTION 'Preservation check failed: ID set or existing field changed';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.clip_jobs
    WHERE attempts <> 0 OR processing_started_at IS NOT NULL
       OR transcript_source IS NOT NULL OR updated_at IS NULL
  ) THEN
    RAISE EXCEPTION 'Unexpected new-column baseline';
  END IF;
END;
$preservation$;

-- No DROP/DELETE/TRUNCATE, FK replacement, ownership reassignment, RLS changes,
-- table grants, RPC replacement or index assumptions are part of this draft.
-- Finalize the remaining inspected queue objects before promotion to migrations/.
COMMIT;

-- ToolStackAI Video Clipper: Stage 0 live database inspection
-- READ ONLY. Run manually in Supabase SQL Editor; never via the application.
-- Confirmed on 2026-11-06: public.clip_jobs exists; RLS enabled, not forced;
-- attempts, processing_started_at, transcript_source, updated_at are missing;
-- status and clips are nullable; clip_jobs_pkey and clip_jobs_user_id_fkey
-- exist; the user_id foreign key uses ON DELETE CASCADE.
-- Migration 001 describes repository intent, not verified live state.
--
-- Every statement below is SELECT or WITH ... SELECT. Queue RPCs are inspected,
-- never invoked. No role changes, DDL or DML occur. Raw job URLs, clip text and error
-- messages are excluded. Redact unexpected secrets in custom function bodies
-- before sharing inspection outputs. Job summaries include job IDs for targeted follow-up.
-- Missing queue fields are read through to_jsonb; only the confirmed clips
-- column is referenced directly to distinguish SQL NULL from JSON null.
-- Run sections in order. Each SELECT produces its own result set; SQL Editor
-- may show only the last one, so select individual sections when copying results.
-- Results can reflect concurrent writes; capture a stable maintenance window
-- before finalizing a migration. This script does not lock or pause workers.

-- 1. Relation, owner, RLS flags and table ACL.
SELECT
  n.nspname AS schema_name,
  c.relname AS relation_name,
  c.relkind,
  pg_get_userbyid(c.relowner) AS owner,
  c.relrowsecurity AS rls_enabled,
  c.relforcerowsecurity AS rls_forced,
  COALESCE(c.relacl, acldefault('r', c.relowner)) AS table_acl
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid = to_regclass('public.clip_jobs');

-- 2. Live column types, nullability, defaults and generated/identity attributes.
SELECT
  a.attnum AS position,
  a.attname AS column_name,
  format_type(a.atttypid, a.atttypmod) AS data_type,
  a.attnotnull AS not_null,
  pg_get_expr(d.adbin, d.adrelid) AS default_expression,
  a.attidentity AS identity_kind,
  a.attgenerated AS generated_kind
FROM pg_attribute a
LEFT JOIN pg_attrdef d
  ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = to_regclass('public.clip_jobs')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;

-- 3. Constraints, incoming references and status-constraint name collisions.
-- confdeltype: c = CASCADE, n = SET NULL, a = NO ACTION, r = RESTRICT.
SELECT
  c.conrelid::regclass::text AS source_relation,
  c.conname AS constraint_name,
  c.contype AS constraint_type,
  c.convalidated AS validated,
  CASE WHEN c.confrelid <> 0
    THEN c.confrelid::regclass::text
  END AS referenced_relation,
  c.confdeltype AS foreign_key_delete_action,
  pg_get_constraintdef(c.oid, true) AS definition
FROM pg_constraint c
WHERE c.conrelid = to_regclass('public.clip_jobs')
   OR c.confrelid = to_regclass('public.clip_jobs')
   OR c.conname = 'clip_jobs_status_check'
ORDER BY source_relation, constraint_name;

-- 4. Index definitions and validity, including a conflicting expected name.
SELECT
  ix.indrelid::regclass::text AS indexed_relation,
  n.nspname AS index_schema,
  i.relname AS index_name,
  ix.indisprimary AS primary_index,
  ix.indisunique AS unique_index,
  ix.indisvalid AS valid,
  ix.indisready AS ready,
  pg_get_indexdef(ix.indexrelid) AS definition
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_namespace n ON n.oid = i.relnamespace
WHERE ix.indrelid = to_regclass('public.clip_jobs')
   OR (
     n.nspname = 'public'
     AND i.relname = 'clip_jobs_status_created_at_idx'
   )
ORDER BY indexed_relation, index_name;

-- 5. ALL policies. Permissive policies combine with OR; restrictive policies
-- add restrictions. SQL Editor reads do not prove authenticated RLS behavior.
SELECT
  schemaname, tablename, policyname, permissive, roles, cmd,
  qual AS using_expression,
  with_check AS check_expression
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename = 'clip_jobs'
ORDER BY policyname;

-- 6. All table triggers, plus other users of public.touch_updated_at().
-- enabled_mode: O = origin, D = disabled, R = replica, A = always.
-- Internal foreign-key triggers are included; their definitions are not changed.
SELECT
  t.tgrelid::regclass::text AS relation_name,
  t.tgname AS trigger_name,
  t.tgenabled AS enabled_mode,
  t.tgisinternal AS internal_trigger,
  n.nspname AS function_schema,
  p.proname AS function_name,
  pg_get_triggerdef(t.oid, true) AS trigger_definition,
  pg_get_functiondef(p.oid) AS trigger_function_definition
FROM pg_trigger t
JOIN pg_proc p ON p.oid = t.tgfoid
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE t.tgrelid = to_regclass('public.clip_jobs')
   OR (n.nspname = 'public' AND p.proname = 'touch_updated_at')
ORDER BY relation_name, trigger_name;

-- 7. Queue RPC definitions, overloads, argument names/defaults and execution ACL.
-- Worker contract: claim_job() -> clip_jobs row;
-- reap_stale_jobs(stale_after interval, max_attempts integer) -> integer.
-- Worker supplies "30 minutes" and 3; migration 001 defaults to 20 minutes.
SELECT
  n.nspname AS function_schema,
  p.proname AS function_name,
  pg_get_function_identity_arguments(p.oid) AS identity_arguments,
  pg_get_function_arguments(p.oid) AS arguments_and_defaults,
  pg_get_function_result(p.oid) AS return_type,
  p.proretset AS returns_set,
  p.prosecdef AS security_definer,
  p.proconfig AS function_settings,
  pg_get_userbyid(p.proowner) AS owner,
  COALESCE(p.proacl, acldefault('f', p.proowner)) AS execution_acl,
  pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prokind = 'f'
  AND p.proname IN (
    'claim_job', 'reap_stale_jobs', 'touch_updated_at',
    'clip_jobs_002_touch_updated_at'
  )
ORDER BY function_schema, function_name, identity_arguments;

-- 8. Effective schema/table privileges, including inherited and PUBLIC grants.
SELECT
  r.rolname AS role_name,
  r.rolsuper AS superuser,
  r.rolbypassrls AS bypasses_rls,
  has_schema_privilege(r.oid, 'public', 'USAGE') AS schema_usage,
  has_schema_privilege(r.oid, 'public', 'CREATE') AS schema_create,
  has_table_privilege(r.oid, t.oid, 'SELECT') AS table_select,
  has_table_privilege(r.oid, t.oid, 'INSERT') AS table_insert,
  has_table_privilege(r.oid, t.oid, 'UPDATE') AS table_update,
  has_table_privilege(r.oid, t.oid, 'DELETE') AS table_delete
FROM pg_roles r
LEFT JOIN pg_class t ON t.oid = to_regclass('public.clip_jobs')
WHERE r.rolname IN ('anon', 'authenticated', 'service_role')
ORDER BY role_name;

-- 9. Effective column privileges: INSERT on worker-owned fields needs review.
SELECT
  r.rolname AS role_name,
  a.attname AS column_name,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'SELECT') AS can_select,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'INSERT') AS can_insert,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'UPDATE') AS can_update
FROM pg_attribute a
CROSS JOIN pg_roles r
WHERE a.attrelid = to_regclass('public.clip_jobs')
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND r.rolname IN ('anon', 'authenticated', 'service_role')
ORDER BY role_name, a.attnum;

-- 10. Effective queue RPC execution permission; DOES NOT invoke an RPC.
SELECT
  n.nspname AS function_schema,
  p.proname AS function_name,
  pg_get_function_identity_arguments(p.oid) AS identity_arguments,
  r.rolname AS role_name,
  has_schema_privilege(r.oid, n.oid, 'USAGE') AS schema_usage,
  has_function_privilege(r.oid, p.oid, 'EXECUTE') AS can_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
CROSS JOIN pg_roles r
WHERE p.prokind = 'f'
  AND p.proname IN ('claim_job', 'reap_stale_jobs')
  AND r.rolname IN ('anon', 'authenticated', 'service_role')
ORDER BY function_schema, function_name, identity_arguments, role_name;

-- 11. Existing-job preservation/reconciliation counts. No implicit timestamp
-- or numeric casts: incorrect live types cannot cause conversion exceptions.
WITH jobs AS (
  SELECT to_jsonb(j) AS job, j.clips IS NULL AS clips_is_sql_null
  FROM public.clip_jobs j
)
SELECT
  count(*) AS total_jobs,
  count(*) FILTER (WHERE job->>'id' IS NULL) AS missing_ids,
  count(*) FILTER (WHERE job->>'user_id' IS NULL) AS ownerless_jobs,
  count(*) FILTER (
    WHERE job->>'user_id' IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM auth.users u
        WHERE u.id::text = job->>'user_id'
      )
  ) AS unmatched_owner_ids,
  count(*) FILTER (
    WHERE btrim(COALESCE(job->>'video_url', '')) = ''
  ) AS missing_or_blank_source_urls,
  count(*) FILTER (WHERE job->>'status' IS NULL) AS null_status_jobs,
  count(*) FILTER (
    WHERE job->>'status' IS NOT NULL
      AND job->>'status' NOT IN ('pending', 'processing', 'completed', 'failed')
  ) AS unrecognized_status_jobs,
  count(*) FILTER (WHERE job->>'status' = 'processing') AS processing_jobs,
  count(*) FILTER (
    WHERE job->>'status' = 'processing'
      AND job->>'processing_started_at' IS NULL
  ) AS processing_without_start_time,
  count(*) FILTER (WHERE clips_is_sql_null) AS sql_null_clips_jobs,
  count(*) FILTER (
    WHERE NOT clips_is_sql_null AND job->'clips' = 'null'::jsonb
  ) AS json_null_clips_jobs,
  count(*) FILTER (
    WHERE NOT clips_is_sql_null
      AND jsonb_typeof(job->'clips') <> 'array'
  ) AS non_array_clips_jobs,
  count(*) FILTER (
    WHERE job->>'status' = 'completed'
      AND (
        jsonb_typeof(job->'clips') IS DISTINCT FROM 'array'
        OR job->'clips' = '[]'::jsonb
      )
  ) AS completed_without_nonempty_clip_array
FROM jobs;

-- 12. Status distribution (NULL stays NULL; it is not inferred as pending).
WITH jobs AS (
  SELECT to_jsonb(j) AS job FROM public.clip_jobs j
)
SELECT job->>'status' AS status, count(*) AS job_count
FROM jobs
GROUP BY job->>'status'
ORDER BY status NULLS FIRST;

-- 13. At most 50 existing-job summaries for targeted follow-up.
-- Source URLs, user IDs, payload text and raw errors are deliberately excluded.
-- Missing attempts/timestamps/transcript_source appear as NULL.
WITH jobs AS (
  SELECT to_jsonb(j) AS job FROM public.clip_jobs j
)
SELECT
  job->>'id' AS job_id,
  job->>'status' AS status,
  job->>'created_at' AS created_at,
  job->>'updated_at' AS updated_at,
  job->>'processing_started_at' AS processing_started_at,
  job->'attempts' AS attempts,
  job->>'transcript_source' AS transcript_source,
  job->>'user_id' IS NOT NULL AS has_owner,
  btrim(COALESCE(job->>'video_url', '')) <> '' AS has_source_url,
  job->>'error_message' IS NOT NULL AS has_error_message,
  jsonb_typeof(job->'clips') AS clips_json_type,
  CASE WHEN jsonb_typeof(job->'clips') = 'array'
    THEN jsonb_array_length(job->'clips')
  END AS clip_count
FROM jobs
ORDER BY job->>'created_at' DESC NULLS LAST, job->>'id'
LIMIT 50;

-- 14. Clip payload shape counts, including legacy fields and asset references.
-- A reference-key count does not prove a playable file exists or is authorized.
WITH jobs AS (
  SELECT to_jsonb(j) AS job FROM public.clip_jobs j
),
clips AS (
  SELECT e.clip
  FROM jobs
  CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(job->'clips') = 'array'
      THEN job->'clips' ELSE '[]'::jsonb
    END
  ) AS e(clip)
)
SELECT
  count(*) AS total_clip_entries,
  count(*) FILTER (
    WHERE jsonb_typeof(clip) IS DISTINCT FROM 'object'
  ) AS non_object_entries,
  count(*) FILTER (
    WHERE clip ? 'start_time' AND clip ? 'end_time'
  ) AS entries_with_current_time_fields,
  count(*) FILTER (
    WHERE clip ? 'start' AND clip ? 'end'
      AND NOT (clip ? 'start_time')
  ) AS entries_with_legacy_time_fields,
  count(*) FILTER (
    WHERE clip ?| ARRAY[
      'video_url', 'videoUrl', 'storage_path', 'output_path', 'download_url'
    ]
  ) AS entries_with_asset_reference_keys
FROM clips;

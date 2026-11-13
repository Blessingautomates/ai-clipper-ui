-- READ ONLY verification after migrations/002_clip_jobs_reconciliation.sql.
-- No queue RPC invocation, role impersonation, DDL, DML, or private job payloads.
-- Select sections separately if SQL Editor displays only the final result set.
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;

-- 1. Table/RLS and role assumptions. Every passed value must be true.
SELECT 'ordinary table; RLS enabled, not forced' AS check_name,
  COALESCE((SELECT relkind = 'r' AND relrowsecurity AND NOT relforcerowsecurity
    FROM pg_class WHERE oid = to_regclass('public.clip_jobs')), false) AS passed
UNION ALL
SELECT 'service_role bypasses RLS; browser roles do not',
  (SELECT count(*) = 3 AND bool_and(CASE WHEN rolname = 'service_role'
    THEN rolbypassrls ELSE NOT rolbypassrls AND NOT rolsuper END)
   FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role'));

-- 2. Eleven required columns, exact type/nullability/default. All passed=true.
WITH expected(name, type_oid, not_null, default_expr) AS (VALUES
  ('id', 'uuid'::regtype, true, 'gen_random_uuid()'),
  ('user_id', 'uuid'::regtype, true, NULL::text),
  ('video_url', 'text'::regtype, true, NULL::text),
  ('status', 'text'::regtype, true, '''pending''::text'),
  ('clips', 'jsonb'::regtype, true, '''[]''::jsonb'),
  ('error_message', 'text'::regtype, false, NULL::text),
  ('created_at', 'timestamptz'::regtype, true, 'now()'),
  ('updated_at', 'timestamptz'::regtype, true, 'now()'),
  ('attempts', 'integer'::regtype, true, '0'),
  ('processing_started_at', 'timestamptz'::regtype, false, NULL::text),
  ('transcript_source', 'text'::regtype, false, NULL::text)
)
SELECT e.name AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
  a.attnotnull AS not_null, pg_get_expr(d.adbin, d.adrelid) AS default_expression,
  COALESCE(a.atttypid = e.type_oid AND a.atttypmod = -1
    AND a.attnotnull = e.not_null AND a.attgenerated = '' AND a.attidentity = ''
    AND pg_get_expr(d.adbin, d.adrelid) IS NOT DISTINCT FROM e.default_expr, false) AS passed
FROM expected e
LEFT JOIN pg_attribute a ON a.attrelid = to_regclass('public.clip_jobs')
  AND a.attname = e.name AND a.attnum > 0 AND NOT a.attisdropped
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
ORDER BY e.name;

-- 3. Required check constraints (four rows, validated=true).
-- Definitions must restrict status to pending/processing/completed/failed,
-- attempts >= 0, clips to JSON arrays, and video_url to nonblank text.
WITH expected(name) AS (VALUES ('clip_jobs_status_check'), ('clip_jobs_attempts_check'),
  ('clip_jobs_clips_array_check'), ('clip_jobs_video_url_check'))
SELECT e.name, c.contype, c.convalidated AS validated,
  pg_get_constraintdef(c.oid, true) AS definition,
  COALESCE(c.contype = 'c' AND c.convalidated, false) AS passed
FROM expected e LEFT JOIN pg_constraint c
  ON c.conrelid = to_regclass('public.clip_jobs') AND c.conname = e.name
ORDER BY e.name;

-- 4. PK and retained FK. Both passed=true; FK remains ON DELETE CASCADE.
SELECT 'primary key on id' AS check_name, EXISTS (SELECT 1 FROM pg_constraint c
  WHERE c.conrelid = to_regclass('public.clip_jobs') AND c.contype = 'p'
    AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute
      WHERE attrelid = to_regclass('public.clip_jobs') AND attname = 'id')]::smallint[]) AS passed
UNION ALL
SELECT 'user_id -> auth.users(id), validated, ON DELETE CASCADE', EXISTS (
  SELECT 1 FROM pg_constraint c WHERE c.conrelid = to_regclass('public.clip_jobs')
    AND c.contype = 'f' AND c.confrelid = to_regclass('auth.users')
    AND c.confdeltype = 'c' AND c.convalidated
    AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute
      WHERE attrelid = to_regclass('public.clip_jobs') AND attname = 'user_id')]::smallint[]
    AND c.confkey = ARRAY[(SELECT attnum FROM pg_attribute
      WHERE attrelid = to_regclass('auth.users') AND attname = 'id')]::smallint[]);

-- 5. Three required indexes. All passed=true; PK index is additionally retained.
WITH expected(name, columns, predicate) AS (VALUES
  ('clip_jobs_status_created_at_idx', ARRAY['status', 'created_at', 'id'], NULL::text),
  ('clip_jobs_user_id_idx', ARRAY['user_id'], NULL::text),
  ('clip_jobs_processing_started_at_idx', ARRAY['processing_started_at'],
    '(status = ''processing''::text)')
)
SELECT e.name, pg_get_indexdef(i.indexrelid) AS definition,
  COALESCE(i.indrelid = to_regclass('public.clip_jobs') AND i.indisvalid AND i.indisready
    AND NOT i.indisunique AND am.amname = 'btree' AND i.indexprs IS NULL
    AND i.indnatts = cardinality(e.columns) AND i.indnkeyatts = cardinality(e.columns)
    AND NOT EXISTS (SELECT 1 FROM unnest(i.indoption) o WHERE o <> 0)
    AND pg_get_expr(i.indpred, i.indrelid) IS NOT DISTINCT FROM e.predicate
    AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
      JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
      ORDER BY k.pos) = e.columns, false) AS passed
FROM expected e LEFT JOIN pg_class c ON c.oid = to_regclass('public.' || e.name)
LEFT JOIN pg_index i ON i.indexrelid = c.oid LEFT JOIN pg_am am ON am.oid = c.relam
ORDER BY e.name;

-- 6. Exactly two ownership policies. No public/anon/ALL/UPDATE/DELETE policy.
SELECT schemaname, tablename, policyname, permissive, roles, cmd,
  qual AS using_expression, with_check AS check_expression
FROM pg_policies WHERE schemaname = 'public' AND tablename = 'clip_jobs'
ORDER BY policyname;
-- Expected: authenticated SELECT USING ((SELECT auth.uid()) = user_id),
-- authenticated INSERT WITH CHECK ((SELECT auth.uid()) = user_id).
SELECT 'exactly the two authenticated ownership policies' AS check_name,
  count(*) = 2 AND count(*) FILTER (WHERE
    polroles = ARRAY[(SELECT oid FROM pg_roles WHERE rolname = 'authenticated')]
    AND polpermissive AND (
      (polname = 'clip_jobs_select_own' AND polcmd = 'r' AND polwithcheck IS NULL
        AND regexp_replace(pg_get_expr(polqual, polrelid), '\s+', '', 'g')
          = '((SELECTauth.uid()ASuid)=user_id)')
      OR (polname = 'clip_jobs_insert_own' AND polcmd = 'a' AND polqual IS NULL
        AND regexp_replace(pg_get_expr(polwithcheck, polrelid), '\s+', '', 'g')
          = '((SELECTauth.uid()ASuid)=user_id)'))) = 2 AS passed
FROM pg_policy WHERE polrelid = to_regclass('public.clip_jobs');

-- 7. Effective TABLE privileges (column grants are checked separately below).
-- SELECT: authenticated/service_role true, anon false. Every other flag false.
SELECT r.rolname AS role_name,
  has_schema_privilege(r.oid, 'public', 'USAGE') AS schema_usage,
  has_table_privilege(r.oid, t.oid, 'SELECT') AS table_select,
  has_table_privilege(r.oid, t.oid, 'INSERT') AS table_insert,
  has_table_privilege(r.oid, t.oid, 'UPDATE') AS table_update,
  has_table_privilege(r.oid, t.oid, 'DELETE') AS table_delete,
  has_table_privilege(r.oid, t.oid, 'TRUNCATE') AS table_truncate,
  has_table_privilege(r.oid, t.oid, 'REFERENCES') AS table_references,
  has_table_privilege(r.oid, t.oid, 'TRIGGER') AS table_trigger,
  COALESCE(has_table_privilege(r.oid, t.oid, 'SELECT') = (r.rolname <> 'anon')
    AND NOT has_table_privilege(r.oid, t.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
    AND (r.rolname = 'anon' OR has_schema_privilege(r.oid, 'public', 'USAGE')), false) AS passed
FROM pg_roles r LEFT JOIN pg_class t ON t.oid = to_regclass('public.clip_jobs')
WHERE r.rolname IN ('anon', 'authenticated', 'service_role') ORDER BY r.rolname;

-- 8. Effective COLUMN privileges (includes inheritance and PUBLIC grants).
-- anon: none. authenticated: SELECT all, INSERT user_id/video_url only.
-- service_role: SELECT all, UPDATE the six queue/result fields only.
SELECT r.rolname AS role_name, a.attname AS column_name,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'SELECT') AS can_select,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'INSERT') AS can_insert,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'UPDATE') AS can_update,
  has_column_privilege(r.oid, a.attrelid, a.attnum, 'REFERENCES') AS can_reference,
  (has_column_privilege(r.oid, a.attrelid, a.attnum, 'SELECT') = (r.rolname <> 'anon')
    AND has_column_privilege(r.oid, a.attrelid, a.attnum, 'INSERT') =
      (r.rolname = 'authenticated' AND a.attname IN ('user_id', 'video_url'))
    AND has_column_privilege(r.oid, a.attrelid, a.attnum, 'UPDATE') =
      (r.rolname = 'service_role' AND a.attname IN
        ('status', 'clips', 'error_message', 'transcript_source', 'attempts', 'processing_started_at'))
    AND NOT has_column_privilege(r.oid, a.attrelid, a.attnum, 'REFERENCES')) AS passed
FROM pg_attribute a CROSS JOIN pg_roles r
WHERE a.attrelid = to_regclass('public.clip_jobs') AND a.attnum > 0 AND NOT a.attisdropped
  AND r.rolname IN ('anon', 'authenticated', 'service_role') ORDER BY r.rolname, a.attnum;

-- 9. One application trigger; internal FK triggers retained. Inspect body too.
SELECT t.tgname, t.tgenabled, t.tgisinternal,
  pg_get_triggerdef(t.oid, true) AS trigger_definition
FROM pg_trigger t WHERE t.tgrelid = to_regclass('public.clip_jobs') ORDER BY t.tgname;
SELECT 'exactly one enabled table-specific BEFORE UPDATE row trigger' AS check_name,
  count(*) = 1 AND count(*) FILTER (WHERE tgname = 'clip_jobs_002_touch_updated_at'
    AND tgfoid = to_regprocedure('public.clip_jobs_002_touch_updated_at()')
    AND tgenabled = 'O' AND tgtype = 19 AND tgqual IS NULL AND tgnargs = 0) = 1 AS passed
FROM pg_trigger WHERE tgrelid = to_regclass('public.clip_jobs') AND NOT tgisinternal;

-- 10. Required functions, return contracts, search paths, defaults and bodies.
-- Exactly these three signatures; security_definer=false; search_path=pg_catalog.
-- claim_job returns SETOF public.clip_jobs (0/1 rows).
-- reaper returns integer; defaults '00:30:00'::interval and 3.
WITH expected(signature, return_oid, returns_set) AS (VALUES
  ('public.claim_job()', 'public.clip_jobs'::regtype, true),
  ('public.reap_stale_jobs(interval,integer)', 'integer'::regtype, false),
  ('public.clip_jobs_002_touch_updated_at()', 'trigger'::regtype, false)
)
SELECT e.signature, pg_get_function_arguments(p.oid) AS arguments_and_defaults,
  pg_get_function_result(p.oid) AS result_type, p.prosecdef AS security_definer,
  p.proconfig AS function_settings, pg_get_userbyid(p.proowner) AS owner,
  pg_get_functiondef(p.oid) AS definition,
  COALESCE(p.prokind = 'f' AND p.prorettype = e.return_oid AND p.proretset = e.returns_set
    AND NOT p.prosecdef AND p.proconfig = ARRAY['search_path=pg_catalog'], false) AS passed
FROM expected e LEFT JOIN pg_proc p ON p.oid = to_regprocedure(e.signature)
ORDER BY e.signature;
SELECT 'no unexpected public queue/trigger overloads' AS check_name, count(*) = 3 AS passed
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname IN
  ('claim_job', 'reap_stale_jobs', 'clip_jobs_002_touch_updated_at');

-- 11. Effective RPC execution: worker only; trigger callable by none of these.
WITH expected(signature, worker_rpc) AS (VALUES
  ('public.claim_job()', true), ('public.reap_stale_jobs(interval,integer)', true),
  ('public.clip_jobs_002_touch_updated_at()', false)
)
SELECT e.signature, r.rolname AS role_name,
  has_function_privilege(r.oid, to_regprocedure(e.signature), 'EXECUTE') AS can_execute,
  COALESCE(has_function_privilege(r.oid, to_regprocedure(e.signature), 'EXECUTE')
    = (e.worker_rpc AND r.rolname = 'service_role'), false) AS passed
FROM expected e CROSS JOIN pg_roles r WHERE r.rolname IN ('anon', 'authenticated', 'service_role')
ORDER BY e.signature, r.rolname;

-- 12. PUBLIC must have no direct table/column/function grants on these objects.
SELECT 'no PUBLIC table, column, or function grants' AS check_name,
  NOT EXISTS (SELECT 1 FROM pg_class c,
    LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) acl
    WHERE c.oid = to_regclass('public.clip_jobs') AND acl.grantee = 0)
  AND NOT EXISTS (SELECT 1 FROM pg_attribute a, LATERAL aclexplode(a.attacl) acl
    WHERE a.attrelid = to_regclass('public.clip_jobs') AND a.attnum > 0
      AND NOT a.attisdropped AND acl.grantee = 0)
  AND NOT EXISTS (SELECT 1 FROM pg_proc p,
    LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
    WHERE p.oid IN (to_regprocedure('public.claim_job()'),
      to_regprocedure('public.reap_stale_jobs(interval,integer)'),
      to_regprocedure('public.clip_jobs_002_touch_updated_at()')) AND acl.grantee = 0) AS passed;

-- 13. Preservation totals and invariants. Immediately after the audited empty
-- baseline, total_jobs=0. After submissions, a positive count is expected.
-- invalid_jobs=0 always. Compare totals with the pre-migration capture/backup.
SELECT count(*) AS total_jobs,
  count(*) FILTER (WHERE user_id IS NULL OR status IS NULL OR clips IS NULL
    OR updated_at IS NULL OR created_at IS NULL OR attempts IS NULL OR attempts < 0
    OR status NOT IN ('pending', 'processing', 'completed', 'failed')
    OR jsonb_typeof(clips) <> 'array' OR btrim(video_url) = '') AS invalid_jobs
FROM public.clip_jobs;

COMMIT;

# Migration 002: application and database reconciliation

Prepared locally against the completed 14-check production audit and all current
repository database call sites. **Production is unchanged.** Apply and verify the
SQL manually using the sequence below. No live connection, deployment, commit,
or push was performed while preparing these files.

For the current ordered deployment procedure, use the
[deployment runbook](002_DEPLOYMENT_RUNBOOK.md). Its production sequence supersedes
the staging-first order below when no separate staging environment is configured;
the schema, preservation, and rollback explanations here remain applicable.

The runnable file is [migrations/002_clip_jobs_reconciliation.sql](../migrations/002_clip_jobs_reconciliation.sql).
The [verification SQL](002_clip_jobs_verification.sql) is read-only.
The original [audit script](stage-0-live-database-audit.sql), migration 001, and
guarded [SQL draft](drafts/002_clip_jobs_reconciliation.sql) remain intact.
The historical draft is superseded; it is not another migration to execute.

## Application contract and evidence

| Code | Actual database/authentication requirement |
| --- | --- |
| `app/api/generate/route.js` | Insert an owned job, return its row/ID. Now uses the cookie-backed server client, verifies `auth.getUser()`, derives `user_id` from that user, and inserts only `user_id` and `video_url`. Caller-supplied `userId` and queue fields are ignored. |
| `worker.py:claim_job` | Calls `claim_job()` without arguments; accepts a row dictionary or a list. Requires atomic pending-to-processing transition, attempt increment, start timestamp, and a returned job row. |
| `worker.py:reap_stale_jobs` | Calls `reap_stale_jobs(stale_after, max_attempts)` with `"30 minutes"` and `3`; needs an integer affected-row count. |
| `worker.py:process_video_job` | Reads `id`, `video_url`, and the claimed `attempts`; updates status, clips, transcript provenance, and errors. Success and failure now filter by ID, `status = processing`, and the claimed attempt so late writes cannot overwrite reclaimed work. |
| `.github/workflows/worker.yml` | Worker-only service-role secret, serialized queue runs, 55-minute runner timeout. No browser service-role client is required. |
| `utils/supabase/server.ts`, `utils/supabase/client.ts`, auth routes/login | Existing cookie session with public anon key. Queue authorization must use the verified user plus RLS. |
| `app/auth/callback/route.js` | Existing origin-based redirect validation and cookie exchange are preserved byte-for-byte; regression tests exercise hostile redirects. |

Both dashboard files were inspected. `page.js` displays mock clips and simulated
generation; `page.tsx` calls `NEXT_PUBLIC_PYTHON_BACKEND_URL` or `/api/process-video`.
Neither reads or submits `clip_jobs`. They also coexist for the same route, an
existing routing ambiguity. `/api/process-video` analyzes through an external
LLM; `/api/webhook` forwards to n8n. Neither queries this table or authenticates
its caller. They remain outside this database reconciliation. Hardening these
endpoints and connecting the dashboard to owned queue progress are separate work.
No middleware or other queue query/RPC call sites were found.

The legacy `lib/supabaseClient.js` uses a public publishable/anon client and a
hard-coded project fallback, not a service-role credential. The creation route
no longer imports it. Both active auth helpers use `NEXT_PUBLIC_SUPABASE_URL`
and `NEXT_PUBLIC_SUPABASE_ANON_KEY`. Keep the service-role key exclusively in
worker/server secret configuration, never in `NEXT_PUBLIC_*`, client imports,
API responses, or committed files.

The supplied live audit confirms an empty seven-column table, nullable owner/
status/clips, missing queue metadata/RPCs/indexes, and overly broad policies and
grants. The exact original policy USING/WITH CHECK expressions were **not**
included in that summary or recorded as results in the repository. Their values
cannot be honestly reported from this local review. Audit section 5 selects them;
the migration also emits their actual catalog definitions as NOTICEs before
replacement. Retain that output with the original audit evidence. Policy name
and command alone do not prove a particular expression.

## Final schema and why it is needed

| Column | Final definition | Reason |
| --- | --- | --- |
| `id` | uuid, primary key, default `gen_random_uuid()`, NOT NULL | Generated job identity used by API and worker. Existing IDs/PK retained. |
| `user_id` | uuid, NOT NULL; existing FK to `auth.users(id)` ON DELETE CASCADE; no default | Every new API job has a verified owner. Empty baseline makes tightening safe; ownerless rows cause an abort, never reassignment. Preserve the audited deletion behavior rather than 001's SET NULL proposal. |
| `video_url` | text, NOT NULL, nonblank; no default | Worker input. Blank inputs would be skipped and stranded. HTTP(S) validation remains in the API; no provider-specific restriction is invented. |
| `status` | text, NOT NULL, default `pending`; four-state CHECK | Exactly the pending/processing/completed/failed states used by the worker. NULL/unknown existing statuses stop the migration, never reset to pending. |
| `clips` | jsonb, NOT NULL, default `[]`; JSON-array CHECK | Worker produces arrays. Only SQL NULL becomes `[]`; non-array/JSON-null data stops migration. No clip-object fields are rewritten or rigidly constrained. |
| `error_message` | nullable text, no default | Worker clears errors on claim/success and records failures. |
| `created_at` | timestamptz, NOT NULL, default `now()` | Creation route omits timestamps; queue ordering needs a valid timestamp. Existing values retained. |
| `updated_at` | timestamptz, NOT NULL, default `now()` | Requested database timestamp handling for job state changes. Missing legacy values derive from `created_at`; populated values remain unchanged. No current dashboard reads this field. |
| `attempts` | integer, NOT NULL, default 0; CHECK >= 0 | Claim increments, reaper caps retries, worker guards stale results. Missing historical counts receive a baseline of zero, not reconstructed history. |
| `processing_started_at` | nullable timestamptz, no default | Claim establishes the start; reaper finds abandoned work and clears the start when requeued/failed. Legacy processing rows without retry metadata abort. Completed jobs retain their last start timestamp. |
| `transcript_source` | nullable text, no default | Worker writes `youtube_captions` or `whisper`. No ENUM/CHECK is needed to couple future providers to a migration. |

No storage/upload references, asset URLs, separate clips table, heartbeat,
completed timestamp, credit accounting, or retry RPC is added: the application
does not implement those contracts. `completed` currently means completed clip
analysis JSON; the worker does not render/store downloadable video artifacts.

The migration retains existing constraints and adds/replaces four table-specific
checks. It builds three btree indexes: `(status, created_at, id)` for deterministic
FIFO claims, `(user_id)` for ownership selection and CASCADE lookup, and a partial
`(processing_started_at) WHERE status = processing` for the reaper. Existing PK
index stays. Conflicting index definitions cause rollback rather than silently
accepting a name. Indexes build inside the transaction because the audited table
is empty; reassess maintenance duration if it has grown substantially.

A single table-specific BEFORE UPDATE trigger writes `updated_at = now()`.
The shared `public.touch_updated_at()` is not replaced. The trigger function has
`SECURITY INVOKER`, `search_path = pg_catalog`, and no direct app-role EXECUTE.
PostgreSQL checks trigger-function EXECUTE at trigger creation; row updates do
not require this privilege for the updating role.

## RLS, grants, and RPC security

RLS stays enabled and unforced. All policies on this table are replaced with
exactly two permissive policies for `authenticated`: SELECT using ownership and
INSERT checking ownership, both `((SELECT auth.uid()) = user_id)`. No public,
anon, UPDATE, or DELETE policies remain. Keeping any broad permissive policy
would OR it with the ownership policy and defeat tenant isolation.

Both table grants **and independent column grants** are revoked from PUBLIC,
anon, authenticated, and service_role before these minimal grants are installed:

| Role | Final permissions on `clip_jobs` |
| --- | --- |
| PUBLIC / anon | None. |
| authenticated | Table SELECT, subject to RLS; column INSERT only on `user_id`, `video_url`. No status/clips/attempt/timestamp insertion; no UPDATE/DELETE. |
| service_role | Table SELECT; column UPDATE only on status, clips, error_message, transcript_source, attempts, processing_started_at; EXECUTE only on the two queue RPCs. No INSERT, DELETE, owner/URL/ID modification, or table UPDATE grant. |

BYPASSRLS does not bypass ordinary table/column ACLs. Both queue RPCs therefore
use `SECURITY INVOKER`, a fixed `pg_catalog` search path, and schema-qualified
table references. No SECURITY DEFINER escalation is needed. The migration checks
the expected Supabase role attributes and fails if inherited grants leave
excessive effective permissions; it never edits shared role memberships or
global default privileges. This limits the shared service_role's access to this
table, not every other object in the Supabase project.

`claim_job()` returns SETOF `public.clip_jobs`, zero or one row, with `FOR UPDATE
SKIP LOCKED`. The worker already supports list results. Claim increments attempts,
sets processing/start time, and clears errors in the same statement.
`reap_stale_jobs(interval, integer)` defaults to 30 minutes/3, validates positive
arguments, skips locked rows, requeues stale jobs below the attempt cap, and fails
those at the cap. It retains clip payloads and does not retry ordinary analysis
failures automatically. Both RPCs are inaccessible to browser roles/PUBLIC.

The configured reaper runs before processing and the workflow serializes runs.
Keep this serialization and avoid concurrent ad hoc queue workers. Thirty minutes
can expire during legitimate long analysis if parallel workers are introduced;
choose a duration covering legitimate processing or design heartbeat leases
before such a deployment. Attempt guards stop stale database overwrites, but
cannot prevent duplicate external analysis costs after an early requeue.

## Exact application order

1. Review these local files and arrange a separate disposable PostgreSQL/Supabase
   staging database. Preserve the original audit outputs; capture the existing
   policy expressions from audit section 5 if they were not retained. Obtain a
   restorable backup of jobs and schema/policy/ACL/function definitions even
   though the supplied baseline was empty.
2. Rehearse **002 directly** against the audited schema in staging, including
   RLS/column grants, API enqueue, claim, completion, stale recovery, and a rerun.
   Do not run 001 or the guarded historical draft first. 001 does not remove the
   broad policy/grants and has a different claim return contract. 002 aborts on
   unexpected overloads, signatures, triggers, types, or index definitions rather
   than dropping unknown dependencies.
3. When staging passes and a production maintenance window is approved, pause
   new job submissions and every queue worker. Confirm no processing jobs have
   unknown retry metadata and take the backup/current catalog capture.
4. In the correct production project's SQL Editor, as its migration administrator
   (normally `postgres`), run the entire
   `migrations/002_clip_jobs_reconciliation.sql` once. Its BEGIN/COMMIT, exclusive
   lock, five-second lock timeout, and sixty-second statement timeout keep the
   change atomic. Capture the replaced-policy NOTICEs. On any exception, issue
   ROLLBACK and investigate; do not remove guards to force execution.
5. While submissions/workers remain paused, run
   `docs/002_clip_jobs_verification.sql`. Every `passed` value must be true;
   inspect the constraint and function bodies/defaults as well. Confirm the
   totals match the pre-migration capture. The notification refreshes PostgREST
   at commit; wait for refreshed columns/functions if API schema-cache errors
   occur. The production database is reconciled only after these checks pass.
6. Deploy the companion `/api/generate` session/defaults change and the worker
   attempt guards together with 002. The old anonymous route will fail under the
   new grants, so keep submissions disabled until the new route is active. The
   old worker lacks protection against late writes after reclaims. Verify public
   frontend keys and the worker-only secret configuration before resuming workers
   and submissions. No deployment is performed by this repository task.
7. With separately authorized test accounts/jobs, perform a small end-to-end
   enqueue -> claim -> analysis -> owned-result read and cross-user isolation
   smoke test. RPC calls and enqueue tests mutate data; the verification SQL
   intentionally does not perform them. Do not describe rendered downloads as
   working based on this database/analysis smoke test.

## Verification expectations

Verification sections check all 11 definitions/defaults/nullability; four
validated checks; the retained PK/CASCADE FK; three valid indexes; exactly two
authenticated ownership policies; effective table and column ACLs; one enabled
timestamp trigger plus internal FK triggers; the two RPC signatures and isolated
trigger function; search paths, bodies, defaults and execution ACLs; PUBLIC
grants; and aggregate row invariants. Immediately after applying to the supplied
empty baseline, total_jobs/invalid_jobs should both be zero. Later valid jobs
increase the total without increasing invalid_jobs.

In staging, use two distinct authenticated users and an anon caller. Verify own
insert/read succeed, foreign-owner insert fails, foreign-owner reads return no
rows, worker-field INSERT and all browser UPDATE/DELETE/RPC calls are denied.
Verify service_role claims one oldest job, increments attempts, updates allowed
results and timestamps, but cannot insert/delete/reassign jobs. Exercise stale
requeue and terminal cap, nonpositive reaper argument rejection, late-attempt
write rejection, and an empty claim result. Concurrent claims require separate
PostgreSQL sessions in staging; static catalogs do not prove concurrency or the
live PostgREST/authentication boundary.

## Local validation

Offline commands (never start `worker.py`'s CLI queue or use real credentials):

```sh
node --experimental-vm-modules --test tests/generate-route.test.mjs
python3 -m unittest discover -s tests -p 'test_worker_queue.py' -v
CLIP_JOBS_TEST_RUNTIME=/tmp/clip-jobs-reconciliation-check node --test tests/clip-jobs-reconciliation.test.mjs
npm run lint
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

The six Node tests and six Python tests pass. The callback module was unchanged.
TypeScript and full repository lint pass after minimal type/unused-variable
cleanup: caught errors are narrowed, the dashboard JSON result uses unknown,
and the unused setter/icon bindings are removed. No lint rules were weakened and
no unrelated features were refactored. The deployment runbook records the exact
four original errors and two warnings, their fixes, and the release file manifest.
The production build, lint, TypeScript, and existing tests now also pass under
Node 22.23.4 with npm 10.8.2. The project declares Node 22.x for local tools, CI,
and Vercel. Dependency versions are unchanged; the lockfile adds only root engine
metadata. The runbook records the confirmed runtime, safe side-by-side setup, and
remaining hosted-runtime/backup/maintenance gates.

All six PostgreSQL test groups pass using an isolated PGlite runtime in `/tmp`,
with synthetic Supabase roles, `auth.uid()`, and job data. They execute the actual
migration and read-only verification file, including empty-baseline reconciliation,
data preservation, populated-metadata reruns, tenant isolation, column ACLs,
worker-only RPC execution, claim/recovery/cap behavior, timestamp updates,
stale-attempt rejection, and atomic rollback for unsafe data, index collisions,
and inherited excessive grants. Eighteen test groups pass across all three
test files. No SQL was executed against Supabase.

The test runtime was downloaded separately and did not change package.json or
package-lock.json. To reproduce it, install `@electric-sql/pglite` into the
directory specified by `CLIP_JOBS_TEST_RUNTIME` (for example with
`npm install --prefix /tmp/clip-jobs-reconciliation-check --no-save --package-lock=false --ignore-scripts @electric-sql/pglite`).
PGlite provides one local PostgreSQL session; it does not verify competing
transactions, Supabase-managed role configuration, PostgREST result serialization,
or production cookie/session integration. Those staging checks remain required.

## Rollback and preservation

Any migration error rolls back schema, backfills, grants, policies, and functions
together. No partial reconciliation should remain. On a successful rerun, row
contents and populated timestamps/attempts are retained; table-specific checks,
policies, and function bodies are deliberately re-established.

The transaction-local snapshot asserts exact job membership and preservation of
pre-existing values, allowing only SQL NULL clips -> [] and missing updated_at
baselines. It disappears at commit and is not a rollback backup. NULL statuses,
ownerless rows, non-array clips, invalid numeric counts, unexpected metadata,
and unknown legacy processing leases are not silently rewritten.

After commit, pause submissions/workers again and prefer a reviewed forward fix.
Keep new columns and their populated retry/provenance metadata. Do not apply 001
as a down migration, drop columns/functions with dependent application code, or
restore a stale whole-table backup over newer results. Restore originally NULL
clips only for specific unchanged backed-up rows if a data reversal is necessary.
Restore prior schema/function definitions only from captured metadata and with
the corresponding application version. **Do not reopen the public ALL policy or
broad anon/authenticated grants as an operational rollback.** Keep enqueue paused
until a secure compatible route is available. No destructive down migration is
provided.

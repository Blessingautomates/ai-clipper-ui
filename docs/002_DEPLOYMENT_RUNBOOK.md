# Migration 002 deployment runbook

Prepared on 2026-11-07. This is an operator procedure for the existing database
reconciliation and companion code. No remote SQL, deployment, commit, push, or
production modification was performed in preparing it. Do not rerun the completed
14-check audit. The migration, verification SQL, queue API/worker changes, and
existing tests are reused unchanged. Minimal type and unused-variable fixes in
five frontend files clear the reproduced lint blockers without weakening rules.

## Deployment target

**Use the production sequence below: no separate staging environment was found
in the accessible local configuration.** This does not establish whether an
unlinked staging project exists in a hosted dashboard.

Only environment-variable names and local configuration file names were checked;
no configured values were displayed, compared, or changed:

| Location | Finding |
| --- | --- |
| Current process | No Supabase/staging/database variable names present. |
| `.env.local` | No Supabase/staging/database variable names present. |
| `.env.example` | Template names only: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. A template is not a configured environment. |
| Other local environment files | No additional environment files found outside dependency/build directories. |
| `.github/workflows/worker.yml` | References the ordinary `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` secret names; no staging environment or staging-specific Supabase names. References do not prove that hosted secrets are populated. |
| Supabase/Vercel local configuration | No local Supabase project/branch configuration or Vercel project linkage found. |

Use the existing production project's identity in the Supabase dashboard. Never
copy a key, database connection string, or token into SQL, this document, a test,
a ticket, a screenshot, or chat. Keep backup artifacts private as well.

If an already-existing separate staging project becomes available, apply steps
4–8 there first using that project's own credentials and isolated worker. It
must contain the existing audited table: 002 is a reconciliation, not a fresh
database bootstrap. A blank staging project or migration 001 is not a substitute
for the audited starting schema. Keep staging workers separate from production.
Otherwise continue with the production procedure; creating staging is not a
prerequisite added by this runbook.

## Confirmed compatibility and rerun behavior

| Application contract | Migration 002 contract |
| --- | --- |
| `/api/generate` verifies the cookie session with `auth.getUser()` and inserts only `user_id` and `video_url`; returns the job row | Authenticated column INSERT on these two fields, ownership WITH CHECK using `auth.uid()`, generated ID, pending/empty-clips/zero-attempt/timestamp defaults, ownership-filtered SELECT for the returned row. |
| Worker calls `claim_job()` with no arguments and accepts list results | `public.claim_job()` returns SETOF `public.clip_jobs`, zero or one row; atomic claim with `FOR UPDATE SKIP LOCKED`, deterministic ordering, attempt increment, start timestamp, error clearing. |
| Worker calls `reap_stale_jobs` with `stale_after = "30 minutes"`, `max_attempts = 3` | `public.reap_stale_jobs(interval, integer)` returns integer; same names/defaults, positive-argument validation, stale requeue/terminal cap. |
| Worker writes status, clips, transcript source, and errors; guards results with ID, processing status, and claimed attempts | Service-role SELECT plus UPDATE on these fields and the RPC-owned attempt/start fields only. Late results cannot overwrite a reclaimed attempt. |
| Queue security and timestamp handling | Exactly two authenticated ownership policies; no anon/PUBLIC table or column permissions; worker-only RPC EXECUTE; `SECURITY INVOKER`, fixed `pg_catalog` search path, qualified table references; one table-specific timestamp trigger. |
| Worker states, payload, ordering, and ownership | Four-state status CHECK, nonnegative attempts CHECK, clips-array CHECK, nonblank URL CHECK; existing UUID PK and CASCADE owner FK retained; indexes on `(status, created_at, id)`, `(user_id)`, and partial `(processing_started_at) WHERE status = 'processing'`. |

The complete migration and verification files were reviewed against the current
code. No compatibility edit was required. The auth callback's existing parsed
origin redirect validation stays unchanged.

Rerunning 002 is safe **under its guarded schema/data assumptions and with the
same maintenance pause**. Existing columns/indexes are reused after checks;
existing job IDs, owners, statuses, arrays, retry/provenance metadata, and populated
timestamps are preserved. Policies, four named checks, the dedicated trigger,
RPC bodies, and grants are deliberately re-established, so a rerun is not a
no-op and will replace later customizations to those objects. Unknown types,
RPC overloads/signatures, triggers, index conflicts, invalid rows, or excessive
inherited permissions cause an abort. Do not remove guards to make a rerun pass.
Do not rerun merely to clear a PostgREST cache delay.

## Ordered production procedure

### 1. Prepare the release and backup mechanism

Have the existing reviewed frontend and worker changes ready for deployment,
along with the existing callback fix. Identify the current frontend deployment
and worker revision so application rollback is possible. Confirm an operator
can publish the companion code while maintenance remains active.

Use the project's declared **Node 22.x LTS** for the frontend build and runtime:
the locked Supabase packages require `node >=22.0.0`. `.nvmrc` and `.node-version`
both select 22, package.json/package-lock.json declare `22.x`, and `.npmrc` enforces
engine compatibility during installs. CI reads `.nvmrc`; Vercel's Node setting must
match `22.x`. Prepare a successful production build from a
clean lockfile install (`npm ci`, retaining optional native dependencies) on the
target platform **before activating maintenance or applying migration 002**.
This is a build/readiness step, not permission to publish to production. Preserve
the existing package.json/package-lock.json versions; do not replace a clean
install with an unpinned dependency update to work around local native binaries.

There is no built-in maintenance flag in the current application. Use hosting
maintenance/access controls to block external job submissions, including direct
requests to `POST /api/generate`; hiding the form is insufficient. If no suitable
edge rule is available, use site-wide maintenance during this procedure. Stop
other known direct `clip_jobs` writers or integrations too. Prepare this control
before starting the maintenance window.

In Supabase, open the existing production project and **Database → Backups**.
Identify a supported on-demand backup, a usable point-in-time recovery mechanism,
or an operator-managed PostgreSQL backup that includes schema, jobs, policies,
ACLs, and function definitions. Obtain connection credentials privately through
the normal secured backup tooling if needed; this runbook contains none.

A row-count screenshot, audit output, SQL Editor query history, the migration's
TEMP snapshot, or a Table Editor CSV alone is not a restorable database backup.
If the hosted plan cannot provide a usable backup, take a secure full backup
with your existing PostgreSQL backup tooling before running the migration.
Do not proceed without a completed backup and a known recovery path.

### 2. Pause submissions and all workers

Activate the submission/site maintenance control. In GitHub Actions, open
**ToolStack Video Worker → workflow menu → Disable workflow**. This prevents
scheduled starts; separately cancel queued runs and let active processing finish.
Do not manually dispatch another run. Stop ad hoc/local workers and other queue
writers. Disabling a workflow does not stop an already-running job.

Prefer letting active jobs finish over terminating a runner: old processing rows
without retry metadata make 002 stop. If they cannot finish, keep maintenance
active and resolve that specific job separately; do not globally reset statuses,
delete jobs, or invent retry metadata. The already-completed audit reported zero
jobs, so this is a maintenance condition, not another audit request.

In Supabase SQL Editor, choose the production project's migration administrator
(normally `postgres`), not anon/authenticated/service_role. Run **SQL A** below
once to record maintenance totals. Require `processing_jobs = 0` for this first
rollout, then keep all writers paused.

### 3. Complete the mandatory backup at the maintenance boundary

Take the backup after the pause, or establish an available PITR recovery point
covering this paused state. Record its completion/recovery timestamp and the
SQL A total privately. For a backup file, confirm it is complete and readable
with the relevant restore tooling; for a managed backup/PITR point, confirm it
is available and that the restore procedure and required access are known.
Retain original audit evidence without editing it.

The backup must preserve pre-migration schema/ACL/function definitions and any
original SQL NULL clip values. Do not proceed on a backup still in progress,
an unavailable recovery point, or an unknown restore procedure.

### 4. Execute migration 002 as one transaction

In a new production SQL Editor query, paste **the complete, unchanged file**
[`migrations/002_clip_jobs_reconciliation.sql`](../migrations/002_clip_jobs_reconciliation.sql)
and run it once as **SQL B**. Do not run migration 001, the historical draft,
selected DDL fragments, or an autogenerated migration bundle that includes 001.

Wait for successful completion through the file's final `COMMIT;`. The file
sets five-second lock and sixty-second statement timeouts, takes an exclusive
table lock, checks compatibility, reconciles the schema and access, asserts
preservation, and notifies PostgREST to reload its schema at commit.

Retain the success/error result and old-policy NOTICEs privately. The exact
original policy expressions are captured by the migration before replacement;
you do not need to rerun the earlier policy audit.

On any error or uncertain completion, keep maintenance active and follow the
failure table below. Do not click Run repeatedly or bypass the checks.

### 5. Execute every final verification section

Run **SQL C**, the complete existing
[`docs/002_clip_jobs_verification.sql`](002_clip_jobs_verification.sql), in the
same production project as the migration administrator. This file is read-only
and does not invoke claim/reaper RPCs. Review every result set. If SQL Editor
shows only the final result, use the per-section procedure below; seeing a row
count alone does not verify security or schema.

Every `passed` value must be `true`. Inspect the displayed CHECK definitions,
function bodies/defaults, and policy expressions too; some checks establish
presence/signatures rather than independently proving the displayed body.
Require `invalid_jobs = 0` and total_jobs equal to the paused backup/SQL A total.
For the previously audited empty state, the expected total remains zero.

If anything fails or totals differ while writers are paused, stop before code
promotion or worker resumption. A successful migration is not a successful
rollout until verification passes.

### 6. Deploy the companion code while maintenance remains active

Include every file in the release manifest below; the five lint-cleanup files
must be included in the frontend build along with the queue API and callback.

Deploy the existing frontend release containing `app/api/generate/route.js` and
the preserved callback fix, and make the existing guarded `worker.py` available
to the GitHub Actions revision that will run. Keep the workflow disabled until
the new worker revision is confirmed. Its checkout uses the revision selected
by the workflow run; do not assume local uncommitted changes are available to a
hosted runner.

Through the normal private hosting/secrets interface, ensure the frontend has
`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and the worker has
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` for this same project. Never expose
their values. Preserve the existing analysis/transcription secret configuration.
The service-role secret stays worker-only and out of `NEXT_PUBLIC_*` or client
code. The local template names do not establish hosted secret readiness.

The old anonymous generation route is incompatible with the new grants. The
old worker lacks the late-attempt guard. Keep maintenance active if either
companion release is missing or its deployment fails. Require a successful
frontend production build/deployment before promoting it; the local checks below
did not include a hosted production build.

### 7. Perform one controlled smoke test before opening submissions

Keep general submissions blocked but allow designated test accounts/operator
access. Sign in through the deployed cookie flow; verify the callback stays on
the application origin. Submit one known test video through `POST /api/generate`
with JSON `{"videoUrl":"<test video URL>"}` using the signed-in browser session.
Do not send a service-role credential to the browser or choose ownership from
the request body. The returned job must belong to the verified user, with pending
status, empty clips, and zero attempts. An unauthenticated request must return
401. A different user's authenticated table read must not expose that job.

Once the guarded worker revision is available, enable **ToolStack Video Worker**
and dispatch a controlled run with `max_jobs = 1`. Enabling also permits scheduled
runs; retain `concurrency.group = clipper-worker` and `cancel-in-progress = false`,
and do not start another ad hoc worker. The configured run is capped at 55 minutes
with a 50-minute between-job budget. A missing-RPC/schema-cache message is a
failure even if the workflow exits successfully: the worker catches those errors.

Confirm the designated job transitions to processing with an incremented attempt
and start timestamp, then completed with analysis clips/transcript source or a
meaningful failed status. A failed analysis does not satisfy the end-to-end success
gate. Confirm allowed worker writes succeed and owned results remain isolated.
Repeat SQL C section 13 after the test: total_jobs can now increase; invalid_jobs
must remain zero. These smoke-test writes are performed by the operator only;
none were performed in preparing this runbook.

If the smoke test fails, disable the workflow again, wait for any active attempt
to finish, and keep submissions in maintenance. Use the failure table. Do not
delete production jobs or requeue all jobs to make the smoke test green.

### 8. Resume and observe

When schema verification, both deployments, and the controlled smoke test pass,
remove the general submission maintenance control and leave the serialized
worker workflow enabled. Watch the first queued jobs for RPC availability,
permission errors, completed/failed results, and retry behavior. Keep the backup
and deployment record through the rollout observation period.

This releases the secure database/analysis queue foundation. The current
dashboards are not connected to this queue and the worker does not render/store
downloadable video files. Queue verification does not establish those product
features as launched; use the API/session smoke test for this deployment.

## Exact SQL to execute

The only mandatory SQL units are A, B, and C, in that order. D is an error-cleanup
command only. All are run manually by the operator in the selected Supabase project.

### SQL A — paused maintenance totals, before the backup/migration

This is not a repeat of the 14-check audit and exposes no job URLs, IDs, or users.

```sql
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
SELECT count(*) AS total_jobs,
       count(*) FILTER (WHERE status = 'processing') AS processing_jobs
FROM public.clip_jobs;
COMMIT;
```

### SQL B — migration, complete file only

Execute every statement in
[`migrations/002_clip_jobs_reconciliation.sql`](../migrations/002_clip_jobs_reconciliation.sql),
starting at its `BEGIN;` and ending at its final `COMMIT;`. The original comments
may be pasted too. The full file is the exact SQL unit, not the descriptions below;
no replacement SQL or duplicated migration is introduced by this runbook.

Its internal order must remain unchanged:

1. BEGIN, timeouts/search path, exclusive lock, `$preflight$` checks.
2. Transaction-local preservation snapshot, missing columns, bounded backfills,
   nullable/default corrections and four CHECK definitions.
3. Three indexes and `$indexes$` validation.
4. Timestamp function/trigger and the two worker RPC definitions/EXECUTE grants.
5. RLS, `$policies$` replacement, table/column revocation and minimal grants.
6. `$preservation$` assertions, `NOTIFY pgrst, 'reload schema';`, COMMIT.

Do not execute these six stages as separate transactions. The exclusive lock
and guard/preservation assertions protect the complete change together.

### SQL C — final verification, all 13 existing sections

Execute every statement in
[`docs/002_clip_jobs_verification.sql`](002_clip_jobs_verification.sql), starting
at `BEGIN READ ONLY;` and ending at `COMMIT;`.

For a SQL Editor that exposes only one result set, execute each numbered section
individually in order: copy all statements between its `-- N.` heading and the
next numbered heading (or the final COMMIT for section 13). Prepend these exact
two statements and append the exact COMMIT below. Do not copy partial CTEs or DO
blocks, and do not append the source file's COMMIT twice.

```sql
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
```

Append after the complete section's SQL:

```sql
COMMIT;
```

Sections 6, 9, and 10 each contain two SELECT statements. Run both; if necessary,
give each complete statement its own read-only wrapper to view both outputs.

| Existing section | Required output |
| --- | --- |
| 1 | Two true checks: ordinary table/RLS and Supabase role assumptions. |
| 2 | Eleven columns, all passed=true; exact type, nullability, and default expressions. |
| 3 | Four validated CHECKs; inspect four-state status, attempts >= 0, JSON array, nonblank URL definitions. |
| 4 | PK and validated CASCADE FK checks both true. |
| 5 | Three valid/ready indexes, all passed=true with the exact columns/predicate. |
| 6 | Exactly authenticated SELECT-own and INSERT-own policies using/checking `((SELECT auth.uid()) = user_id)`, plus passed=true. No public/anon/ALL policy. |
| 7 | Three role rows; authenticated/service SELECT true; table INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER false for all. All passed=true. |
| 8 | Thirty-three column/role rows. Authenticated INSERT only user_id/video_url; service UPDATE only six queue/result fields; anon none. All passed=true. |
| 9 | Internal FK triggers retained; exactly one enabled BEFORE UPDATE row timestamp trigger, passed=true. |
| 10 | Three function signatures/bodies; all SECURITY INVOKER with `search_path=pg_catalog`. Claim is SETOF clip_jobs, reaper integer with arguments `stale_after`, `max_attempts` and defaults 30 minutes/3, trigger returns trigger and sets NEW.updated_at. No overloads, passed=true. |
| 11 | Nine role/function rows; only service_role can execute the two RPCs; none of these app roles can directly execute the trigger function. All passed=true. |
| 12 | No PUBLIC table/column/function grants, passed=true. |
| 13 | invalid_jobs=0; total_jobs matches SQL A/backup before the smoke test. |

### SQL D — clear a failed transaction only

If the migration or a verification transaction reports an error and leaves the
session aborted, execute this separately in that session:

```sql
ROLLBACK;
```

This clears/rolls back an uncommitted transaction. It does **not** undo a
successfully committed migration. Do not run claim/reaper RPC SQL as a verification
shortcut; those functions mutate jobs.

## Failure, rollback, and rerun decisions

The existing reconciliation document's rollback advice is accurate with this
distinction: a PostgreSQL error aborts the migration transaction, preventing a
partial commit, but the session may still need SQL D. A completed COMMIT requires
a forward correction or a carefully scoped backup-based reversal, not ROLLBACK.

| Condition | Required action |
| --- | --- |
| Missing/unusable backup or incomplete pause | Do not execute SQL B. Complete the backup/pause prerequisites. |
| Five-second lock timeout | Run SQL D if needed, keep maintenance active, let remaining readers/writers finish, then retry the unchanged entire file once the cause is resolved. Do not kill unrelated sessions. |
| Statement timeout, schema/data guard, conflicting index/RPC/trigger, or inherited ACL error | Run SQL D if needed; preserve the error and keep maintenance active. No partial migration can commit. Resolve the specific cause through a reviewed correction; do not weaken guards or mass-edit jobs. |
| Connection/UI result lost during execution | Treat commit state as unknown. Open a fresh administrator SQL Editor query and run SQL C. If it passes, treat 002 as committed; do not restore the old schema. If it fails or shows a mixed/unexpected state, keep maintenance active and resolve the specific state before retrying. |
| SQL B committed but SQL C fails | SQL D cannot reverse the commit. Keep maintenance active and use a reviewed forward correction. Restore from backup only if necessary and after preserving any later valid writes. |
| SQL C passes but PostgREST reports an absent column/RPC | Allow the committed reload notification to take effect and retry the controlled API check. If it persists, investigate the schema-cache/API exposure issue; do not rerun 002 or bypass ACLs solely to clear the cache. |
| Frontend/worker deployment fails | Leave submissions paused and worker disabled. Retry the compatible release. Keep the secure database schema; the old anonymous route is not a working rollback. |
| Smoke test exposes permission/auth/result problems | Reapply maintenance, disable queued starts, let active processing finish, retain the test job/error, and fix the specific failure before opening submissions. |
| Application version must be rolled back after commit | Keep enqueue paused. Revert only to a version compatible with 002 or ship a corrected compatible version; do not reactivate the old unguarded worker or anonymous insertion route. |
| Database reversal is necessary after commit | Pause all writers, take a new backup, account for post-migration writes, and use captured definitions for a scoped correction. Keep populated new columns; do not restore stale whole-table data over newer results or apply 001 as a down migration. |

### Destructive changes and limits of reversal

002 contains no DELETE/TRUNCATE, DROP TABLE, DROP COLUMN, owner reassignment, or
reset of existing statuses. The existing auth-user deletion CASCADE behavior is
retained; no auth user is deleted by the migration.

These operations replace existing definitions/access and are intentional:

- DROP/recreate all policies on this table; revoke table and independent column
  privileges, then grant minimal permissions. Previous definitions/ACLs are lost
  from the live catalog after commit unless retained in the backup. A rerun also
  replaces policy/ACL customizations made after the first application.
- DROP/recreate four named CHECK constraints and the dedicated timestamp trigger;
  CREATE OR REPLACE the dedicated trigger function and two compatible queue RPC
  bodies. No CASCADE object drops are used. Unknown signatures/triggers stop the
  migration rather than being dropped.
- SQL NULL clips become `[]`, losing the original NULL distinction after commit
  without a backup. Missing updated_at receives a created_at baseline, and newly
  added attempts defaults to zero; these do not recover historical information.
- Temporary preservation data is dropped at commit. It was an assertion snapshot,
  not a durable backup. Index creation and schema changes are transactional but
  hold an exclusive table lock during the change.

Do not reopen the broad public ALL policy or excessive anon/authenticated grants
as a rollback. Restore originally NULL clips only for specific backed-up rows
proven unchanged since migration. There is no automatic destructive down migration.

## Release file manifest

Deploy the full frontend build containing these seven application files, plus
the tested worker revision. Do not replace the existing auth helpers or leave
the callback fix out of the release.

| Deployment | Application file | Required change |
| --- | --- | --- |
| Frontend | `app/api/generate/route.js` | Verified cookie ownership and database-default-only enqueue. |
| Frontend | `app/auth/callback/route.js` | Preserve the existing parsed-origin open-redirect fix and cookie exchange. |
| Frontend | `app/api/process-video/route.ts` | Narrow caught errors before reading their messages. |
| Frontend | `app/dashboard/page.tsx` | Unknown JSON display state and narrowed caught errors. |
| Frontend | `app/dashboard/page.js` | Remove unused state-setter binding. |
| Frontend | `app/login/page.tsx` | Narrow caught authentication errors. |
| Frontend | `app/page.js` | Remove unused icon import. |
| Worker | `worker.py` | Guard success/failure writes by the claimed attempt and processing state; use the required queue RPCs. |

The frontend retains its existing `utils/supabase/server.ts` and
`utils/supabase/client.ts` dependencies. The worker checkout retains
`transcript.py`, `requirements.txt`, and `.github/workflows/worker.yml`; keep
workflow serialization and worker-only secret configuration. These supporting
files are unchanged but must remain available in their normal deployment.
SQL/documentation/test files are deployment instructions and validation assets,
not additional browser code or worker entry points.

Include the runtime declarations `package.json`, `package-lock.json`, `.nvmrc`,
`.node-version`, and `.npmrc` in the frontend release checkout. Keep
`.github/workflows/validate.yml` for Node 22 build/test CI and the updated
`.github/workflows/worker.yml` helper-action versions with the worker release.
Application dependency versions are unchanged; the lockfile change is only the
root project's engine metadata.

## Node 22 runtime setup and validation

### Runtime declarations and assumptions

| Location | Declared or assumed runtime |
| --- | --- |
| Current system installation | Node 20.20.2, npm 10.8.2 at `/usr/bin`; retained for existing tools. |
| Verified side-by-side application runtime | Node 22.23.4 at `/tmp/toolstack-node22/node_modules/node-linux-arm64/bin/node`, using existing npm 10.8.2. Temporary runtime, not a global replacement. |
| package.json / root package-lock metadata | `engines.node = "22.x"`; lowest dependency-compatible major is 22, minimum 22.0.0. Use current patched 22.x rather than installing obsolete 22.0.0. |
| `.nvmrc`, `.node-version` | Both `22`; select the same LTS major for supported local version managers. |
| `.npmrc` | `engine-strict=true`; prevents a dependency install from silently accepting Node 20 or a different undeclared major. |
| Locked Supabase 2.116.0 packages | Require >=22.0.0; unchanged. Next 16.3.4 requires >=20.9.0, so Supabase sets the project's minimum. |
| `@types/node` ^20 | Type declarations only, not a runtime selector. Existing code does not require new Node 22 APIs; no typing/dependency upgrade is needed. |
| Vercel | No local project linkage or vercel.json existed. The package engine selects application major 22; confirm Project Settings → Build and Deployment → Node.js Version is `22.x` at release time. No remote setting was changed. Vercel manages the patch within that major. |
| `.github/workflows/validate.yml` | `actions/setup-node@v6` reads `.nvmrc`; npm ci, lint, types, existing offline tests, isolated PostgreSQL tests, and build. No deployment, remote SQL, or application secrets are used. |
| `.github/workflows/worker.yml` | Application worker remains Python 3.11. Checkout/setup-python v6 use Node 24 for GitHub's action internals; they do not select the Next.js application runtime. Legacy Node 20 helper-action versions were replaced. |
| next.config.ts / deployment docs | No separate Node override. Follow package engines and the declarations above. No Dockerfile, other build/runtime declaration, or hidden local Vercel linkage was found. |

The exact local test runtime is 22.23.4; CI and Vercel select supported patched
releases within major 22. Changing to a later major is a separate coordinated
update to these declarations. npm 10.8.2 is compatible with Node 22 and lockfile
version 3, so npm and application dependencies were not upgraded.

### Use the already-available Node 22 in this Ubuntu session

Run these commands inside the current Ubuntu/proot environment, not native
Android Termux. The parentheses scope PATH changes to this project shell and
leave `/usr/bin/node`, `/usr/bin/npm`, and `/usr/bin/codex` untouched:

```sh
cd /data/data/com.termux/files/home/ai-clipper-ui
(
  export PATH="/tmp/toolstack-node22/node_modules/node-linux-arm64/bin:$PATH"
  node --version
  npm --version
  npm run dev
)
```

The verified outputs are `v22.23.4` and `10.8.2`. Stop the dev server with Ctrl-C
to leave the subshell. The temporary runtime can disappear after a restart; for
a persistent side-by-side install, use the next procedure. Do not reinstall or
uninstall Codex CLI or replace system Node as part of this project migration.

### Persistent Ubuntu ARM64 setup without changing system Node

This environment is Ubuntu 26.04 LTS, ARM64, glibc 2.43, and can execute the
Linux ARM64 Node 22 binary. These commands install the same tested runtime into
a separate user directory. They do not change shell startup files or global npm
packages. Bootstrap outside the project so its stricter engine declaration does
not interfere with the existing system npm:

```sh
cd /tmp
npm install --prefix "$HOME/.local/share/toolstack-node22" --no-save --package-lock=false --ignore-scripts --no-audit --no-fund node-linux-arm64@22.23.4
cd /data/data/com.termux/files/home/ai-clipper-ui
(
  export PATH="$HOME/.local/share/toolstack-node22/node_modules/node-linux-arm64/bin:$PATH"
  node --version
  npm --version
  npm ci
  npm run dev
)
```

Use the same scoped PATH for build/test commands. A conventional nvm/fnm/asdf
installation may instead read `.nvmrc` or `.node-version`; keep changes local to
the project shell. The existing Codex CLI package declares Node >=16, and its
system installation was neither replaced nor moved.

### Enter Ubuntu safely from native Termux

Do not run Linux/glibc Node binaries directly in native Termux's Android/bionic
environment, and do not replace native Node with an Ubuntu apt package. Use the
existing Ubuntu distro for this project while keeping the native Codex CLI and
Termux Node installation intact:

```sh
proot-distro login ubuntu --shared-tmp
```

Then run the Ubuntu setup above. If Ubuntu is not already installed, first run
these commands in native Termux, only for the missing packages/distro:

```sh
pkg install proot-distro
proot-distro install ubuntu
proot-distro login ubuntu --shared-tmp
```

Do not reinstall an existing distro. There is no need for a global `pkg upgrade`,
a NodeSource apt replacement, or removal of an existing Node/Codex installation.
`pkg install nodejs-lts` may select another LTS major, so it is not the pinned
Node 22 setup used to validate this release.

### Node 22 release validation commands

Use the scoped Node 22 PATH shown above before running:

```sh
npm ci
npm run lint
node node_modules/typescript/bin/tsc --noEmit --incremental false
node --experimental-vm-modules --test tests/generate-route.test.mjs
python3 -m unittest discover -s tests -p 'test_worker_queue.py' -v
npm install --prefix /tmp/clip-jobs-reconciliation-check --no-save --package-lock=false --ignore-scripts --no-audit --no-fund @electric-sql/pglite@0.5.15
CLIP_JOBS_TEST_RUNTIME=/tmp/clip-jobs-reconciliation-check node --test tests/clip-jobs-reconciliation.test.mjs
npm run build
git diff --check
```

The isolated test runtime does not change application dependencies. Never run
the worker CLI queue as a local validation command. PostgreSQL tests use only
synthetic in-memory data. CI implements the same checks; it has not been run
remotely because this work was not committed or pushed.

## Local readiness record

The complete validation was rerun using **Node 22.23.4 / npm 10.8.2** after the
runtime declarations were added. All JavaScript commands below ran with the
scoped Node 22 PATH; the Python worker tests remain Python tests. No application
code, tests, migration SQL, or verification SQL changed in this runtime follow-up.
The system Node 20.20.2 and existing Codex CLI installation remain untouched.

| Command | Result |
| --- | --- |
| `node --experimental-vm-modules --test tests/generate-route.test.mjs` | PASS, 6 tests, including ownership and callback redirect regression. |
| `python3 -m unittest discover -s tests -p 'test_worker_queue.py' -v` | PASS, 6 tests, including current/stale attempt writes. |
| `CLIP_JOBS_TEST_RUNTIME=/tmp/clip-jobs-reconciliation-check node --test tests/clip-jobs-reconciliation.test.mjs` | PASS, 6 PostgreSQL test groups: actual migration/verification, RLS/ACLs/RPCs, preservation/reruns, and atomic failure conditions. Synthetic in-memory database only. |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | PASS. |
| `npm run lint` | PASS: zero errors and zero warnings. No rule changes or suppressions. |
| `npm ci` | PASS under Node 22; 438 locked packages installed. Only an existing ESLint package deprecation notice; no Node-engine compatibility warnings. |
| `npm run build` | PASS under Node 22.23.4, including compilation, build-time TypeScript, prerendering, and tracing. No Node 20/Supabase compatibility warnings. |
| `git diff --check` | PASS. |

The initial full lint run reproduced these exact findings; all are now resolved:

| Original location | Rule / finding | Minimal fix |
| --- | --- | --- |
| `app/api/process-video/route.ts:134:19` | `no-explicit-any`, catch annotation | Remove explicit any; narrow with instanceof Error. |
| `app/dashboard/page.tsx:9:40` | `no-explicit-any`, response state | Use unknown and conditional rendering for the JSON display. |
| `app/dashboard/page.tsx:40:19` | `no-explicit-any`, catch annotation | Remove explicit any; narrow with instanceof Error. |
| `app/login/page.tsx:56:19` | `no-explicit-any`, catch annotation | Remove explicit any; narrow with instanceof Error. |
| `app/dashboard/page.js:12:17` | `no-unused-vars`, setClips | Remove the unused setter binding while retaining the state. |
| `app/page.js:2:20` | `no-unused-vars`, Video | Remove only the unused icon import. |

Runbook SQL order was reviewed against both complete SQL files: A is the paused
maintenance count; B executes 002 once from BEGIN through COMMIT; C executes
verification sections 1–13 in their source order inside read-only transactions;
D is only failed-transaction cleanup. The six internal migration stages and all
13 verification expectations above match the actual files. No SQL order edits
were needed.

The earlier Node 20 build required restoration of a missing Linux ARM64 Lightning
CSS binding. The current clean install and complete production build succeeded
under Node 22.23.4. Dependency versions/graph are unchanged; package.json and the
lockfile changed only to declare the root Node engine. ESLint rules and TypeScript
configuration are unchanged. Runtime declarations and both workflow YAML files
were also checked locally for consistency and parsed successfully.

An initial standalone TypeScript command overlapped the build and reported missing
generated `.next/types` files as Next.js replaced them. The sequential check after
build completion passed without source or compiler-rule changes. Do not run these
two commands concurrently; the new CI workflow runs them sequentially.

The local PostgreSQL runtime proves execution and single-session behavior, not
remote Supabase configuration, PostgREST/cookie integration, concurrent sessions,
or a hosted production build. Steps 5–7 supply the deployment-specific verification.

## Final deployment checklist

1. Prepare the tested release on declared Node 22.x, using the locked dependencies and all
   application files in the release manifest. Confirm its production build before
   activating maintenance; do not promote the old anonymous route or unguarded worker.
2. In the existing Supabase production project, arrange a restorable backup/recovery
   mechanism. Pause submissions and all writers/workers, record SQL A, and complete
   the backup/recovery point for that paused state.
3. In that project's SQL Editor as the migration administrator, execute the complete
   unchanged migration 002 (SQL B) once. Do not run 001 or the draft. Retain results/
   policy NOTICEs. Use SQL D only to clear a failed uncommitted transaction.
4. Execute all 13 SQL C verification sections. Require every passed=true, inspect
   definitions/defaults, require invalid_jobs=0 and unchanged maintenance totals.
   Keep maintenance active on any error, uncertain state, or mismatch.
5. Deploy the full frontend and guarded worker releases from the manifest while
   maintenance remains active. Keep existing auth helpers and worker dependencies;
   verify hosted public-key/worker-only secret configuration privately for this
   project and preserve callback security and worker serialization.
6. Perform the designated signed-in enqueue/worker/owned-result smoke test and
   cross-user isolation check. Recheck SQL C section 13 after the test. Resume
   submissions/workflow only after success; retain the backup and observe initial jobs.

These production operations remain outstanding; local checks did not apply the
migration or validate the hosted Node version, Supabase deployment, or smoke test.
The local Node 22 release-build gate is now satisfied. Confirm the hosting project
will use `22.x` when this release is promoted and retain the new runtime/CI files;
then proceed to the backup and maintenance sequence. The local build does not
claim that Vercel settings or remote CI have already been changed or verified.

## Exact next action in Supabase

After preparing the supported release in step 1, open the **existing production
project → Database → Backups** and arrange a
completed restorable backup/recovery point for the paused maintenance state.
Then, after steps 1–3, open **SQL Editor as the migration administrator** and
execute SQL B, the whole unchanged migration 002, followed by every SQL C
verification section. No repeat of the earlier audit is required.

# Proposed migration 002: review and rollback

Historical draft, superseded by the completed
[migration 002 documentation](../002_clip_jobs_reconciliation.md) and
[runnable migration](../../migrations/002_clip_jobs_reconciliation.sql).
The original SQL draft remains guarded and unchanged. The text below records
the earlier audit-stage plan; it is not the current application sequence.

Draft only. No SQL has been executed. The SQL file stays in `docs/drafts/` and
contains an unconditional execution guard. Do not promote it to `migrations/`
or remove that guard before the inspection results and final change are approved.

## Evidence and scope

Confirmed live on 2026-11-06:

- `public.clip_jobs` exists; RLS is enabled and not forced.
- `attempts`, `processing_started_at`, `transcript_source`, `updated_at` are absent.
- `status` and `clips` permit NULL.
- `clip_jobs_pkey` and `clip_jobs_user_id_fkey` exist.
- The ownership foreign key uses `ON DELETE CASCADE`.

The SQL proposes the four columns, default/NOT NULL corrections, array/status/
attempt checks, and an isolated updated-at trigger. It preserves the CASCADE
foreign key, existing RLS, IDs, owners, statuses, URLs, errors, timestamps and
non-NULL clip arrays. Older clip object fields are retained.

This is the confirmed schema portion of 002, not a complete deployment migration.
Indexes, current RPC signatures/bodies/ACLs, triggers, policies, grants and record
counts still need the single consolidated inspection script's results.

## Decisions before finalization

1. Check live types/defaults, check definitions and trigger side effects. The draft
   stops on mismatched types, pre-existing proposed columns/checks/functions or
   any non-internal table trigger; it does not overwrite unknown live objects.
2. If NULL statuses exist, approve a per-job disposition based on evidence.
   Do not map them all to pending. Keep 002 blocked until resolved; stage a
   nullable transition if records cannot yet be classified.
3. Resolve existing processing jobs explicitly or let legitimate work finish.
   New timestamps remain NULL rather than inventing processing history.
   The draft refuses to strand existing processing rows with unknown leases.
4. Approve SQL NULL clips -> empty arrays. No non-NULL payload is normalized,
   replaced or discarded. Non-array payloads stop the draft for separate review.
5. Accept `attempts = 0` and `updated_at = created_at` (or migration time when
   absent) as legacy baselines, not reconstructed history.
6. Use inspected definitions to add/reconcile a valid queue index, worker-only
   claim/recovery RPCs and their grants. Do not drop/recreate unknown RPCs or
   replace shared trigger functions. Align recovery thresholds with real job
   duration and guarded worker updates.
7. Reconcile all RLS policies and effective grants with authenticated ownership.
   Coordinate the request-scoped creation-route fix with that deployment:
   the present /api/generate does not transfer the caller's authenticated session.
   Inspect INSERT column privileges; ownership alone does not protect initial
   worker-managed state.
8. Preserve CASCADE in 002. A later retention/deletion decision must coordinate
   job history and source/output object cleanup; do not change it to SET NULL
   just because migration 001 says so.

## Data-preservation checks and execution conditions

- Capture the audit outputs for types/defaults, constraints, indexes, triggers,
  function definitions/owners/ACLs, policies and role privileges.
- Before any approved execution, obtain and verify a secure backup of full jobs
  and database definitions. Preserve IDs and original NULL values in that backup.
  The read-only script's redacted summaries are not a restorable backup.
- Stop new submissions and all workers during the short transaction. The draft
  acquires an exclusive table lock with a five-second lock timeout.
- Rehearse on a separate staging database restored from the snapshot.
- The draft's transaction-local full-row snapshot checks exact ID membership and
  every existing field, allowing only the approved NULL-clips -> [] change.
- An exception rolls back the transaction. Validate schema/checks, unchanged
  counts/ownership/payloads, tenant isolation and queue transitions in staging.
- Do not run production RPC tests without separate approval: claims/recovery
  and end-to-end acceptance tests write records.

## Rollback strategy

Before COMMIT: use ROLLBACK. Schema, trigger and backfill changes are transactional;
any raised exception aborts them. Resolve an aborted SQL Editor transaction with
ROLLBACK before another manual operation.

After COMMIT: quiesce submissions/workers again and preserve a fresh backup.
Prefer a reviewed forward correction retaining the new columns and any new data.

If behavior must be reversed:

1. Disable/remove only the newly introduced trigger and checks after reviewing
   dependencies; do not remove pre-existing constraints, RPCs or policies.
2. Restore original status/clips nullability and defaults from the captured
   metadata, rather than assuming their old defaults were absent.
3. Restore originally NULL clips only for specific backed-up rows proven unchanged
   since migration. Do not overwrite newer results or restore a whole stale
   table. Full rollback of these values requires the durable pre-change backup;
   the TEMP verification snapshot disappears at COMMIT.
4. Keep the four new columns and their values. Dropping them would discard retry
   and transcript metadata and can break worker/RPC dependencies.
5. If final 002 later includes RPC/policy/grant changes, add exact reverse
   definitions from the captured live baseline before approving that final draft.

No destructive down-migration is provided. Do not reset/delete jobs, assign
unknown owners, or restore a backup over subsequent valid writes.

## Next implementation sequence toward rendered downloads

1. Run the consolidated read-only script manually and return its result sets.
   Finalize 002 against those results, test it in staging, then request separate
   approval for production execution.
2. Make /api/generate use the existing cookie-backed server client, validate the
   user, derive user_id on the server, and return a job ID. Preserve the callback.
3. Wire the existing dashboard to authenticated source upload, job submission,
   ownership-filtered progress/results and retry/error display. Resolve the two
   dashboard page files deliberately; retain the existing product UI.
4. Add private source storage and an owned object reference. Workers must obtain
   authorized access to the source; expiring upload URLs cannot be durable input.
   Specify upload/storage/artifact fields in a later reviewed migration.
5. Extend the worker to download the source video and render real clip files
   with FFmpeg, including agreed framing/subtitle behavior. Analysis JSON is
   intermediate output. Mark product completion only after artifacts are stored.
6. Store per-clip output object references and expose authorized playback and
   signed downloads. Do not persist expiring download URLs as the artifact record.
7. Verify one real upload -> job -> analysis -> rendered video -> owned download,
   plus cross-user isolation, render failure, retries and interrupted-worker
   recovery. No mock clip or transcript-only success qualifies as completion.

No application or existing migration changes are authorized by this documentation
task. Each next implementation change needs a concrete reviewed scope.

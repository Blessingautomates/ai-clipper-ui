-- clip_jobs: job queue for the clipper worker.
--
-- Run this in the Supabase SQL editor. It is written to be idempotent, because
-- clip_jobs very likely ALREADY EXISTS (the worker has been writing to it).
-- `create table if not exists` alone would silently leave an existing table
-- without the new columns, so every column is also added via
-- `alter table ... add column if not exists`.

-- ---------------------------------------------------------------------------
-- 1. Table
-- ---------------------------------------------------------------------------

create table if not exists public.clip_jobs (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references auth.users (id) on delete set null,
  video_url     text not null,
  status        text not null default 'pending',
  clips         jsonb not null default '[]'::jsonb,
  error_message text,
  created_at    timestamptz not null default now()
);

alter table public.clip_jobs add column if not exists user_id               uuid references auth.users (id) on delete set null;
alter table public.clip_jobs add column if not exists video_url             text;
alter table public.clip_jobs add column if not exists status                text not null default 'pending';
alter table public.clip_jobs add column if not exists clips                 jsonb not null default '[]'::jsonb;
alter table public.clip_jobs add column if not exists error_message         text;
alter table public.clip_jobs add column if not exists created_at            timestamptz not null default now();
alter table public.clip_jobs add column if not exists updated_at            timestamptz not null default now();
-- Phase 1 additions:
alter table public.clip_jobs add column if not exists transcript_source     text;
alter table public.clip_jobs add column if not exists attempts              int not null default 0;
alter table public.clip_jobs add column if not exists processing_started_at timestamptz;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'clip_jobs_status_check') then
    alter table public.clip_jobs
      add constraint clip_jobs_status_check
      check (status in ('pending', 'processing', 'completed', 'failed'));
  end if;
end $$;

create index if not exists clip_jobs_status_created_at_idx
  on public.clip_jobs (status, created_at);

-- ---------------------------------------------------------------------------
-- 2. updated_at maintenance
-- ---------------------------------------------------------------------------

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists clip_jobs_touch_updated_at on public.clip_jobs;
create trigger clip_jobs_touch_updated_at
  before update on public.clip_jobs
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- 3. claim_job(): atomic, race-free job claim
-- ---------------------------------------------------------------------------
--
-- worker.py previously did `select ... limit 1` and updated the row later, so two
-- overlapping cron runs (every 5 minutes) could both claim the same job.
-- FOR UPDATE SKIP LOCKED makes concurrent claims safe: a second worker skips a
-- row the first has locked instead of blocking on it.
--
-- Returns a single clip_jobs row, or NULL when the queue is empty, so in Python
-- `supabase.rpc("claim_job").execute().data` is a dict or None.

-- `drop` before `create or replace`: if an earlier attempt left a claim_job()
-- with a different return type, `create or replace` would fail outright
-- ("cannot change return type of existing function") rather than replacing it.
drop function if exists public.claim_job();

create or replace function public.claim_job()
returns public.clip_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed public.clip_jobs;
begin
  select * into claimed
    from public.clip_jobs
   where status = 'pending'
   order by created_at
   for update skip locked
   limit 1;

  if claimed.id is null then
    return null;
  end if;

  update public.clip_jobs
     set status                = 'processing',
         processing_started_at = now(),
         attempts              = claimed.attempts + 1,
         error_message         = null
   where id = claimed.id
  returning * into claimed;

  return claimed;
end;
$$;

-- Only the worker (service role) may claim. Without this, any authenticated user
-- could call claim_job() through PostgREST and pull someone else's job.
revoke all on function public.claim_job() from public;
revoke all on function public.claim_job() from anon, authenticated;
grant execute on function public.claim_job() to service_role;

-- ---------------------------------------------------------------------------
-- 4. reap_stale_jobs(): recover jobs orphaned by a killed runner
-- ---------------------------------------------------------------------------
--
-- The GitHub Actions runner is SIGKILLed when it exceeds its timeout, so no
-- `except` block runs and the row is left in 'processing' forever. This resets
-- stale rows to 'pending' for another attempt, and fails them once they have
-- burned through max_attempts. Returns rows affected.

drop function if exists public.reap_stale_jobs(interval, int);

create or replace function public.reap_stale_jobs(
  stale_after  interval default '20 minutes',
  max_attempts int      default 3
)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  reaped int;
begin
  update public.clip_jobs
     set status                = case when attempts >= max_attempts then 'failed' else 'pending' end,
         error_message         = case when attempts >= max_attempts
                                      then coalesce(error_message, 'Abandoned after ' || attempts || ' attempt(s)')
                                      else error_message
                                 end,
         processing_started_at = null
   where status = 'processing'
     and processing_started_at is not null
     and processing_started_at < now() - stale_after;

  get diagnostics reaped = row_count;
  return reaped;
end;
$$;

revoke all on function public.reap_stale_jobs(interval, int) from public;
revoke all on function public.reap_stale_jobs(interval, int) from anon, authenticated;
grant execute on function public.reap_stale_jobs(interval, int) to service_role;

-- ---------------------------------------------------------------------------
-- 5. Row level security
-- ---------------------------------------------------------------------------
--
-- The worker connects with the service-role key, which bypasses RLS entirely.
-- Browser clients get read/insert on their own rows only.

alter table public.clip_jobs enable row level security;

drop policy if exists "clip_jobs_select_own" on public.clip_jobs;
create policy "clip_jobs_select_own"
  on public.clip_jobs for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists "clip_jobs_insert_own" on public.clip_jobs;
create policy "clip_jobs_insert_own"
  on public.clip_jobs for insert
  to authenticated
  with check (auth.uid() = user_id);

-- Deliberately no UPDATE or DELETE policy for browser clients: job state is
-- owned by the worker.

-- ---------------------------------------------------------------------------
-- 6. Ask PostgREST to pick up the new functions/columns without a restart.
-- ---------------------------------------------------------------------------

notify pgrst, 'reload schema';

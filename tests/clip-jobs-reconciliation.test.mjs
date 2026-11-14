import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import test from 'node:test';

// PGlite runs PostgreSQL in memory, with synthetic roles/data. No connection
// string, Supabase client, network request, or production credential is used.
// The runtime may be installed separately in /tmp; no project dependency change.
const runtimeRequire = process.env.CLIP_JOBS_TEST_RUNTIME
  ? createRequire(path.join(process.env.CLIP_JOBS_TEST_RUNTIME, 'package.json'))
  : createRequire(import.meta.url);
const { PGlite } = runtimeRequire('@electric-sql/pglite');
const migration = await readFile(new URL('../migrations/002_clip_jobs_reconciliation.sql', import.meta.url), 'utf8');
const verification = await readFile(new URL('../docs/002_clip_jobs_verification.sql', import.meta.url), 'utf8');
const ownerA = '00000000-0000-0000-0000-000000000001';
const ownerB = '00000000-0000-0000-0000-000000000002';

async function baseline() {
  const db = new PGlite();
  await db.exec(`
    CREATE ROLE anon NOLOGIN;
    CREATE ROLE authenticated NOLOGIN;
    CREATE ROLE service_role NOLOGIN BYPASSRLS;
    CREATE SCHEMA auth;
    CREATE TABLE auth.users (id uuid PRIMARY KEY);
    INSERT INTO auth.users VALUES ('${ownerA}'), ('${ownerB}');
    CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
      $$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    GRANT USAGE ON SCHEMA public, auth TO anon, authenticated, service_role;
    CREATE TABLE public.clip_jobs (
      id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
      user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
      video_url text NOT NULL,
      status text DEFAULT 'pending', clips jsonb DEFAULT '[]'::jsonb,
      error_message text, created_at timestamptz NOT NULL
    );
    ALTER TABLE public.clip_jobs ENABLE ROW LEVEL SECURITY;
    -- Synthetic permissive expressions, not a claim about the original policy.
    CREATE POLICY "Allow public insert and read for clip jobs" ON public.clip_jobs
      FOR ALL TO public USING (true) WITH CHECK (true);
    GRANT ALL ON public.clip_jobs TO anon, authenticated, service_role;
    GRANT SELECT (id,user_id,video_url,status,clips,error_message,created_at),
      INSERT (id,user_id,video_url,status,clips,error_message,created_at),
      UPDATE (id,user_id,video_url,status,clips,error_message,created_at)
      ON public.clip_jobs TO anon, authenticated, service_role;
  `);
  return db;
}

async function asRole(db, role, owner, sql, params = []) {
  await db.exec(`SET ROLE ${role}`);
  try {
    await db.query("SELECT set_config('request.jwt.claim.sub', $1, false)", [owner ?? '']);
    return await db.query(sql, params);
  } finally {
    await db.exec('RESET ROLE');
  }
}

async function verify(db) {
  const outputs = await db.exec(verification);
  for (const output of outputs) {
    for (const row of output.rows) {
      if ('passed' in row) assert.equal(row.passed, true, JSON.stringify(row));
    }
  }
}

test('audited empty baseline reconciles, verifies, and reruns', async () => {
  const db = await baseline();
  try {
    await db.exec(migration);
    await verify(db);
    await db.exec(migration);
    await verify(db);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM public.clip_jobs')).rows[0].n, 0);
  } finally { await db.close(); }
});

test('owned legacy data and populated metadata survive migration/reruns', async () => {
  const db = await baseline();
  try {
    await db.query(`INSERT INTO public.clip_jobs (user_id,video_url,status,clips,error_message,created_at)
      VALUES ($1,'https://example.com/legacy','completed','[{"start":1,"end":10,"text":"legacy"}]',
      'historical error','2026-01-01T00:00:00Z'),
      ($1,'https://example.com/pending','pending',NULL,NULL,'2026-01-02T00:00:00Z')`, [ownerA]);
    const before = (await db.query('SELECT * FROM public.clip_jobs ORDER BY created_at')).rows;
    await db.exec(migration);
    const after = (await db.query('SELECT * FROM public.clip_jobs ORDER BY created_at')).rows;
    assert.equal(after.length, before.length);
    for (let i = 0; i < before.length; i++) {
      for (const key of Object.keys(before[i])) {
        assert.deepEqual(after[i][key], key === 'clips' && before[i][key] === null ? [] : before[i][key]);
      }
      assert.deepEqual(after[i].updated_at, before[i].created_at);
      assert.equal(after[i].attempts, 0);
    }
    await asRole(db, 'service_role', null, 'SELECT * FROM public.claim_job()');
    const populated = (await db.query('SELECT * FROM public.clip_jobs ORDER BY created_at')).rows;
    await db.exec(migration);
    assert.deepEqual((await db.query('SELECT * FROM public.clip_jobs ORDER BY created_at')).rows, populated);
    await verify(db);
  } finally { await db.close(); }
});

test('RLS and independent column grants enforce ownership and worker-only state', async () => {
  const db = await baseline();
  try {
    await db.exec(migration);
    const inserted = await asRole(db, 'authenticated', ownerA,
      'INSERT INTO public.clip_jobs (user_id,video_url) VALUES ($1,$2) RETURNING *',
      [ownerA, 'https://example.com/a']);
    assert.equal(inserted.rows[0].status, 'pending');
    assert.deepEqual(inserted.rows[0].clips, []);
    assert.equal(inserted.rows[0].attempts, 0);
    assert.equal((await asRole(db, 'authenticated', ownerA, 'SELECT * FROM public.clip_jobs')).rows.length, 1);
    assert.equal((await asRole(db, 'authenticated', ownerB, 'SELECT * FROM public.clip_jobs')).rows.length, 0);
    await assert.rejects(asRole(db, 'authenticated', ownerB,
      'INSERT INTO public.clip_jobs (user_id,video_url) VALUES ($1,$2)', [ownerA, 'https://example.com/spoof']), /row-level security/);
    for (const [role, sql] of [
      ['anon', 'SELECT * FROM public.clip_jobs'],
      ['anon', "INSERT INTO public.clip_jobs (video_url) VALUES ('https://example.com/anon')"],
      ['authenticated', "INSERT INTO public.clip_jobs (user_id,video_url,status) VALUES ('" + ownerA + "','https://example.com/forged','completed')"],
      ['authenticated', "UPDATE public.clip_jobs SET status='failed'"],
      ['authenticated', 'DELETE FROM public.clip_jobs'],
      ['authenticated', 'SELECT * FROM public.claim_job()'],
      ['anon', 'SELECT public.reap_stale_jobs()'],
      ['service_role', "INSERT INTO public.clip_jobs (user_id,video_url) VALUES ('" + ownerA + "','https://example.com/service')"],
      ['service_role', 'DELETE FROM public.clip_jobs'],
      ['service_role', "UPDATE public.clip_jobs SET user_id='" + ownerB + "'"],
      ['service_role', "UPDATE public.clip_jobs SET video_url='https://example.com/reassigned'"],
    ]) await assert.rejects(asRole(db, role, ownerA, sql), /permission denied/);
    await verify(db);
  } finally { await db.close(); }
});

test('RPC claims, retries, terminal cap, timestamps, and stale attempt rejection work', async () => {
  const db = await baseline();
  try {
    await db.exec(migration);
    assert.deepEqual((await asRole(db, 'service_role', null, 'SELECT * FROM public.claim_job()')).rows, []);
    await asRole(db, 'authenticated', ownerA,
      'INSERT INTO public.clip_jobs (user_id,video_url) VALUES ($1,$2)', [ownerA, 'https://example.com/queued']);
    const claimed = (await asRole(db, 'service_role', null, 'SELECT * FROM public.claim_job()')).rows[0];
    assert.equal(claimed.status, 'processing');
    assert.equal(claimed.attempts, 1);
    assert.ok(claimed.processing_started_at);
    await asRole(db, 'service_role', null,
      "UPDATE public.clip_jobs SET processing_started_at=now()-interval '40 minutes' WHERE id=$1", [claimed.id]);
    assert.equal((await asRole(db, 'service_role', null,
      'SELECT public.reap_stale_jobs() AS n')).rows[0].n, 1);
    const reclaimed = (await asRole(db, 'service_role', null, 'SELECT * FROM public.claim_job()')).rows[0];
    assert.equal(reclaimed.attempts, 2);
    const stale = await asRole(db, 'service_role', null,
      "UPDATE public.clip_jobs SET status='completed', clips='[{\"title\":\"old\"}]' WHERE id=$1 AND status='processing' AND attempts=$2 RETURNING *",
      [claimed.id, claimed.attempts]);
    assert.deepEqual(stale.rows, []);
    await asRole(db, 'service_role', null,
      "UPDATE public.clip_jobs SET attempts=3, processing_started_at=now()-interval '40 minutes', error_message=NULL WHERE id=$1", [claimed.id]);
    assert.equal((await asRole(db, 'service_role', null, 'SELECT public.reap_stale_jobs() AS n')).rows[0].n, 1);
    const final = (await db.query('SELECT * FROM public.clip_jobs')).rows[0];
    assert.equal(final.status, 'failed');
    assert.equal(final.error_message, 'Abandoned after 3 attempt(s)');
    assert.equal(final.processing_started_at, null);
    assert.ok(final.updated_at >= claimed.updated_at);
    await assert.rejects(asRole(db, 'service_role', null,
      "SELECT public.reap_stale_jobs(interval '0 seconds',3)"), /must be positive/);
    await assert.rejects(asRole(db, 'service_role', null,
      "SELECT public.reap_stale_jobs(interval '30 minutes',0)"), /must be positive/);
    await verify(db);
  } finally { await db.close(); }
});

test('unsafe legacy rows fail atomically without data/schema/ACL loss', async () => {
  for (const row of [
    { owner: null, status: 'pending', clips: '[]' },
    { owner: ownerA, status: null, clips: '[]' },
    { owner: ownerA, status: 'processing', clips: '[]' },
    { owner: ownerA, status: 'completed', clips: '{"legacy":"object"}' },
    { owner: ownerA, status: 'completed', clips: 'null' },
  ]) {
    const db = await baseline();
    try {
      await db.query(`INSERT INTO public.clip_jobs (user_id,video_url,status,clips,created_at)
        VALUES ($1,'https://example.com/legacy',$2,$3::jsonb,now())`, [row.owner, row.status, row.clips]);
      const before = (await db.query('SELECT * FROM public.clip_jobs')).rows;
      await assert.rejects(db.exec(migration), /require|lack/);
      await db.exec('ROLLBACK');
      assert.deepEqual((await db.query('SELECT * FROM public.clip_jobs')).rows, before);
      assert.equal((await db.query("SELECT count(*)::int AS n FROM information_schema.columns WHERE table_schema='public' AND table_name='clip_jobs'")).rows[0].n, 7);
      assert.equal((await db.query("SELECT has_table_privilege('anon','public.clip_jobs','UPDATE') AS allowed")).rows[0].allowed, true);
    } finally { await db.close(); }
  }
});

test('index name collisions and inherited excessive grants fail closed', async () => {
  for (const setup of [
    'CREATE INDEX clip_jobs_status_created_at_idx ON public.clip_jobs (created_at)',
    'CREATE ROLE inherited_reader; GRANT SELECT ON public.clip_jobs TO inherited_reader; GRANT inherited_reader TO anon',
  ]) {
    const db = await baseline();
    try {
      await db.exec(setup);
      await assert.rejects(db.exec(migration), /index definition|inherited privileges/);
      await db.exec('ROLLBACK');
      assert.equal((await db.query("SELECT count(*)::int AS n FROM information_schema.columns WHERE table_schema='public' AND table_name='clip_jobs'")).rows[0].n, 7);
    } finally { await db.close(); }
  }
});

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import test from 'node:test';

const runtimeRequire = process.env.CLIP_JOBS_TEST_RUNTIME
  ? createRequire(path.join(process.env.CLIP_JOBS_TEST_RUNTIME, 'package.json'))
  : createRequire(import.meta.url);
const { PGlite } = runtimeRequire('@electric-sql/pglite');
const migration = await readFile(new URL('../migrations/003_private_clips_storage.sql', import.meta.url), 'utf8');
const owner = '22222222-2222-4222-8222-222222222222';
const other = '33333333-3333-4333-8333-333333333333';

async function baseline() {
  const db = new PGlite();
  await db.exec(`
    CREATE ROLE anon NOLOGIN;
    CREATE ROLE authenticated NOLOGIN;
    CREATE SCHEMA auth;
    CREATE SCHEMA storage;
    CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
      $$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    CREATE TABLE storage.buckets (id text PRIMARY KEY, name text, public boolean,
      file_size_limit bigint, allowed_mime_types text[]);
    CREATE TABLE storage.objects (bucket_id text, name text);
    CREATE FUNCTION storage.foldername(text) RETURNS text[] LANGUAGE sql IMMUTABLE AS
      $$ SELECT string_to_array($1, '/') $$;
    ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
    GRANT USAGE ON SCHEMA auth, storage TO anon, authenticated;
    GRANT SELECT, INSERT, UPDATE, DELETE ON storage.objects TO anon, authenticated;
    INSERT INTO storage.objects VALUES ('clips', '${owner}/job/attempt-1/clip-1.mp4'),
      ('clips', '${other}/job/attempt-1/clip-1.mp4'), ('elsewhere', '${owner}/other.mp4');
  `);
  return db;
}

test('private clips policy permits owner reads and blocks other users, anonymous access and browser writes', async () => {
  const db = await baseline();
  try {
    await db.exec(migration);
    await db.exec(migration);
    assert.equal((await db.query("SELECT public FROM storage.buckets WHERE id = 'clips'")).rows[0].public, false);
    await db.exec('SET ROLE authenticated');
    await db.query("SELECT set_config('request.jwt.claim.sub', $1, false)", [owner]);
    const visible = (await db.query('SELECT * FROM storage.objects')).rows;
    assert.equal(visible.length, 1);
    assert.equal(visible[0].name, `${owner}/job/attempt-1/clip-1.mp4`);
    await assert.rejects(db.query("INSERT INTO storage.objects VALUES ('clips', $1)", [`${owner}/forged.mp4`]), /row-level security/);
    assert.equal((await db.query('UPDATE storage.objects SET name = name RETURNING *')).rows.length, 0);
    await db.exec('RESET ROLE; SET ROLE anon');
    assert.equal((await db.query('SELECT * FROM storage.objects')).rows.length, 0);
  } finally { await db.close(); }
});

test('existing public bucket aborts without changing production-style resources', async () => {
  const db = await baseline();
  try {
    await db.exec("INSERT INTO storage.buckets VALUES ('clips', 'clips', true, NULL, NULL)");
    await assert.rejects(db.exec(migration), /must be private/);
    await db.exec('ROLLBACK');
    assert.equal((await db.query("SELECT public FROM storage.buckets WHERE id = 'clips'")).rows[0].public, true);
    assert.equal((await db.query("SELECT count(*)::int AS n FROM pg_policy WHERE polrelid = 'storage.objects'::regclass")).rows[0].n, 0);
  } finally { await db.close(); }
});

test('unknown permissive storage policy fails closed and is preserved', async () => {
  const db = await baseline();
  try {
    await db.exec('CREATE POLICY legacy_read ON storage.objects FOR SELECT TO authenticated USING (true)');
    await assert.rejects(db.exec(migration), /Storage policies require review/);
    await db.exec('ROLLBACK');
    assert.equal((await db.query('SELECT count(*)::int AS n FROM storage.buckets')).rows[0].n, 0);
    assert.equal((await db.query("SELECT polname FROM pg_policy WHERE polrelid = 'storage.objects'::regclass")).rows[0].polname, 'legacy_read');
  } finally { await db.close(); }
});

test('existing browser write policies abort atomically instead of surviving the owner read policy', async () => {
  for (const policy of [
    'FOR INSERT TO authenticated WITH CHECK (true)',
    'FOR UPDATE TO authenticated USING (true) WITH CHECK (true)',
    'FOR DELETE TO authenticated USING (true)',
  ]) {
    const db = await baseline();
    try {
      await db.exec(`CREATE POLICY legacy_write ON storage.objects ${policy}`);
      await assert.rejects(db.exec(migration), /Storage policies require review/);
      await db.exec('ROLLBACK');
      assert.equal((await db.query('SELECT count(*)::int AS n FROM storage.buckets')).rows[0].n, 0);
      assert.equal((await db.query("SELECT polname FROM pg_policy WHERE polrelid = 'storage.objects'::regclass")).rows[0].polname, 'legacy_write');
    } finally { await db.close(); }
  }
});

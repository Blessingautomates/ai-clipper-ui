import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const owner = '22222222-2222-4222-8222-222222222222';
const jobId = '11111111-1111-4111-8111-111111111111';
const path = `${owner}/${jobId}/attempt-1/clip-1.mp4`;

async function route(file, client) {
  const context = vm.createContext({ URL, process: { env: {} } });
  const routeModule = new vm.SourceTextModule(await readFile(new URL(file, import.meta.url), 'utf8'), { context });
  await routeModule.link(async (specifier) => {
    const exports = specifier === 'next/server'
      ? { NextResponse: { json: (body, options) => ({ body, status: options?.status ?? 200, headers: options?.headers }) } }
      : { createClient: async () => client };
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await routeModule.evaluate();
  return routeModule.namespace;
}

function clientFor({ user = { id: owner }, job, dbError, signError } = {}) {
  const filters = [];
  const signed = [];
  const row = job ?? { id: jobId, user_id: owner, status: 'completed', clips: [
    { clip_number: 1, storage_bucket: 'clips', object_path: path },
  ] };
  const query = {
    select() { return query; },
    eq(name, value) { filters.push([name, value]); return query; },
    order() { return query; },
    limit: async () => ({ data: [row].filter((r) => filters.every(([key, value]) => r[key] === value)), error: dbError }),
    maybeSingle: async () => ({ data: filters.every(([key, value]) => row[key] === value) ? row : null, error: dbError }),
  };
  return {
    filters, signed,
    auth: { getUser: async () => ({ data: { user }, error: null }) },
    from: (name) => { assert.equal(name, 'clip_jobs'); return query; },
    storage: { from: (bucket) => ({ createSignedUrl: async (objectPath, expires, options) => {
      signed.push({ bucket, objectPath, expires, options: JSON.parse(JSON.stringify(options)) });
      return { data: { signedUrl: 'https://storage.example/private.mp4?token=expiring' }, error: signError };
    } }) },
  };
}

async function clip(client, suffix = '', number = '1') {
  const { GET } = await route('../app/api/jobs/[jobId]/clips/[clipNumber]/route.js', client);
  return GET({ url: `https://app.example/api/jobs/${jobId}/clips/${number}${suffix}` }, {
    params: Promise.resolve({ jobId, clipNumber: number }),
  });
}

test('job list requires authentication and explicitly filters by verified owner', async () => {
  const anonymous = clientFor({ user: null });
  const anonymousRoute = await route('../app/api/jobs/route.js', anonymous);
  assert.equal((await anonymousRoute.GET()).status, 401);
  assert.equal(anonymous.filters.length, 0);
  const client = clientFor();
  const { GET } = await route('../app/api/jobs/route.js', client);
  const response = await GET();
  assert.equal(response.status, 200);
  assert.deepEqual(client.filters, [['user_id', owner]]);
  assert.equal(response.headers['Cache-Control'], 'private, no-store');
});

test('another owner cannot view or sign clips', async () => {
  const client = clientFor({ user: { id: 'other-owner' } });
  assert.equal((await clip(client)).status, 404);
  assert.equal(client.signed.length, 0);
  const { GET } = await route('../app/api/jobs/route.js', clientFor({ user: { id: 'other-owner' } }));
  assert.equal((await GET()).body.jobs.length, 0);
});

test('playback and download sign owned paths for five minutes without caching', async () => {
  for (const download of [false, true]) {
    const client = clientFor();
    const response = await clip(client, download ? '?download=1&object_path=forged' : '');
    assert.equal(response.status, 200);
    assert.equal(response.body.expiresIn, 300);
    assert.equal(response.headers['Cache-Control'], 'private, no-store');
    assert.deepEqual(client.signed, [{ bucket: 'clips', objectPath: path, expires: 300,
      options: download ? { download: 'clip-1.mp4' } : {} }]);
  }
});

test('anonymous, pending, legacy and forged clip paths are never signed', async () => {
  const anonymous = clientFor({ user: null });
  assert.equal((await clip(anonymous)).status, 401);
  assert.equal(anonymous.signed.length, 0);
  for (const job of [
    { id: jobId, user_id: owner, status: 'processing', clips: [{ clip_number: 1 }] },
    { id: jobId, user_id: owner, status: 'completed', clips: null },
    ...['other-owner/' + jobId + '/attempt-1/clip-1.mp4', path.replace('attempt-1', '../elsewhere'), path.replace('clip-1', 'clip-5')]
      .map((object_path) => ({ id: jobId, user_id: owner, status: 'completed', clips: [
        { clip_number: 1, storage_bucket: 'clips', object_path },
      ] })),
  ]) {
    const client = clientFor({ job });
    assert.equal((await clip(client)).status, 404);
    assert.equal(client.signed.length, 0);
  }
  const client = clientFor();
  assert.equal((await clip(client, '', '4')).status, 404);
  assert.equal(client.signed.length, 0);
});

test('database and signing failures return generic errors', async () => {
  const db = await clip(clientFor({ dbError: { message: 'private error' } }));
  assert.equal(db.status, 500);
  assert.equal(db.body.error, 'Unable to load clip');
  const storage = await clip(clientFor({ signError: { message: 'private key' } }));
  assert.equal(storage.status, 503);
  assert.equal(storage.body.error, 'Unable to open clip');
});

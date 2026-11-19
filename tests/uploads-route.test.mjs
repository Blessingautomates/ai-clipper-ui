import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const owner = '22222222-2222-4222-8222-222222222222';
async function prepare(body, { user = { id: owner }, env = { NEXT_PUBLIC_SUPABASE_URL: 'https://storage.example', SUPABASE_SERVICE_ROLE_KEY: 'server-secret' }, bucket = { public: false, file_size_limit: 104857600, allowed_mime_types: ['video/mp4'] }, signError = null } = {}) {
  const signed = [];
  let adminCreated = false;
  const context = vm.createContext({ process: { env }, Number, Math });
  const route = new vm.SourceTextModule(await readFile(new URL('../app/api/uploads/route.js', import.meta.url), 'utf8'), { context });
  await route.link((specifier) => {
    const exports = specifier === 'node:crypto' ? { randomUUID: () => '11111111-1111-4111-8111-111111111111' }
      : specifier === 'next/server' ? { NextResponse: { json: (body, options) => ({ body, status: options?.status ?? 200, headers: options?.headers }) } }
        : specifier === '@/utils/supabase/server' ? { createClient: async () => ({ auth: { getUser: async () => ({ data: { user }, error: null }) } }) }
          : { createClient: (url, key, options) => {
            adminCreated = true;
            assert.equal(key, 'server-secret');
            assert.equal(options.auth.persistSession, false);
            return { storage: { getBucket: async () => ({ data: bucket, error: null }), from: (bucket) => ({
              createSignedUploadUrl: async (path, options) => {
                signed.push({ bucket, path, upsert: options.upsert });
                return { data: { token: 'limited-upload-token' }, error: signError };
              },
            }) } };
          } };
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context });
  });
  await route.evaluate();
  const response = await route.namespace.POST({ json: async () => {
    if (body === 'invalid-json') throw new SyntaxError();
    return body;
  } });
  return { response, signed, adminCreated };
}

test('anonymous and malformed upload requests cannot authorize storage writes', async () => {
  const anonymous = await prepare({ contentType: 'video/mp4', size: 1024 }, { user: null });
  assert.equal(anonymous.response.status, 401);
  assert.equal(anonymous.adminCreated, false);
  for (const body of ['invalid-json', null, {}, { contentType: 'text/html', size: 1 },
    { contentType: 'video/mp4', size: 0 }, { contentType: 'video/mp4', size: 105 * 1024 * 1024 }]) {
    const result = await prepare(body);
    assert.equal(result.response.status, 400);
    assert.equal(result.adminCreated, false);
  }
});

test('upload authorization uses a generated owner path, refuses overwrites and exposes only a limited token', async () => {
  const { response, signed } = await prepare({ contentType: 'video/mp4', size: 1024, path: 'forged/path', userId: 'forged' });
  assert.equal(response.status, 200);
  assert.deepEqual(signed, [{ bucket: 'clips', path: owner + '/sources/11111111-1111-4111-8111-111111111111.mp4', upsert: false }]);
  assert.equal(response.body.token, 'limited-upload-token');
  assert.equal(response.headers['Cache-Control'], 'private, no-store');
  assert.equal(JSON.stringify(response).includes('server-secret'), false);
});

test('missing configuration, public buckets, bucket limits and signing failures are handled', async () => {
  for (const options of [{ env: {} }, { bucket: null }, { bucket: { public: true } },
    { bucket: { public: false, allowed_mime_types: ['text/plain'] } },
    { signError: { message: 'private storage details' } }]) {
    const { response } = await prepare({ contentType: 'video/mp4', size: 1024 }, options);
    assert.equal(response.status, 503);
    assert.equal(JSON.stringify(response).includes('private storage details'), false);
  }
  const limited = await prepare({ contentType: 'video/mp4', size: 2048 }, { bucket: { public: false, file_size_limit: 1024 } });
  assert.equal(limited.response.status, 400);
  assert.equal(limited.signed.length, 0);
});

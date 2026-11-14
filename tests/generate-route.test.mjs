import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// Evaluate the actual route modules with local auth/database substitutes.
// Neither module can access the network or real environment credentials here.
async function loadRoute(path, client) {
  const context = vm.createContext({ URL, console: { error() {} } });
  const route = new vm.SourceTextModule(await readFile(new URL(path, import.meta.url), 'utf8'), { context });
  await route.link(async (specifier) => {
    if (specifier === '@/utils/supabase/server') {
      return new vm.SyntheticModule(['createClient'], function () {
        this.setExport('createClient', async () => client);
      }, { context });
    }
    if (specifier === '@/lib/worker-dispatch') {
      return new vm.SyntheticModule(['dispatchWorker'], function () {
        this.setExport('dispatchWorker', async () => 'dispatched');
      }, { context });
    }
    if (specifier === 'next/server') {
      return new vm.SyntheticModule(['NextResponse'], function () {
        this.setExport('NextResponse', {
          json: (body, options) => ({ body, status: options?.status ?? 200 }),
          redirect: (url) => ({ url: url.toString(), status: 307 }),
        });
      }, { context });
    }
    throw new Error(`Unexpected import: ${specifier}`);
  });
  await route.evaluate();
  return route.namespace;
}

function clientFor({ user = { id: 'verified-owner' }, authError = null, dbError = null } = {}) {
  const inserted = [];
  return {
    inserted,
    auth: { getUser: async () => ({ data: { user }, error: authError }) },
    from(table) {
      assert.equal(table, 'clip_jobs');
      return {
        insert(rows) {
          inserted.push(JSON.parse(JSON.stringify(rows)));
          return { select: () => ({ single: async () => ({
            data: { id: 'job-id', ...rows[0], status: 'pending', clips: [] }, error: dbError,
          }) }) };
        },
      };
    },
  };
}

async function generate(body, options) {
  const client = clientFor(options);
  const { POST } = await loadRoute('../app/api/generate/route.js', client);
  return { response: await POST({ json: async () => body }), client };
}

test('anonymous or invalid sessions cannot insert a job', async () => {
  for (const options of [{ user: null }, { authError: new Error('expired') }]) {
    const { response, client } = await generate({ videoUrl: 'https://example.com/video' }, options);
    assert.equal(response.status, 401);
    assert.equal(client.inserted.length, 0);
  }
});

test('verified owner replaces forged userId; worker-owned state is omitted', async () => {
  const { response, client } = await generate({
    videoUrl: 'https://example.com/video ', userId: 'another-owner',
    status: 'completed', clips: [{ stolen: true }], attempts: 99,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(client.inserted, [[{
    user_id: 'verified-owner', video_url: 'https://example.com/video',
  }]]);
  assert.equal(response.body.job.id, 'job-id');
});

test('invalid inputs do not insert jobs', async () => {
  for (const body of [null, {}, { videoUrl: '' }, { videoUrl: 123 }, { videoUrl: 'file:///video' },
    { videoUrl: 'https://' }, { videoUrl: 'https://user:password@example.com/video' }]) {
    const { response, client } = await generate(body);
    assert.equal(response.status, 400);
    assert.equal(client.inserted.length, 0);
  }
});

test('malformed JSON returns 400 without an insert', async () => {
  const client = clientFor();
  const { POST } = await loadRoute('../app/api/generate/route.js', client);
  const response = await POST({ json: async () => { throw new SyntaxError('invalid'); } });
  assert.equal(response.status, 400);
  assert.equal(client.inserted.length, 0);
});

test('database failures return a generic error without internal details', async () => {
  const { response } = await generate({ videoUrl: 'https://example.com/video' }, {
    dbError: { message: 'private database detail' },
  });
  assert.equal(response.status, 500);
  assert.equal(response.body.error, 'Failed to create video processing job');
});

test('callback security fix keeps hostile destinations on the application origin', async () => {
  const { GET } = await loadRoute('../app/auth/callback/route.js', {
    auth: { exchangeCodeForSession: async () => ({ error: null }) },
  });
  for (const next of [
    'https://evil.example/path', '//evil.example', '/\\evil.example',
    '  //evil.example', '\\evil.example', 'https://[',
  ]) {
    const url = new URL('https://app.example/auth/callback');
    url.searchParams.set('code', 'local-code');
    url.searchParams.set('next', next);
    const response = await GET({ url: url.toString() });
    assert.equal(new URL(response.url).origin, 'https://app.example');
  }
  const safe = await GET({ url: 'https://app.example/auth/callback?code=local&next=%2Fdashboard%3Fjob%3D1' });
  assert.equal(safe.url, 'https://app.example/dashboard?job=1');
});

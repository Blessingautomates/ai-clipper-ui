import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import test from 'node:test';
import vm from 'node:vm';
import { createClient } from '@supabase/supabase-js';

const require = createRequire(import.meta.url);
const { NextResponse } = require('next/server');

async function loadRoute(file, client) {
  const context = vm.createContext({ URL, console, process: { env: {} } });
  const routeModule = new vm.SourceTextModule(await readFile(new URL(file, import.meta.url), 'utf8'), { context });
  await routeModule.link((specifier) => {
    const exports = specifier === 'next/server' ? { NextResponse }
      : specifier === '@/lib/worker-dispatch' ? { dispatchWorker: async () => 'unconfigured' }
        : { createClient: async () => client };
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await routeModule.evaluate();
  return routeModule.namespace;
}

test('owned URL → authenticated API → queue RPC → real MP4 upload → signed playback/download', { timeout: 120000 }, async (t) => {
  const child = spawn(process.env.CLIPPER_TEST_PYTHON || 'python3', ['tests/fixtures/mvp_backend.py'], {
    env: { PATH: process.env.PATH, PYTHONUNBUFFERED: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  let fixture;
  try {
    fixture = await new Promise((resolve, reject) => {
      const lines = createInterface({ input: child.stdout });
      const timer = setTimeout(() => reject(new Error('Fixture startup timed out: ' + stderr)), 30000);
      lines.once('line', (line) => { clearTimeout(timer); resolve(JSON.parse(line)); });
      child.once('exit', (code) => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${stderr}`)); });
      child.once('error', reject);
    });
    const client = (token) => createClient(fixture.url, 'fixture-anon-key', {
      auth: { persistSession: false, autoRefreshToken: false }, global: { headers: { Authorization: `Bearer ${token}` } },
    });
    const owner = client('owner-token');
    const { POST } = await loadRoute('../app/api/generate/route.js', owner);
    const generated = await POST(new Request('http://app.test/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ videoUrl: fixture.url + '/owned.mp4', userId: 'forged-owner', status: 'completed' }),
    }));
    assert.equal(generated.status, 200, JSON.stringify(await generated.clone().json()));
    const { job } = await generated.json();
    assert.equal(job.user_id, fixture.owner);
    assert.equal(job.status, 'pending');
    const listing = await loadRoute('../app/api/jobs/route.js', owner);
    await fetch(fixture.url + '/fixture/process', { method: 'POST' });
    let completed;
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      const response = await listing.GET();
      const data = await response.json();
      completed = data.jobs?.find((row) => row.id === job.id);
      if (completed?.status === 'completed' || completed?.status === 'failed') break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    assert.equal(completed?.status, 'completed', JSON.stringify(completed) + stderr);
    assert.equal(completed.clips.length, 3);
    const endpoint = '../app/api/jobs/[jobId]/clips/[clipNumber]/route.js';
    const playback = await loadRoute(endpoint, owner);
    const other = await loadRoute(endpoint, client('other-token'));
    const anonymous = await loadRoute(endpoint, client('anonymous'));
    const args = { params: Promise.resolve({ jobId: job.id, clipNumber: '1' }) };
    const request = new Request(`http://app.test/api/jobs/${job.id}/clips/1`);
    assert.equal((await other.GET(request, args)).status, 404);
    assert.equal((await anonymous.GET(request, args)).status, 401);
    for (const clip of completed.clips) {
      const options = { params: Promise.resolve({ jobId: job.id, clipNumber: String(clip.clip_number) }) };
      const signedResponse = await playback.GET(request, options);
      assert.equal(signedResponse.status, 200);
      const signed = await signedResponse.json();
      const streamed = await fetch(signed.url, { headers: { Range: 'bytes=0-127' } });
      assert.equal(streamed.status, 206);
      assert.equal(streamed.headers.get('content-type'), 'video/mp4');
      assert.equal((await streamed.arrayBuffer()).byteLength, 128);
      const downloadResponse = await playback.GET(new Request(request.url + '?download=1'), options);
      const download = await downloadResponse.json();
      const downloaded = await fetch(download.url);
      assert.equal(downloaded.status, 200);
      assert.match(downloaded.headers.get('content-disposition'), /attachment; filename="clip-\d.mp4"/);
      const bytes = Buffer.from(await downloaded.arrayBuffer());
      assert.equal(createHash('sha256').update(bytes).digest('hex'), clip.sha256);
      const destination = join(fixture.artifacts, `download-${clip.clip_number}.mp4`);
      await writeFile(destination, bytes);
      execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-xerror', '-i', destination, '-f', 'null', '-']);
      const tampered = new URL(signed.url);
      tampered.searchParams.set('token', '1.invalid');
      assert.equal((await fetch(tampered)).status, 403);
      assert.equal((await fetch(`${fixture.url}/storage/v1/object/clips/${clip.object_path}`)).status, 403);
    }
    t.diagnostic('Owned source, job metadata and three downloaded MP4s: ' + fixture.artifacts);
  } finally {
    if (fixture) await fetch(fixture.url + '/fixture/stop', { method: 'POST' }).catch(() => {});
    // Fixture files remain in /tmp so a reviewer can inspect the MP4s.
    if (child.exitCode === null) child.kill('SIGTERM');
  }
});

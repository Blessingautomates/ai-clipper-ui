import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

async function load(env, fetch) {
  const context = vm.createContext({ process: { env }, fetch, AbortSignal });
  const routeModule = new vm.SourceTextModule(await readFile(new URL('../lib/worker-dispatch.js', import.meta.url), 'utf8'), { context });
  await routeModule.link(() => new vm.SyntheticModule([], () => {}, { context }));
  await routeModule.evaluate();
  return routeModule.namespace.dispatchWorker;
}

test('unconfigured dispatch leaves the saved job available to recovery polling', async () => {
  const dispatch = await load({}, () => { throw new Error('must not fetch'); });
  assert.equal(await dispatch(), 'unconfigured');
});

test('dispatch uses server token and workflow_dispatch with bounded request', async () => {
  const calls = [];
  const dispatch = await load({ WORKER_GITHUB_TOKEN: 'test-token', WORKER_GITHUB_REPOSITORY: 'owner/repo', WORKER_GITHUB_REF: 'mvp' },
    async (url, options) => { calls.push({ url, options }); return { ok: true }; });
  assert.equal(await dispatch(), 'dispatched');
  assert.equal(calls[0].url, 'https://api.github.com/repos/owner/repo/actions/workflows/worker.yml/dispatches');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer test-token');
  assert.deepEqual(JSON.parse(calls[0].options.body), { ref: 'mvp', inputs: { max_jobs: '5' } });
  assert.ok(calls[0].options.signal);
});

test('dispatch errors are nonfatal and reveal no token or provider detail', async () => {
  for (const fetch of [async () => ({ ok: false }), async () => { throw new Error('test-token'); }]) {
    const dispatch = await load({ WORKER_GITHUB_TOKEN: 'test-token', WORKER_GITHUB_REPOSITORY: 'owner/repo' }, fetch);
    assert.equal(await dispatch(), 'unavailable');
  }
});

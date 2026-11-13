import 'server-only';

// The token belongs to the server and only needs Actions: write on this repo.
export async function dispatchWorker() {
  const { WORKER_GITHUB_TOKEN: token, WORKER_GITHUB_REPOSITORY: repository } = process.env;
  if (!token || !repository) return 'unconfigured';
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) return 'unavailable';
  try {
    const response = await fetch(
      `https://api.github.com/repos/${repository}/actions/workflows/worker.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: process.env.WORKER_GITHUB_REF || 'main', inputs: { max_jobs: '5' } }),
        signal: AbortSignal.timeout(8000),
      },
    );
    return response.ok ? 'dispatched' : 'unavailable';
  } catch {
    // The insert already committed. Never tell the user to resubmit the job.
    return 'unavailable';
  }
}

This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

For the audited Supabase job queue, follow the
[migration 002 application and verification sequence](docs/002_clip_jobs_reconciliation.md).
Apply 002 directly to the audited existing table; do not execute the historical
draft or migration 001 first. The migration has not been applied to production.

## Getting Started

The MVP uses `app/dashboard/page.js`: submit a direct public video file URL,
poll owned jobs, then play or download real MP4s through five-minute signed URLs.
The isolated Python worker downloads at most 256 MB / 30 minutes of video,
renders up to three H.264/AAC clips with FFmpeg, validates them by decoding,
and uploads to private Supabase storage. AI keys are optional: without analysis
keys, evenly spaced clips are explicitly labelled preview selections.
YouTube/webpage extraction is not supported by this first direct-file workflow.

Copy `.env.example` to `.env.local` and configure an **isolated test Supabase
project**. Queue RPCs and private storage must be provisioned there before a
connected run. `migrations/003_private_clips_storage.sql` prepares the private
bucket and owner read policy. Migration 002 remains unapplied; production
migration/storage changes require approval. Migration 003 aborts when unknown
storage policies exist; review those before applying it to that project.

The frontend needs only the public Supabase URL/anon key. Its server can also
use `WORKER_GITHUB_TOKEN`, `WORKER_GITHUB_REPOSITORY`, and `WORKER_GITHUB_REF`
to dispatch `worker.yml` after insertion. The fine-grained token needs
Actions write access to this repository; the workflow must exist on the default
branch and the selected ref must contain this implementation. Worker-only
service-role and optional AI keys belong in Actions secrets. No AI calls or
FFmpeg processing happen in the frontend.

Actions serializes runs, with a 65-minute workflow timeout, 50-minute queue
budget, 20-minute job deadline, and 30-minute stale recovery threshold.
The five-minute schedule is recovery polling: GitHub can delay scheduled runs
and coalesce pending runs, so it does not promise processing within five minutes.
Monitor Actions failures/queue age and dispatch manually if necessary.
Hard runner termination or a storage/database outage can leave orphaned attempt
objects; cleanup logs identify their prefix for later review.

Run the owned synthetic video and API tests without credentials or production writes:

```bash
npm run test:mvp
npm run build
```

For the complete local HTTP integration test with both real Supabase SDKs,
install `python3 -m pip install 'supabase==2.34.1'`, then run
`npm run test:integration`. It uses a local auth/queue/private-storage fixture,
checks ownership, byte-range playback, signed downloads and expired signatures,
and leaves the owned source and downloaded MP4s under `/tmp/clipper-mvp-proof-*`.
This verifies the implementation without claiming a live Supabase acceptance run.

For a direct URL you own, render locally without queue/storage writes:

```bash
python3 worker.py --url 'https://YOUR-HOST/owned-video.mp4' --render-dir /tmp/clipper-output
```

For a connected run against the configured **test** project, install worker
dependencies (`python3 -m pip install -r requirements.txt`), start the app, submit
the URL in `/dashboard`, then run `python3 worker.py --max-jobs 1 --budget-minutes 25`
if Actions dispatch is not configured. Inspect the rendered clip in the dashboard
and download it; never point this command at production before migration approval.

Use Node **22.x LTS**. `.nvmrc` and `.node-version` select major 22;
package engines and `.npmrc` reject incompatible dependency installs. The minimum
required by the locked Supabase packages is 22.0.0; use a current patched 22.x.
CI reads `.nvmrc`, and Vercel must use `22.x`, matching `package.json`.

With nvm already available, install/use the project runtime and locked dependencies:

```bash
nvm install
nvm use
npm ci
npm run dev
```

For a side-by-side Ubuntu/Termux setup that preserves system Node and Codex CLI,
use the [runtime procedure in the deployment runbook](docs/002_DEPLOYMENT_RUNBOOK.md#node-22-runtime-setup-and-validation).

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

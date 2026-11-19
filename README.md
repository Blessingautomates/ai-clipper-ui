# ToolStack Video Clipper

Next.js 16 / React 19 frontend and APIs, Supabase authentication and job queue,
and a Python / FFmpeg worker dispatched through GitHub Actions.

The dashboard accepts direct public video URLs (up to 256 MB) and private MP4
uploads (up to 100 MB, subject to the existing bucket's limit), up to 30 minutes.
The worker renders up to three browser-compatible H.264/AAC MP4s, verifies them
by decoding, and uploads them to private storage. Owners can play and download
clips through five-minute signed links.

With a configured analysis provider and OpenAI Whisper key, the worker
transcribes downloaded audio and selects clips from timed speech. Without
analysis credentials, it renders explicitly labelled preview selections.
YouTube page ingestion, burned-in subtitles, and vertical reframing are not
implemented in this release.

The reconciliation migration has been applied according to the operator's
reported verification result: `total_jobs=0, invalid_jobs=0`. Deployment work
does not require repeating database audits or changing the Supabase schema.
Private storage configuration and live deployment acceptance remain to be
confirmed; the SQL files are historical/preparation references.

## Local setup

Use a current Node **22.x** runtime, Python 3.11, and FFmpeg/ffprobe.
Copy `.env.example` to `.env.local` and populate the public Supabase URL/anon
key. Signed uploads also require `SUPABASE_SERVICE_ROLE_KEY` on the Next.js
server. It must never be a `NEXT_PUBLIC_` variable.

```bash
npm ci
python3 -m pip install -r requirements.txt
npm run dev
```

Open `/login`, then `/dashboard`. Keep the worker's `SUPABASE_URL` pointed at
the same project as `NEXT_PUBLIC_SUPABASE_URL`, and use the same
`CLIPS_STORAGE_BUCKET` on both servers (default: `clips`). Uploads use signed
authorization into that existing private bucket; no new storage policy is
created. Originals are retained so pending jobs and retries keep their source.

## Validation

```bash
npm run lint
npm run typecheck
npm run test:mvp
npm run test:integration
npm run build
```

Tests use owned synthetic video and local auth/queue/storage fixtures, with no
live Supabase access. Integration tests exercise both real Supabase SDKs,
private uploads, rendering, playback ranges, signed downloads, expired
signatures, and owner isolation. Transcription/AI tests use fixture provider
responses and real FFmpeg audio extraction/rendering; they do not establish
that live provider credentials work.

For an owned direct URL, render without queue/storage writes:

```bash
python3 worker.py --url 'https://YOUR-HOST/owned-video.mp4' --render-dir /tmp/clipper-output
```

## Deployment

Follow [the application deployment checklist](docs/DEPLOYMENT.md).
The frontend runs on Vercel with Node 22.x. The worker runs from
`.github/workflows/worker.yml`, using GitHub Actions secrets. Server dispatch
requires `WORKER_GITHUB_TOKEN` with Actions write access,
`WORKER_GITHUB_REPOSITORY`, and `WORKER_GITHUB_REF`.

Worker runs are serialized with a 65-minute workflow timeout, 50-minute run
budget, 20-minute job deadline, and 30-minute stale lease. Scheduled recovery
runs approximately every five minutes; GitHub does not guarantee that start
time. `/api/process-video` and `/api/webhook` are authenticated aliases of the
queue API; n8n is not needed.

A passing local build is not a deployed-system acceptance test.

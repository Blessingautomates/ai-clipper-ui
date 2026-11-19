# Application deployment

The operator reports migration 002 applied and verification completed with
`total_jobs=0, invalid_jobs=0`. Do not repeat database audits or apply schema
or storage migrations as part of this deployment.

## Configure the target

Use the existing Vercel project `ai-clipper-ui` in team `toolstack1`, linked to
`Blessingautomates/ai-clipper-ui`, with `main` as the production branch and
`https://clipper.toolstackai.xyz` as the production domain. Use Node 22.x in
Vercel, `npm ci`, and `npm run build`, with the repository root as the app root.
The release is not deployed until these changes are published to that target.

| Location | Required configuration |
| --- | --- |
| Vercel | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| Vercel server only | `SUPABASE_SERVICE_ROLE_KEY` for signed uploads; `CLIPS_STORAGE_BUCKET` (default `clips`) |
| Vercel server dispatch | `WORKER_GITHUB_TOKEN`, `WORKER_GITHUB_REPOSITORY`, `WORKER_GITHUB_REF` (default `main`) |
| Actions secrets | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |
| Actions repository variable | `CLIPS_STORAGE_BUCKET`, matching Vercel |
| Actions secrets for AI clips | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, and `OPENAI_API_KEY` for Whisper; alternatively a working AgentRouter configuration plus `OPENAI_API_KEY` |

The public and worker Supabase URLs must identify the same project. Never place
the service-role or provider keys in public variables. The GitHub dispatch
token needs Actions write access on this repository. `worker.yml` must be on
the default branch, and the selected ref must contain this release.
Set `WORKER_GITHUB_REPOSITORY=Blessingautomates/ai-clipper-ui` and
`WORKER_GITHUB_REF=main`. Confirm these variables apply to Production; preview
configuration does not configure the production deployment.

Use the existing private bucket, which must accept `video/mp4` and permit
authenticated owners to read their own first-folder prefix. The upload route
uses the service role only to issue a signed upload token for a generated path
under the verified owner's ID. It refuses public buckets and observes their
file-size limits. Originals stay private and are retained for queue retries.
If no suitable bucket already exists, storage is an outstanding prerequisite;
stop before connected acceptance and report it without changing the schema.

In Supabase Auth, configure the deployed application as the Site URL and add
the exact deployed `/auth/callback` URL to the redirect allowlist. Confirm
password/email sign-in works. Google sign-in also requires enabling and
configuring that provider. These are Auth settings, not database migrations.
For this release, the Site URL is `https://clipper.toolstackai.xyz` and the
callback URL is `https://clipper.toolstackai.xyz/auth/callback`.

## Observed production state

Inspection on 2026-11-19 found:

- Production is reachable; the home and login pages return HTTP 200. Anonymous
  requests to `/api/generate` and `/api/jobs` return HTTP 401.
- Before publishing this release, `/api/uploads` returns HTTP 404 and the login
  bundle lacks the new callback error display. The upload and login changes
  have not yet reached production.
- GitHub reports Vercel deployment and validation success for commit
  `e913c2be0c79b2183e807eb0a8863512d8e81464`.
- `worker.yml` exists on `main` but is manually disabled. Its most recent run,
  [43709321335](https://github.com/Blessingautomates/ai-clipper-ui/actions/runs/43709321335),
  failed on 2026-11-15 with `Queue recovery RPC unavailable`. This historical
  failure does not establish a current schema or credential defect; do not
  repeat the completed migration in response to it.
- The public production Supabase Auth settings show email and Google providers
  enabled, signup enabled, and email confirmation required. They do not expose
  the Site URL or redirect allowlist, or prove an authenticated sign-in.
- No Vercel login, project linkage file, or Supabase administrator credentials
  are available in the workspace. Stored GitHub access can inspect runs and
  deployment statuses but receives HTTP 403 for Actions secrets and variables.
  Their presence and values remain unverified.

Authenticated project access is required to verify Vercel Production variables,
the existing private bucket and owner access, Auth redirect settings, and
matching worker credentials. Once those prerequisites are confirmed, enable
the existing worker and verify a current run and the owned-video acceptance
steps below. Do not treat the old worker failure or inaccessible settings as
proof that a setting is missing. Do not mark deployment complete until live
processing, playback, and downloads succeed.

Local release verification passed with Node 22.x: lint, TypeScript, 18 API tests,
21 worker tests, both real MP4 integration workflows, and the production build.
The integration runs used local fixtures, not production credentials.

Release `84805a49188a897f89adef5a74e8733da2f7c0ab` was subsequently pushed to
`main`. [Hosted validation](https://github.com/Blessingautomates/ai-clipper-ui/actions/runs/44475633312)
passed and [Vercel deployment](https://vercel.com/toolstack1/ai-clipper-ui/FJwS8jKWuz1naYfepm66otMv7jYj)
completed. The production domain now serves the new home page, and anonymous
POST requests to `/api/uploads` and `/api/webhook` return HTTP 401. This confirms
the application release is live; it does not confirm authenticated uploads or
worker processing.

The manual `production-config.yml` workflow uses existing worker secrets inside
the GitHub runner to check the expected Supabase project, server key role, and
existing bucket privacy, MP4 support, and upload limit. It reports only booleans,
HTTP statuses, and the numeric upload limit, with no queue or database calls.
Optional AI credential presence is reported separately and does not establish
provider availability. It does not inspect Vercel variables or Auth redirects.

## Publish and accept the release

1. Run `npm run lint`, `npm run typecheck`, `npm run test:mvp`,
   `npm run test:integration`, and `npm run build` with Node 22.x.
2. Publish the tested application changes and deploy the selected Vercel
   project. Confirm the validation workflow passes for the published commit.
3. On the deployed URL, sign up/confirm email or sign in, and confirm the
   dashboard requests use the resulting cookie session. Check Google OAuth if
   enabled and confirm callback failures show an error on the login page.
4. Upload a short MP4 you own. Confirm upload completes, the owned job is
   queued, worker dispatch starts, and that exact job becomes completed.
5. Play and download each clip. Open the downloaded MP4 and check that its
   selected timestamps match the source. Repeat with an owned direct video URL.
6. With AI credentials configured, submit spoken video and confirm timed
   transcription and AI selection complete; preview-only success does not
   prove those provider integrations work.
7. With a second account, confirm the first account's jobs/clips are inaccessible.
   Confirm anonymous API requests fail and expired playback links can be refreshed.
8. Record the deployed URL, application commit, worker run URL, job IDs, and
   playback/download result. Only then call deployment complete.

The local integration tests use fixture Auth/Storage APIs. They prove the code
path with real SDKs and FFmpeg; they do not prove live Auth configuration,
storage permissions, workflow credentials, provider availability, or deployment.

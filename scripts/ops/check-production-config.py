"""Read-only worker configuration check. Never print credentials or API bodies."""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def report(name, passed, **details):
    print(json.dumps({"check": name, "passed": passed, **details}), flush=True)
    return passed


def main():
    expected = "https://lmrfzlbwzdmfgyfglukj.supabase.co"
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not report("worker_supabase_credentials_present", bool(url and key)):
        return 1
    if not report("worker_matches_production_supabase", url == expected):
        return 1
    # Validate legacy JWT roles locally; newer sb_secret keys are verified by
    # the bucket API. A public key must never masquerade as the worker key.
    try:
        parts = key.split(".")
        role = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))).get("role") if len(parts) == 3 else None
    except (ValueError, IndexError, UnicodeDecodeError):
        role = None
    if not report("worker_uses_server_key", role == "service_role" or key.startswith("sb_secret_")):
        return 1
    bucket = os.environ.get("CLIPS_STORAGE_BUCKET") or "clips"
    request = urllib.request.Request(
        url + "/storage/v1/bucket/" + urllib.parse.quote(bucket, safe=""),
        headers={"apikey": key, "Authorization": "Bearer " + key},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        report("existing_video_bucket_accessible", False, http_status=error.code)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError):
        report("existing_video_bucket_accessible", False)
        return 1
    report("existing_video_bucket_accessible", True)
    private = report("video_bucket_private", data.get("public") is False)
    allowed = data.get("allowed_mime_types")
    accepts_mp4 = report("video_bucket_accepts_mp4", not allowed or "video/mp4" in allowed)
    limit = data.get("file_size_limit")
    report("video_bucket_upload_limit", True, max_bytes=limit)
    # Optional credentials are reported as availability only. This makes no
    # provider request and does not prove a provider accepts the credentials.
    analysis = bool(os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY")) or bool(os.environ.get("AGENTROUTER_API_KEY"))
    report("analysis_credentials_present", analysis)
    report("transcription_credentials_present", bool(os.environ.get("OPENAI_API_KEY")))
    return 0 if private and accepts_mp4 else 1


if __name__ == "__main__":
    sys.exit(main())

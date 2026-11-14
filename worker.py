"""ToolStack Clipper worker.

Claims a job from Supabase, retrieves a transcript WITH timestamps, asks
DeepSeek-v4-flash (via AgentRouter) to select viral clips, and writes them back.

Runs on a GitHub Actions cron (see .github/workflows/worker.yml), and can also be
driven directly for local testing -- no Supabase required:

    python worker.py --url "https://youtube.com/watch?v=..." [--json]

Phase 1 changes vs the previous version:
  * The transcript's segment timings are now passed to the model. Previously only
    `transcript.text` was sent, so every start/end time was invented.
  * Transcripts come from YouTube captions when available, falling back to
    Whisper -- which also sidesteps Whisper's 25 MB upload cap for long videos.
  * Long transcripts are analysed in overlapping windows instead of being
    truncated, and clips are de-duplicated across windows.
  * Clips are emitted in a single canonical schema rich enough for the dashboard
    to render score rationales.
  * Jobs are claimed atomically, and stale 'processing' rows orphaned by a killed
    runner are reaped instead of being stuck forever.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tempfile
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

import media

import transcript as transcript_mod
from transcript import TranscriptUnavailable, format_timestamp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://agentrouter.org/v1"

# AgentRouter refuses requests that do not identify the calling client, with
# 401 {"message": "unauthorized client detected", ...}. These two headers are
# what it checks, so they must be sent on every request.
AGENTROUTER_USER_AGENT = "claude-code/0.2.29"
AGENTROUTER_ORIGINATOR = "claude-code"

# A job is considered orphaned if it has been 'processing' for longer than this.
# Must exceed the time a legitimate single job can take.
STALE_AFTER_MINUTES = 30
MAX_ATTEMPTS = 3

# Per-run limits, so the runner exits cleanly between jobs instead of being
# SIGKILLed mid-job (which is what stranded rows in 'processing' before).
MAX_JOBS_PER_RUN = 5
RUN_BUDGET_SECONDS = 50 * 60

# Transcript windowing. Only used when the whole transcript will not fit.
WINDOW_SECONDS = 2700.0   # 45 minutes
OVERLAP_SECONDS = 120.0   # 2 minutes

# Coarse token estimate (~4 chars/token) -- good enough to decide whether to
# window, without pulling in a tokenizer.
CHARS_PER_TOKEN = 4
MAX_TRANSCRIPT_TOKENS = 60_000


# ---------------------------------------------------------------------------
# Canonical clip schema
# ---------------------------------------------------------------------------
#
# Three incompatible clip shapes existed in this codebase: the worker's
# (title/start/end/text/score), the Next.js route's (clip_number/start_time/
# end_time/virality_score/rationale/...), and the dashboard mock's
# (id/title/score/duration/caption). This is the one shape now -- the route's
# field names, because they are the fuller design and because the dashboard
# needs `rationale` and `hook_sentence`, which the old worker schema lacked.
#
#   clip_number int | title str | start_time float sec | end_time float sec
#   duration_seconds float | hook_sentence str | virality_score int 0-100
#   rationale str | genre_category str | suggested_caption str

SYSTEM_PROMPT = """You are an elite short-form video viral strategist and lead video editor.
You analyse a timestamped transcript and extract every highly engaging short-form clip (TikTok, YouTube Shorts, Instagram Reels).

Return up to THREE of the strongest self-contained clips, at most 90 seconds each.

CRITICAL INSTRUCTION - TIMESTAMPS:
Every transcript line is prefixed with its real time range, like:
  [00:01:23.400 -> 00:01:27.900] some spoken words
Those prefixes are the ONLY legal source of time values. Every start_time you return must
equal, exactly, the opening time of one of those prefixes -- copy the number, never compute,
interpolate or estimate one. A start_time that does not appear in the transcript is a
failure. end_time comes from the prefix of the line the clip ends on and must exceed
start_time. Clips must be self-contained: start at the beginning of the hook, end after the
payoff.

CRITICAL INSTRUCTION - HOOKS:
hook_sentence MUST be copied VERBATIM from the transcript line your clip starts on --
character for character, no paraphrase, no clean-up, no ellipsis. It is used to verify that
your start_time really lands on the moment you describe.

ANALYSIS FRAMEWORK:
1. Multi-Genre Intelligence:
   - Podcasts/Talks: controversial opinions, counter-intuitive claims, emotional stories, high-value takeaways.
   - Gaming/Streams: high-energy spikes, rage quits, clutch plays, chaotic banter.
   - Vlogs/IRL: narrative shifts, intense reactions, visual jokes, peak emotional beats.
   - Educational/Tech: concise "lightbulb moments", step-by-step hacks, surprising facts.
2. Chat Analytics: if stream chat is supplied, use spikes in emoji/message bursts to validate exact hype/funny timestamps.
3. Virality Scoring (0-100): score on hook strength, emotional resonance, and pacing. Give a concise 1-2 sentence rationale for each score.

OUTPUT FORMAT:
Return ONLY a JSON object with a single "clips" key, no markdown fences and no commentary:
{"clips": [ { ...one object per clip... } ]}

Each clip object must have EXACTLY these keys:
  clip_number (int), title (str), start_time (float seconds), end_time (float seconds),
  duration_seconds (float), hook_sentence (str), virality_score (int 0-100),
  rationale (str), genre_category (str), suggested_caption (str)
"""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_dotenv(path: str = ".env.local") -> None:
    """Minimal .env loader so local runs work without an extra dependency.

    Values already present in the environment always win, so CI is unaffected.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        # RuntimeError, deliberately not SystemExit: SystemExit derives from
        # BaseException, so `except Exception` in process_video_job() and
        # dry_run() would NOT catch it. A missing key mid-queue would then kill
        # the entire run and strand the claimed row in 'processing' until the
        # reaper timed it out, instead of failing just that one job with a
        # readable error_message.
        raise RuntimeError(
            "Missing required environment variable: " + name + "\n"
            "    Set it in the environment, or in .env.local for local runs."
        )
    return value


def make_agentrouter_client() -> Any:
    from openai import OpenAI

    return OpenAI(
        api_key=require_env("AGENTROUTER_API_KEY"),
        base_url=os.environ.get("AGENTROUTER_BASE_URL") or DEFAULT_BASE_URL,
        # AgentRouter rejects requests that do not identify the calling client
        # with a 401 "unauthorized client detected", so these are required, not
        # decorative. Sent on every request via default_headers.
        default_headers={
            "User-Agent": AGENTROUTER_USER_AGENT,
            "Originator": AGENTROUTER_ORIGINATOR,
        },
    )


def model_name() -> str:
    """Analysis model. Overridable so the provider can change without a code edit."""
    return os.environ.get("LLM_MODEL") or MODEL


def make_llm_client() -> Any:
    """Build the clip-analysis client, from whichever provider is configured.

    AgentRouter is the default, but it is not a hard dependency: it answers every
    request with 401 "unauthorized client detected" regardless of key or headers,
    so the analysis stage has to be able to run against any OpenAI-compatible
    endpoint (OpenAI, DeepSeek direct, OpenRouter, Groq, ...).

    Selection:
      - LLM_BASE_URL and/or LLM_API_KEY set -> that endpoint, with NO AgentRouter
        headers (they are AgentRouter-specific and confuse other providers).
      - otherwise -> AgentRouter, exactly as before, so nothing changes if the
        account is unblocked.
    """
    key = os.environ.get("LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL")

    if not key and not base:
        return make_agentrouter_client()

    if not key:
        raise RuntimeError(
            "LLM_BASE_URL is set but LLM_API_KEY is not; both are required to "
            "use a non-AgentRouter provider."
        )

    from openai import OpenAI

    return OpenAI(api_key=key, base_url=base or None)


def make_openai_client_or_none() -> Any:
    """Whisper fallback client. Optional -- captions may not need it at all."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=key)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_timestamped_transcript(segments: Sequence[dict], block_seconds: float = 20.0) -> str:
    """Render segments as '[HH:MM:SS.mmm -> HH:MM:SS.mmm] text' lines.

    Consecutive segments are coalesced into ~block_seconds blocks. Caption tracks
    emit a snippet every 2-3 seconds, and one prompt line per snippet would spend
    most of the context window on timestamps instead of words.
    """
    lines: list[str] = []
    buf: list[str] = []
    block_start: float | None = None
    block_end: float | None = None

    def flush() -> None:
        nonlocal buf, block_start, block_end
        if buf and block_start is not None and block_end is not None:
            text = " ".join(buf).strip()
            if text:
                lines.append(
                    "[" + format_timestamp(block_start) + " -> "
                    + format_timestamp(block_end) + "] " + text
                )
        buf, block_start, block_end = [], None, None

    for seg in segments:
        if block_start is None:
            block_start = seg["start"]
        buf.append(seg["text"])
        block_end = seg["end"]
        if block_end - block_start >= block_seconds:
            flush()

    flush()
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def window_segments(
    segments: Sequence[dict],
    window_seconds: float = WINDOW_SECONDS,
    overlap_seconds: float = OVERLAP_SECONDS,
) -> list[list[dict]]:
    """Split segments into overlapping time windows.

    Used only when the full transcript will not fit the context. Overlap exists so
    a moment spanning a boundary is still fully present in one of the windows.
    """
    if not segments:
        return []

    windows: list[list[dict]] = []
    start_idx = 0

    while start_idx < len(segments):
        window_start = segments[start_idx]["start"]
        window_end = window_start + window_seconds

        end_idx = start_idx
        while end_idx < len(segments) and segments[end_idx]["start"] < window_end:
            end_idx += 1

        windows.append(list(segments[start_idx:end_idx]))
        if end_idx >= len(segments):
            break

        boundary = window_end - overlap_seconds
        next_start = start_idx
        while next_start < end_idx and segments[next_start]["start"] < boundary:
            next_start += 1
        if next_start <= start_idx:
            next_start = end_idx  # guarantee forward progress
        start_idx = next_start

    return windows


def _user_prompt(
    segments: Sequence[dict],
    duration: float | None,
    chat_log: Any = None,
    part: tuple[int, int] | None = None,
) -> str:
    rendered = build_timestamped_transcript(segments)
    if duration:
        duration_line = (
            f"The full video is {format_timestamp(duration)} ({duration:.1f} seconds) "
            f"long. start_time and end_time must both fall within 0 and {duration:.1f}."
        )
    else:
        duration_line = (
            "The full video duration is unknown; keep clip boundaries within the "
            "supplied timestamps."
        )

    header = "Analyse the following timestamped transcript and extract all viral clips."
    if part:
        header = (
            f"This is part {part[0]} of {part[1]} of a longer video's transcript. "
            "Extract clips found WITHIN THIS PART ONLY, using the absolute "
            "timestamps shown."
        )

    chat_block = ""
    if chat_log:
        chat_block = (
            "\n\nStream chat data (use spikes to validate hype/funny timestamps):\n"
            + (chat_log if isinstance(chat_log, str) else json.dumps(chat_log))
        )

    return (
        header + "\n" + duration_line
        + "\n\nTIMESTAMPED TRANSCRIPT:\n" + rendered + chat_block
    )


# ---------------------------------------------------------------------------
# Clip parsing and validation
# ---------------------------------------------------------------------------

def _parse_clips(raw_content: str) -> list[dict]:
    """Parse the model's reply into a list of clip dicts.

    Tolerates the two shapes a provider might return: the requested
    {"clips": [...]} object, or a bare array.
    """
    text = (raw_content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    def try_load(candidate: str) -> Any:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    parsed = try_load(text)

    if parsed is None:
        # Last resort: slice from the first bracket to the last.
        starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
        if starts:
            start = min(starts)
            end = max(text.rfind("}"), text.rfind("]"))
            if end > start:
                parsed = try_load(text[start:end + 1])

    if isinstance(parsed, dict):
        for key in ("clips", "results", "data"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _first_key(raw: dict, *names: str) -> Any:
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def normalize_clip(raw: dict, duration: float | None) -> dict | None:
    """Coerce one raw model clip into the canonical schema, or None if unusable."""
    if not isinstance(raw, dict):
        return None

    # Accept the legacy key spellings too, so an older reply still maps.
    start = _as_float(_first_key(raw, "start_time", "start"))
    end = _as_float(_first_key(raw, "end_time", "end"))
    if start is None or end is None:
        return None

    if end < start:
        start, end = end, start

    if duration:
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))

    if end - start < 5.0:
        return None

    score = _as_int(_first_key(raw, "virality_score", "score"))
    if score is None:
        score = 50
    score = max(0, min(100, score))

    return {
        "clip_number": 0,  # renumbered after sorting
        "title": str(_first_key(raw, "title") or "Untitled clip")[:200],
        "start_time": round(start, 3),
        "end_time": round(end, 3),
        "duration_seconds": round(end - start, 3),
        "hook_sentence": str(_first_key(raw, "hook_sentence", "hook") or ""),
        "virality_score": score,
        "rationale": str(_first_key(raw, "rationale") or ""),
        "genre_category": str(_first_key(raw, "genre_category", "genre") or "Other")[:60],
        "suggested_caption": str(_first_key(raw, "suggested_caption", "caption") or ""),
    }


# ---------------------------------------------------------------------------
# Timestamp grounding
# ---------------------------------------------------------------------------
#
# Measured against a real 12-minute talk, the model's start_times were absent
# from the transcript entirely for 6 clips out of 10, and landed on the moment
# they described for only 2. Tightening the prompt helps, but adherence varies
# by model and this cannot be left to the prompt alone.
#
# The hooks themselves were accurate: all 10 fuzzy-matched a real window in the
# transcript at 100%. So the code locates each hook and moves the clip onto it,
# which makes the timestamps truthful regardless of which model produced them.

# Words too common to tell one moment from another.
_GROUNDING_STOPWORDS = frozenset(
    "a an the and or but to of in on at it i you that was is be had have my me "
    "with for as so what would will been were there this he she they we not do "
    "did if".split()
)

# How much speech a hook may span, and how much of it must be present for the
# match to count. Below the ratio, the clip keeps the model's own timing.
_GROUNDING_WINDOW = 12.0
_GROUNDING_MIN_RATIO = 0.55


def _content_words(text: Any) -> set:
    return {
        word
        for word in re.findall(r"[a-z0-9']+", str(text).lower())
        if word not in _GROUNDING_STOPWORDS
    }


def locate_phrase(
    phrase: Any,
    segments: Sequence[dict],
    hint: float | None = None,
    window: float = _GROUNDING_WINDOW,
) -> float | None:
    """Return the second at which `phrase` is really spoken, or None if nowhere.

    Scores a sliding `window`-second window by the fraction of the phrase's
    content words it contains. Ties go to the occurrence nearest `hint` (the
    model's own estimate): a phrase like "the number needed to treat" can recur,
    and the model at least tends to pick the right region even when its number
    is wrong outright.
    """
    target = _content_words(phrase)
    if not target or not segments:
        return None

    best_ratio = 0.0
    best_starts: list[float] = []

    for index, segment in enumerate(segments):
        start = segment["start"]
        seen: set = set()
        for later in segments[index:]:
            if later["start"] > start + window:
                break
            seen |= _content_words(later.get("text", ""))
        ratio = len(target & seen) / len(target)
        if ratio > best_ratio:
            best_ratio, best_starts = ratio, [start]
        elif ratio == best_ratio and ratio > 0:
            best_starts.append(start)

    if best_ratio < _GROUNDING_MIN_RATIO:
        return None
    if hint is None:
        return best_starts[0]
    return min(best_starts, key=lambda value: abs(value - hint))


def _caption_end_at_or_after(segments: Sequence[dict], moment: float) -> float:
    """End of the first caption at or after `moment`, so clips don't cut mid-line."""
    for segment in segments:
        if segment["end"] >= moment:
            return float(segment["end"])
    return float(segments[-1]["end"]) if segments else moment


def ground_clips(
    clips: list[dict],
    segments: Sequence[dict],
    duration: float | None = None,
) -> tuple[list[dict], int, list[str]]:
    """Re-anchor each clip onto the moment its hook is actually spoken.

    The clip keeps its own intended length (clamped), only its position moves.
    Returns (clips, how many were re-anchored, titles of those that were not) --
    an unlocatable hook keeps the model's timing, because a clip in the wrong
    place is still worth more than a dropped one.
    """
    anchored = 0
    unlocated: list[str] = []

    for clip in clips:
        anchor = (
            clip.get("hook_sentence")
            or clip.get("rationale")
            or clip.get("title")
        )
        found = locate_phrase(anchor, segments, hint=clip["start_time"])
        if found is None:
            unlocated.append(clip.get("title") or "untitled")
            continue

        length = min(max(clip["end_time"] - clip["start_time"], 5.0), 90.0)
        end = _caption_end_at_or_after(segments, found + length)
        if end - found < 5.0:
            end = found + length
        if duration:
            end = min(end, duration)

        clip["start_time"] = round(found, 3)
        clip["end_time"] = round(end, 3)
        clip["duration_seconds"] = round(end - found, 3)
        anchored += 1

    return clips, anchored, unlocated


def _merge_overlapping(clips: list[dict]) -> list[dict]:
    """Drop clips overlapping an already-kept clip, keeping the higher score.

    Windowed analysis can surface the same moment twice, in the overlap region.
    """
    ordered = sorted(clips, key=lambda c: (-c["virality_score"], c["start_time"]))
    kept: list[dict] = []

    for candidate in ordered:
        clash = False
        for existing in kept:
            overlap = min(candidate["end_time"], existing["end_time"]) - max(
                candidate["start_time"], existing["start_time"]
            )
            if overlap <= 0:
                continue
            shorter = min(
                candidate["end_time"] - candidate["start_time"],
                existing["end_time"] - existing["start_time"],
            )
            # >60% of the shorter clip overlapping means "the same moment".
            if shorter > 0 and overlap / shorter > 0.6:
                clash = True
                break
        if not clash:
            kept.append(candidate)

    return kept


def validate_clips(
    raw_clips: list[dict],
    duration: float | None,
    segments: Sequence[dict] | None = None,
) -> list[dict]:
    """Normalise, ground onto the transcript, drop unusable clips, de-overlap, renumber."""
    normalised = [c for c in (normalize_clip(r, duration) for r in raw_clips) if c]
    if not normalised:
        return []

    if segments:
        normalised, anchored, unlocated = ground_clips(normalised, segments, duration)
        if unlocated:
            shown = ", ".join(unlocated[:3])
            more = f" (+{len(unlocated) - 3} more)" if len(unlocated) > 3 else ""
            print(
                f"[ground] {anchored}/{len(normalised)} clips re-anchored to the "
                f"transcript; hook not found for: {shown}{more}"
            )
        else:
            print(f"[ground] all {anchored} clips re-anchored to the transcript")

    deduped = _merge_overlapping(normalised)
    deduped.sort(key=lambda c: c["start_time"])

    for index, clip in enumerate(deduped, start=1):
        clip["clip_number"] = index
    return deduped


def _analyze_window(
    client: Any,
    segments: Sequence[dict],
    duration: float | None,
    chat_log: Any,
    part: tuple[int, int] | None,
) -> list[dict]:
    request = {
        "model": model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(segments, duration, chat_log, part)},
        ],
        "temperature": 0.3,
    }

    try:
        # Object mode, because the prompt asks for {"clips": [...]}. The old code
        # requested object mode while prompting for a bare array, which strict
        # providers reject outright.
        response = client.chat.completions.create(
            **request, response_format={"type": "json_object"}
        )
    except Exception as exc:  # noqa: BLE001
        # Not every OpenAI-compatible provider implements JSON mode. The prompt
        # already specifies the shape and _parse_clips tolerates fenced or
        # prose-wrapped output, so retry once without it rather than losing the
        # window. Anything else is a real error and propagates.
        if "response_format" not in str(exc).lower():
            raise
        response = client.chat.completions.create(**request)

    content = response.choices[0].message.content or ""
    return _parse_clips(content)


def analyze_clips(
    segments: Sequence[dict],
    duration: float | None,
    chat_log: Any = None,
    client: Any = None,
) -> list[dict]:
    """Select viral clips from a timestamped transcript."""
    if not segments:
        raise RuntimeError("cannot analyse an empty transcript")

    client = client or make_llm_client()

    rendered = build_timestamped_transcript(segments)
    if estimate_tokens(rendered) <= MAX_TRANSCRIPT_TOKENS:
        windows: list[list[dict]] = [list(segments)]
    else:
        windows = window_segments(segments)
        print(
            f"[analyse] transcript is ~{estimate_tokens(rendered):,} tokens; "
            f"splitting into {len(windows)} overlapping windows"
        )

    collected: list[dict] = []
    failures: list[str] = []
    for index, window in enumerate(windows, start=1):
        part = (index, len(windows)) if len(windows) > 1 else None
        try:
            collected.extend(_analyze_window(client, window, duration, chat_log, part))
        except Exception as exc:  # noqa: BLE001 - one bad window must not lose the rest
            failures.append(f"window {index}/{len(windows)}: {exc}")
            print(f"[analyse] window {index}/{len(windows)} failed: {exc}")
        if len(windows) > 1:
            print(f"[analyse] window {index}/{len(windows)} -> {len(collected)} clips so far")

    clips = validate_clips(collected, duration, segments)

    # Surface the real cause instead of returning an empty list. Otherwise the
    # job is recorded as failed with a generic "no usable clips", while the
    # actual reason (an expired API key, a rate limit, a provider outage) stays
    # buried in the Actions log and never reaches the UI.
    if not clips and failures:
        detail = "; ".join(failures[:3])
        if len(failures) > 3:
            detail += f" (+{len(failures) - 3} more)"
        raise RuntimeError(f"clip analysis produced nothing: {detail}")

    return clips


# ---------------------------------------------------------------------------
# Supabase queue
# ---------------------------------------------------------------------------

def make_supabase_client() -> Any:
    from supabase import create_client

    return create_client(
        require_env("SUPABASE_URL"),
        require_env("SUPABASE_SERVICE_ROLE_KEY"),
    )


def reap_stale_jobs(
    supabase: Any,
    stale_minutes: int = STALE_AFTER_MINUTES,
    max_attempts: int = MAX_ATTEMPTS,
) -> int | None:
    """Reset orphaned jobs; fail the run if the queue contract is unavailable."""
    try:
        result = supabase.rpc("reap_stale_jobs", {
            "stale_after": f"{stale_minutes} minutes",
            "max_attempts": max_attempts,
        }).execute()
        return result.data
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Queue recovery RPC unavailable; check the approved queue setup") from exc


def claim_job(supabase: Any) -> dict | None:
    """Atomically claim one pending job, or None if the queue is empty."""
    try:
        data = supabase.rpc("claim_job").execute().data
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Queue claim RPC unavailable; check the approved queue setup") from exc

    # `returns setof` would give a list; `returns clip_jobs` gives a dict.
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


def process_video_job(supabase: Any, job: dict, openai_client: Any = None) -> list | None:
    """Run one claimed job. Returns the clips on success, else None."""
    job_id = job.get("id")
    url = job.get("video_url")
    attempt = job.get("attempts")
    owner = job.get("user_id")
    try:
        UUID(str(job_id))
        UUID(str(owner))
    except ValueError:
        print("[job] missing or invalid job owner/id; skipping")
        return None
    if not url or not isinstance(attempt, int) or attempt < 1:
        print("[job] malformed queue row; skipping")
        return None

    print(f"[job {job_id}] processing attempt {attempt}")
    started = time.monotonic()
    uploaded = []
    bucket_name = os.environ.get("CLIPS_STORAGE_BUCKET") or "clips"

    try:
        with media.job_deadline(), tempfile.TemporaryDirectory(prefix="clipper-job-") as directory:
            bucket = supabase.storage.get_bucket(bucket_name)
            is_public = bucket.get("public") if isinstance(bucket, dict) else getattr(bucket, "public", None)
            if is_public is not False:
                raise RuntimeError("Clip storage bucket is missing or is not private")
            rendered, transcript_source = build_video(url, Path(directory), openai_client)
            clips = []
            for path, metadata in rendered:
                object_path = f"{owner}/{job_id}/attempt-{attempt}/clip-{metadata['clip_number']}.mp4"
                # Include the path before upload: a lost response may still have stored it.
                uploaded.append(object_path)
                with path.open("rb") as handle:
                    supabase.storage.from_(bucket_name).upload(object_path, handle, {
                        "content-type": "video/mp4", "upsert": "false", "cache-control": "300",
                    })
                clips.append({**metadata, "storage_bucket": bucket_name, "object_path": object_path})

            saved = supabase.table("clip_jobs").update({
                "status": "completed", "clips": clips,
                "transcript_source": transcript_source, "error_message": None,
                "processing_started_at": None,
            }).eq("id", job_id).eq("user_id", owner).eq("status", "processing").eq("attempts", attempt).execute()

        # A reaper/reclaim can invalidate this attempt while analysis is running.
        # Never let its late result overwrite a newer attempt's state or clips.
        if not saved.data:
            print(f"[job {job_id}] attempt {attempt} no longer owns the job; result discarded")
            cleanup_attempt(supabase, job, bucket_name, uploaded)
            return None

        print(f"[job {job_id}] completed: {len(clips)} clips in {time.monotonic() - started:.0f}s")
        return clips

    except Exception as exc:  # noqa: BLE001 - a failure fails the job, not the run
        message = str(exc)[:2000]
        for name in ("SUPABASE_SERVICE_ROLE_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "AGENTROUTER_API_KEY"):
            secret = os.environ.get(name)
            if secret:
                message = message.replace(secret, "[redacted]")
        print(f"[job {job_id}] FAILED: {message}")
        try:
            saved = supabase.table("clip_jobs").update({
                "status": "failed",
                "error_message": message,
                "clips": [], "processing_started_at": None,
            }).eq("id", job_id).eq("user_id", owner).eq("status", "processing").eq("attempts", attempt).execute()
            if not saved.data:
                print(f"[job {job_id}] attempt {attempt} no longer owns the job; failure discarded")
        except Exception as update_exc:  # noqa: BLE001
            print(f"[job {job_id}] could not record failure: {update_exc}")
        cleanup_attempt(supabase, job, bucket_name, uploaded)
        return None


def build_video(url: str, workdir: Path, openai_client: Any = None):
    source = media.download_source(url, workdir / "source.video")
    _, _, duration = media.probe(source)
    if duration > media.MAX_SOURCE_SECONDS:
        raise ValueError("Source video exceeds the 30 minute limit")
    if any(os.environ.get(name) for name in ("LLM_API_KEY", "AGENTROUTER_API_KEY", "LLM_BASE_URL")):
        result = transcript_mod.get_transcript(url, openai_client=openai_client,
                                               workdir=str(workdir), source_path=str(source))
        suggestions = analyze_clips(result["segments"], duration)
        for clip in suggestions:
            clip["selection_source"] = "ai"
        transcript_source = result["source"]
    else:
        suggestions = media.preview_timestamps(duration)
        transcript_source = "preview"
    return media.render_clips(source, suggestions, workdir), transcript_source


def cleanup_attempt(supabase: Any, job: dict, bucket: str, paths: list[str]):
    if not paths:
        return
    try:
        # A commit can succeed while its response is lost. Retain referenced
        # objects; if the database is unavailable, defer cleanup rather than
        # deleting clips that may already be visible to the user.
        result = supabase.table("clip_jobs").select("clips").eq("id", job["id"]).execute()
        referenced = {clip.get("object_path") for row in (result.data or [])
                      for clip in (row.get("clips") or []) if isinstance(clip, dict)}
        unused = [path for path in paths if path not in referenced]
        if unused:
            supabase.storage.from_(bucket).remove(unused)
    except Exception:
        print(f"[job {job['id']}] storage cleanup deferred; inspect attempt-{job['attempts']} objects")


def run_queue(
    supabase: Any,
    openai_client: Any = None,
    max_jobs: int = MAX_JOBS_PER_RUN,
    budget_seconds: float = RUN_BUDGET_SECONDS,
) -> int:
    """Process queued jobs until the queue empties or a limit is reached."""
    reaped = reap_stale_jobs(supabase)
    if reaped:
        print(f"[reaper] reset {reaped} stale job(s)")

    deadline = time.monotonic() + budget_seconds
    processed = 0

    while processed < max_jobs:
        if time.monotonic() + media.JOB_BUDGET_SECONDS >= deadline:
            print(
                f"[queue] time budget of {budget_seconds / 60:.0f} min reached; "
                f"stopping cleanly after {processed} job(s)"
            )
            break

        job = claim_job(supabase)
        if not job:
            print("[queue] no pending jobs")
            break

        process_video_job(supabase, job, openai_client)
        processed += 1

    print(f"[queue] run complete: {processed} job(s) processed")
    return processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def dry_run(
    url: str,
    as_json: bool = False,
    openai_client: Any = None,
    transcript_only: bool = False,
) -> int:
    """Process a single URL locally, with no Supabase dependency.

    This is the Phase 1 acceptance test: it should print clips whose timestamps
    actually land on the moments their titles describe.
    """
    print(f"[dry-run] retrieving transcript for {url}")
    try:
        result = transcript_mod.get_transcript(url, openai_client=openai_client)
    except TranscriptUnavailable as exc:
        print(f"[dry-run] FAILED to get a transcript: {exc}", file=sys.stderr)
        return 1

    print(
        f"[dry-run] source={result['source']} language={result.get('language')} "
        f"duration={result.get('duration')} segments={len(result['segments'])}"
    )

    if transcript_only:
        print(build_timestamped_transcript(result["segments"]))
        return 0

    try:
        clips = analyze_clips(result["segments"], result.get("duration"))
    except Exception as exc:  # noqa: BLE001 - report cleanly, not as a traceback
        print(f"[dry-run] FAILED to analyse: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(
            {"source": result["source"], "duration": result.get("duration"), "clips": clips},
            indent=2,
        ))
        return 0 if clips else 1

    if not clips:
        print("[dry-run] no clips returned", file=sys.stderr)
        return 1

    print(f"\n[dry-run] {len(clips)} clip(s):\n")
    for clip in clips:
        print(
            f"  #{clip['clip_number']}  score {clip['virality_score']:>3}  "
            f"{format_timestamp(clip['start_time'])} -> {format_timestamp(clip['end_time'])} "
            f"({clip['duration_seconds']:.1f}s)  [{clip['genre_category']}]"
        )
        print(f"      {clip['title']}")
        if clip["hook_sentence"]:
            print(f"      hook: {clip['hook_sentence']}")
        if clip["rationale"]:
            print(f"      why:  {clip['rationale']}")
        print()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worker.py",
        description="ToolStack Clipper worker (Supabase queue, or a local dry run).",
    )
    parser.add_argument("--url", help="Dry-run one URL locally; no Supabase needed.")
    parser.add_argument("--render-dir", help="With --url: download and render actual MP4s here without Supabase.")
    parser.add_argument("--json", action="store_true", help="With --url: print JSON.")
    parser.add_argument(
        "--transcript-only", action="store_true",
        help="With --url: print the timestamped transcript and stop.",
    )
    parser.add_argument(
        "--max-jobs", type=int, default=MAX_JOBS_PER_RUN,
        help=f"Max jobs per queue run (default {MAX_JOBS_PER_RUN}).",
    )
    parser.add_argument(
        "--budget-minutes", type=float, default=RUN_BUDGET_SECONDS / 60,
        help="Wall-clock budget for a queue run, in minutes.",
    )
    parser.add_argument(
        "--env-file", default=".env.local",
        help="Env file to load for local runs (default .env.local).",
    )
    args = parser.parse_args(argv)

    load_dotenv(args.env_file)
    openai_client = make_openai_client_or_none()

    if args.url:
        if args.render_dir:
            output = Path(args.render_dir).resolve()
            output.mkdir(parents=True, exist_ok=True)
            with media.job_deadline(), tempfile.TemporaryDirectory(prefix="clipper-local-") as directory:
                rendered, source = build_video(args.url, Path(directory), openai_client)
                import shutil
                clips = []
                for path, metadata in rendered:
                    shutil.copy2(path, output / path.name)
                    clips.append({**metadata, "file": path.name})
                (output / "clips.json").write_text(json.dumps({"source": source, "clips": clips}, indent=2))
            print(f"Rendered {len(clips)} MP4 clips in {output}")
            return 0
        if not openai_client and transcript_mod.youtube_video_id(args.url) is None:
            print(
                "[!] OPENAI_API_KEY is not set and this is not a YouTube URL, so the "
                "Whisper fallback is unavailable.",
                file=sys.stderr,
            )
        return dry_run(args.url, args.json, openai_client, args.transcript_only)

    supabase = make_supabase_client()
    run_queue(
        supabase,
        openai_client,
        max_jobs=args.max_jobs,
        budget_seconds=args.budget_minutes * 60,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except RuntimeError as exc:
        # Startup/configuration problems (a missing key, no Supabase credentials)
        # should read as a message, not a traceback.
        print(f"[!] {exc}", file=sys.stderr)
        sys.exit(1)

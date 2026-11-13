"""Transcript retrieval that PRESERVES TIMESTAMPS.

Phase 1 of the clipper pipeline. Before this module, worker.py fetched a
verbose_json transcript from Whisper and then passed only `transcript.text` to
the LLM -- so the model was asked for second-accurate start/end times while
being given no time information at all, and every timestamp it returned was
invented. Everything here exists to keep the timings attached to the text.

Strategy, in order:
  1. YouTube captions  -- free, instant, real millisecond timings, no download,
                          and immune to Whisper's 25 MB upload limit.
  2. Whisper           -- fallback for uploads, non-YouTube URLs, and videos
                          whose captions are disabled or blocked.

Any failure on the captions path falls through to Whisper. That is not
defensive padding: YouTube actively rate-limits and IP-blocks datacenter ranges,
and GitHub Actions runners are datacenter IPs, so this fallback fires routinely.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Iterable, Sequence

# OpenAI's hard cap on the audio upload endpoint.
WHISPER_MAX_BYTES = 25 * 1024 * 1024

# Whisper internally resamples to 16 kHz mono, so we extract to that shape
# directly: it is ~4x smaller than 192 kbps stereo with no loss of usable signal.
AUDIO_SAMPLE_RATE = 16000
AUDIO_BITRATE = "48k"

# At 48 kbps mono (~6 kB/s), a 60-minute chunk is ~21.6 MB -- comfortably inside
# the 25 MB cap. Anything longer is split; anything shorter is a single chunk.
CHUNK_SECONDS = 3600

DEFAULT_LANGUAGES = ("en", "en-US", "en-GB")

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TranscriptUnavailable(RuntimeError):
    """Neither captions nor Whisper could produce a timestamped transcript."""


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def youtube_video_id(url: str) -> str | None:
    """Extract a YouTube video id, or None if this is not a YouTube URL.

    Handles watch?v=, youtu.be/, /shorts/, /embed/, /live/ and a bare id.
    """
    if not url:
        return None
    url = url.strip()

    if _YT_ID_RE.match(url):
        return url

    from urllib.parse import parse_qs, urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if _YT_ID_RE.match(candidate) else None

    if host in {"youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
            return candidate if _YT_ID_RE.match(candidate) else None
        match = re.match(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})", parsed.path)
        if match:
            return match.group(1)

    return None


# ---------------------------------------------------------------------------
# Segment helpers
# ---------------------------------------------------------------------------

def _coerce_segment(raw: Any) -> dict | None:
    """Normalise one caption/Whisper snippet into {start, end, text}.

    Written against BOTH shapes because the two libraries disagree: the
    youtube-transcript-api returns dicts in 0.6.x and attribute-bearing objects
    in 1.x, and the OpenAI SDK returns pydantic models rather than dicts.
    """
    def field(name: str, default: Any = None) -> Any:
        if isinstance(raw, dict):
            return raw.get(name, default)
        return getattr(raw, name, default)

    start = field("start")
    if start is None:
        return None

    text = (field("text") or "").strip()
    if not text:
        return None

    end = field("end")
    if end is None:
        # Captions give `duration`; Whisper gives `end`.
        duration = field("duration") or 0.0
        end = float(start) + float(duration)

    start_f, end_f = float(start), float(end)
    if end_f < start_f:
        start_f, end_f = end_f, start_f
    return {"start": round(start_f, 3), "end": round(end_f, 3), "text": text}


def _normalise_segments(raw_segments: Iterable[Any]) -> list[dict]:
    out = []
    for raw in raw_segments or []:
        seg = _coerce_segment(raw)
        if seg:
            out.append(seg)
    out.sort(key=lambda s: s["start"])
    return out


def join_text(segments: Sequence[dict]) -> str:
    return " ".join(s["text"] for s in segments).strip()


def format_timestamp(seconds: float) -> str:
    """83.4 -> '00:01:23.400'. Millisecond precision; clip selection depends on it."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"


# ---------------------------------------------------------------------------
# Source 1: YouTube captions
# ---------------------------------------------------------------------------

def _captions_segments(video_id: str, languages: Sequence[str]) -> tuple[list[dict], str | None]:
    """Fetch caption segments, honouring `languages` and preferring manual tracks.

    Returns (segments, language_code). `language_code` is the track actually
    used, which may differ from the request if no requested language exists --
    callers must report what they got, not what they asked for.

    Tries each known API generation in turn and reports every failure at once,
    so a version mismatch produces an actionable message instead of a bare
    AttributeError.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise TranscriptUnavailable(
            "youtube-transcript-api is not installed (pip install -r requirements.txt)"
        ) from exc

    errors: list[str] = []
    lang_list = [str(l).lower() for l in languages]

    def language_rank(code: Any) -> int:
        """How well `code` matches the requested language preference list.

        Lower is better; len(lang_list) means "not requested at all". A regional
        variant ("en-GB") matches a bare request ("en") via its base subtag.
        """
        code = (code or "").lower() if isinstance(code, str) else ""
        if not code:
            return len(lang_list)
        if code in lang_list:
            return lang_list.index(code)
        base = code.split("-")[0]
        for index, pref in enumerate(lang_list):
            if pref.split("-")[0] == base:
                return index
        return len(lang_list)

    # --- 1.x: list() exposes language_code and is_generated, so we can honour
    # the language preference AND prefer human-authored tracks over ASR.
    #
    # Language outranks provenance: an auto-generated English track beats a
    # hand-written Arabic one when English was requested. Sorting only on
    # is_generated (as an earlier version did) returns whichever track YouTube
    # happens to list first -- an English request for a TED talk silently
    # yielded the Arabic translation, and the model then wrote Arabic clips.
    try:
        api = YouTubeTranscriptApi()
        listing = api.list(video_id)

        ranked = sorted(
            list(listing),
            key=lambda t: (
                language_rank(getattr(t, "language_code", None)),
                # Unknown provenance is treated as generated, which ranks it lower.
                bool(getattr(t, "is_generated", True)),
            ),
        )

        for transcript in ranked:
            code = getattr(transcript, "language_code", None)
            try:
                segments = _normalise_segments(transcript.fetch())
            except Exception as exc:
                errors.append(f"fetch({code or '?'}): {exc}")
                continue
            if segments:
                return segments, code
    except Exception as exc:
        errors.append(f"list(): {exc}")

    # --- 1.x: direct fetch with a language preference list.
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=lang_list)
        segments = _normalise_segments(getattr(fetched, "snippets", fetched))
        if segments:
            return segments, getattr(fetched, "language_code", None)
    except Exception as exc:
        errors.append(f"fetch(languages=...): {exc}")

    # --- 0.6.x: module-level classmethod returning a list of dicts.
    try:
        legacy = getattr(YouTubeTranscriptApi, "get_transcript", None)
        if legacy is not None:
            segments = _normalise_segments(legacy(video_id, languages=lang_list))
            if segments:
                return segments, None
    except Exception as exc:
        errors.append(f"get_transcript(): {exc}")

    raise TranscriptUnavailable("captions unavailable: " + ("; ".join(errors) or "no tracks"))


# ---------------------------------------------------------------------------
# Source 2: yt-dlp + Whisper
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _probe_duration(path: str) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ])
    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        return 0.0


def _download_audio(url: str, workdir: str) -> str:
    """Download audio only, normalised to the shape Whisper wants anyway."""
    import yt_dlp

    out_template = os.path.join(workdir, "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "48",
            "postprocessor_args": [
                "-ac", "1",
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-b:a", AUDIO_BITRATE,
            ],
        }],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    for name in os.listdir(workdir):
        if name.startswith("audio."):
            return os.path.join(workdir, name)

    raise TranscriptUnavailable("yt-dlp produced no audio file")


def _split_audio(audio_path: str, workdir: str) -> list[tuple[str, float]]:
    """Split into <=CHUNK_SECONDS pieces, returning [(path, start_offset_seconds)].

    Chunking is what makes long videos work at all: Whisper rejects uploads over
    25 MB, which a 2-hour file blows past several times over. Each chunk is
    re-encoded (not stream-copied) so the cut lands on an exact boundary and the
    offset we add back to every timestamp is accurate.
    """
    total = _probe_duration(audio_path)
    if total <= 0:
        raise TranscriptUnavailable("could not determine audio duration with ffprobe")

    chunks: list[tuple[str, float]] = []
    offset = 0.0
    index = 0
    while offset < total:
        chunk_path = os.path.join(workdir, f"chunk_{index:03d}.mp3")
        result = _run([
            "ffmpeg", "-nostdin", "-y",
            "-ss", f"{offset:.3f}",
            "-t", f"{CHUNK_SECONDS:.3f}",
            "-i", audio_path,
            "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE), "-b:a", AUDIO_BITRATE,
            chunk_path,
        ])
        if result.returncode != 0 or not os.path.exists(chunk_path):
            raise TranscriptUnavailable(
                f"ffmpeg failed to split audio at {offset:.1f}s: {result.stderr[-400:]}"
            )

        size = os.path.getsize(chunk_path)
        if size > WHISPER_MAX_BYTES:
            raise TranscriptUnavailable(
                f"audio chunk is {size / 1e6:.1f} MB, over Whisper's "
                f"{WHISPER_MAX_BYTES / 1e6:.0f} MB limit; lower AUDIO_BITRATE or CHUNK_SECONDS"
            )

        # A chunk shorter than a second at the tail is ffmpeg rounding noise.
        if _probe_duration(chunk_path) < 0.5 and chunks:
            os.remove(chunk_path)
            break

        chunks.append((chunk_path, offset))
        offset += CHUNK_SECONDS
        index += 1

    if not chunks:
        raise TranscriptUnavailable("audio produced no usable chunks")
    return chunks


def _whisper_segments(openai_client: Any, audio_path: str) -> list[dict]:
    with open(audio_path, "rb") as handle:
        result = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=handle,
            response_format="verbose_json",
        )

    raw = getattr(result, "segments", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("segments")

    segments = _normalise_segments(raw or [])
    if not segments:
        # Returning flat text here would silently reintroduce the original bug:
        # a transcript with no timings, and therefore invented clip boundaries.
        raise TranscriptUnavailable(
            "Whisper returned no segment timings; refusing to continue without them"
        )
    return segments


def _from_whisper(url: str, workdir: str, openai_client: Any, source_path: str | None = None) -> list[dict]:
    if openai_client is None:
        raise TranscriptUnavailable("no OpenAI client available for Whisper fallback")

    if source_path:
        audio_path = os.path.join(workdir, "audio.mp3")
        rendered = _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
                         "-i", source_path, "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
                         "-b:a", AUDIO_BITRATE, audio_path])
        if rendered.returncode:
            raise TranscriptUnavailable("Source video has no transcribable audio")
    else:
        audio_path = _download_audio(url, workdir)
    segments: list[dict] = []
    for chunk_path, offset in _split_audio(audio_path, workdir):
        for seg in _whisper_segments(openai_client, chunk_path):
            segments.append({
                "start": round(seg["start"] + offset, 3),
                "end": round(seg["end"] + offset, 3),
                "text": seg["text"],
            })
    return segments


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------

def _probe_metadata_duration(url: str) -> float | None:
    """Total length from metadata only -- no audio download."""
    try:
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        duration = info.get("duration")
        return float(duration) if duration else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_transcript(
    url: str,
    *,
    openai_client: Any = None,
    workdir: str | None = None,
    languages: Sequence[str] = DEFAULT_LANGUAGES,
    source_path: str | None = None,
) -> dict:
    """Return {source, language, duration, segments, text} for `url`.

    Raises TranscriptUnavailable if neither source yields timed segments.
    """
    import tempfile

    video_id = youtube_video_id(url)
    caption_error: str | None = None

    # --- Source 1: captions
    if video_id:
        try:
            segments, language = _captions_segments(video_id, languages)
            duration = _probe_metadata_duration(url)
            if not duration:
                duration = segments[-1]["end"] if segments else None
            return {
                "source": "youtube_captions",
                # The track actually used, not `languages[0]`: the caption list
                # may have had no match for the request, and recording the
                # requested language would misreport what the model was given.
                "language": language,
                "duration": duration,
                "segments": segments,
                "text": join_text(segments),
            }
        except TranscriptUnavailable as exc:
            caption_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - fall through to Whisper by design
            caption_error = f"unexpected captions failure: {exc}"

    # --- Source 2: Whisper
    owns_workdir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="clipper-transcript-")
    try:
        segments = _from_whisper(url, workdir, openai_client, source_path)
    except Exception as exc:  # noqa: BLE001 - reported below with caption context
        message = f"whisper fallback failed: {exc}"
        if caption_error:
            message = f"captions failed ({caption_error}); {message}"
        raise TranscriptUnavailable(message) from exc
    finally:
        if owns_workdir:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)

    duration = segments[-1]["end"] if segments else None
    if not duration:
        duration = _probe_metadata_duration(url)

    return {
        "source": "whisper",
        "language": None,
        "duration": duration,
        "segments": segments,
        "text": join_text(segments),
    }

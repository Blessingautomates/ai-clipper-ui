"""Bounded direct-URL download and local-only FFmpeg rendering for the MVP."""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import json
import math
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.parse import urljoin, urlsplit

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_SECONDS = 30 * 60
JOB_BUDGET_SECONDS = 20 * 60


@contextlib.contextmanager
def job_deadline(seconds=JOB_BUDGET_SECONDS):
    """Actions/Linux worker: interrupt even a stalled external API request."""
    def expired(*_):
        raise TimeoutError('Video processing exceeded its time limit')
    old_handler = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, old_handler)


def public_target(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use a public HTTP or HTTPS direct video URL')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in (80, 443):
        raise ValueError('Video URL must use port 80 or 443')
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Video URL must resolve only to public internet addresses')
    return parsed, addresses[0][4][0], port


def download_source(url, destination: Path):
    """No proxies; pin each connection to its checked IP, retaining TLS SNI.

    Validate every redirect too. HTML pages/YouTube extraction are deliberately
    outside this first direct-file path; FFmpeg never receives a remote URL.
    """
    deadline = time.monotonic() + 300
    for _ in range(6):
        parsed, address, port = public_target(url)
        connection_type = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, port, timeout=30)
        connection._create_connection = lambda *_args, **_kwargs: socket.create_connection((address, port), timeout=30)
        try:
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            connection.request('GET', path, headers={'User-Agent': 'ToolStackClipper/1.0', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Video URL returned a redirect without a destination')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f'Video download returned HTTP {response.status}')
            if 'text/html' in (response.getheader('Content-Type') or '').lower():
                raise ValueError('This URL is a webpage. Please use a direct video file URL')
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_SOURCE_BYTES:
                raise ValueError('Source video exceeds the 256 MB limit')
            size = 0
            with destination.open('wb') as handle:
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError('Video download exceeded five minutes')
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_SOURCE_BYTES:
                        raise ValueError('Source video exceeds the 256 MB limit')
                    handle.write(block)
            if not size or (length and size != int(length)):
                raise ValueError('Video download is empty or incomplete')
            return destination
        finally:
            connection.close()
    raise ValueError('Video URL redirected too many times')


def run(command, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Avoid publishing raw FFmpeg output/URLs or provider credentials to jobs.
        raise RuntimeError(f'{Path(command[0]).name} could not process the video')
    return result.stdout


def probe(path: Path):
    info = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                           '-show_format', '-show_streams', '-of', 'json', str(path)]))
    duration = float(info.get('format', {}).get('duration', 0))
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Source has no playable video stream or valid duration')
    return info, video, duration


def preview_timestamps(duration):
    """Deterministic, honestly labelled selections when no AI keys are set."""
    count = min(3, max(1, int(duration // 5)))
    length = min(30.0, duration / count)
    return [{
        'clip_number': index + 1, 'title': f'Clip {index + 1}',
        'start_time': round(index * duration / count, 3),
        'end_time': round(min(duration, index * duration / count + length), 3),
        'selection_source': 'preview', 'suggested_caption': '',
    } for index in range(count)]


def render_clips(source: Path, suggestions, workdir: Path):
    _, _, source_duration = probe(source)
    if source_duration > MAX_SOURCE_SECONDS:
        raise ValueError('Source video exceeds the 30 minute limit')
    chosen = sorted(suggestions, key=lambda c: -(c.get('virality_score') or 0))[:3]
    rendered = []
    for index, suggestion in enumerate(chosen, 1):
        start, end = float(suggestion['start_time']), float(suggestion['end_time'])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or start >= source_duration:
            raise ValueError('Suggested clip timestamps are invalid')
        end = min(end, source_duration, start + 90)
        output = workdir / f'clip-{index}.mp4'
        run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
             '-ss', f'{start:.3f}', '-i', str(source), '-t', f'{end - start:.3f}',
             '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
             '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
             '-threads', '2', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
             '-map_metadata', '-1', str(output)], timeout=240)
        metadata = validate_mp4(output, end - start)
        rendered.append((output, {**suggestion, **metadata, 'clip_number': index,
                                  'start_time': start, 'end_time': end}))
    if not rendered:
        raise ValueError('No usable clip timestamps were suggested')
    return rendered


def validate_mp4(path: Path, expected_duration=None):
    info, video, duration = probe(path)
    audio = [s for s in info['streams'] if s.get('codec_type') == 'audio']
    if 'mp4' not in info['format']['format_name'] or video['codec_name'] != 'h264' or video.get('pix_fmt') != 'yuv420p':
        raise ValueError('Rendered clip is not a browser compatible MP4')
    if any(stream.get('codec_name') != 'aac' for stream in audio):
        raise ValueError('Rendered clip has incompatible audio')
    if expected_duration is not None and abs(duration - expected_duration) > 0.5:
        raise ValueError('Rendered clip duration differs from suggested timestamps')
    # Decode the entire result; metadata alone does not prove it plays.
    run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
         '-i', str(path), '-f', 'null', '-'], timeout=120)
    return {'duration_seconds': round(duration, 3), 'width': video['width'], 'height': video['height'],
            'size_bytes': path.stat().st_size, 'content_type': 'video/mp4',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

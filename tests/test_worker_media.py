"""Owned synthetic video: real HTTP download, FFmpeg cuts and storage bytes.

Only DNS validation is overridden for this loopback fixture. Production always
requires public addresses. Queue/storage doubles never touch Supabase resources.
"""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import media
import worker
from test_worker_queue import FakeQueue


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='clipper-owned-fixture-')
        cls.root = Path(cls.directory.name)
        cls.source = cls.root / 'owned.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'testsrc2=size=320x180:rate=24', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:sample_rate=44100', '-t', '18',
                        '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-movflags', '+faststart', str(cls.source)], check=True)
        cls.contents = cls.source.read_bytes()
        contents = cls.contents
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/owned.mp4')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(len(contents)))
                self.end_headers()
                self.wfile.write(contents)
            def log_message(self, *_):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/owned.mp4'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def fixture_target(self, url):
        parsed = urlsplit(url)
        return parsed, '127.0.0.1', self.server.server_port

    def test_url_to_three_real_uploaded_mp4s_and_local_cleanup(self):
        queue = FakeQueue()
        queue.row['video_url'] = self.url
        scratch = []
        download = media.download_source
        def capture(url, destination):
            scratch.append(destination.parent)
            return download(url, destination)
        with patch.dict(os.environ, {'LLM_API_KEY': '', 'AGENTROUTER_API_KEY': '', 'LLM_BASE_URL': '', 'CLIPS_STORAGE_BUCKET': 'clips'}), \
             patch.object(media, 'public_target', side_effect=self.fixture_target), \
             patch.object(media, 'download_source', side_effect=capture), \
             contextlib.redirect_stdout(io.StringIO()):
            clips = worker.process_video_job(queue, dict(queue.row))
        self.assertEqual(queue.row['status'], 'completed')
        self.assertEqual(len(clips), 3)
        self.assertTrue(all(not path.exists() for path in scratch))
        for clip in clips:
            self.assertEqual(clip['selection_source'], 'preview')
            uploaded = queue.storage.objects[clip['object_path']]
            self.assertEqual(hashlib.sha256(uploaded).hexdigest(), clip['sha256'])
            with tempfile.TemporaryDirectory() as directory:
                downloaded = Path(directory) / 'download.mp4'
                downloaded.write_bytes(uploaded)
                checked = media.validate_mp4(downloaded, 6)
                self.assertEqual((checked['width'], checked['height']), (320, 180))

    def test_silent_video_is_playable_without_an_audio_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'silent.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(self.source),
                            '-an', '-c:v', 'copy', str(source)], check=True)
            rendered = media.render_clips(source, [{'start_time': 2, 'end_time': 4}], Path(directory))
            self.assertEqual(len(rendered), 1)
            self.assertEqual(media.validate_mp4(rendered[0][0], 2)['width'], 320)

    def test_suggested_offsets_are_rendered_and_capped_at_three(self):
        suggestions = [{'title': str(i), 'start_time': i * 3, 'end_time': i * 3 + 2,
                        'virality_score': 90 - i} for i in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            results = media.render_clips(self.source, suggestions, Path(directory))
            self.assertEqual(len(results), 3)
            self.assertEqual([c['start_time'] for _, c in results], [0, 3, 6])
            # Compare the decoded first frame against the source at each cut.
            def frame(path, start):
                return subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path),
                                       '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                      capture_output=True, check=True).stdout
            for output, metadata in results:
                original, cut = frame(self.source, metadata['start_time']), frame(output, 0)
                self.assertEqual(len(original), len(cut))
                self.assertLess(sum(abs(a - b) for a, b in zip(original, cut)) / len(cut), 12)

    def test_failed_download_records_failure_and_cleans_scratch(self):
        queue = FakeQueue()
        scratch = []
        def fail(_url, destination):
            scratch.append(destination.parent)
            destination.write_bytes(b'partial')
            raise ValueError('download failed')
        with patch.object(media, 'download_source', side_effect=fail), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(worker.process_video_job(queue, dict(queue.row)))
        self.assertEqual(queue.row['status'], 'failed')
        self.assertEqual(queue.row['clips'], [])
        self.assertTrue(all(not path.exists() for path in scratch))

    def test_private_targets_and_credentials_rejected(self):
        for url in ('http://127.0.0.1/video', 'http://[::1]/video', 'http://169.254.169.254/video',
                    'file:///etc/passwd', 'https://user:pass@example.com/video', 'http://example.com:8080/video'):
            with self.assertRaises(ValueError):
                media.public_target(url)

    def test_private_redirect_target_is_rejected_before_connecting(self):
        actual = media.public_target
        def first_public(url):
            if url.endswith('/redirect'):
                return self.fixture_target(url)
            return actual(url)
        with tempfile.TemporaryDirectory() as directory, patch.object(media, 'public_target', side_effect=first_public):
            with self.assertRaises(ValueError):
                media.download_source(self.url.replace('/owned.mp4', '/redirect'), Path(directory) / 'source')

    def test_stream_size_limit_and_invalid_media(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(media, 'public_target', side_effect=self.fixture_target), \
             patch.object(media, 'MAX_SOURCE_BYTES', 10):
            with self.assertRaises(ValueError):
                media.download_source(self.url, Path(directory) / 'source')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.mp4'
            path.write_bytes(b'not a video')
            with self.assertRaises(RuntimeError):
                media.validate_mp4(path)

    def test_invalid_timestamps_fail_before_rendering(self):
        for start, end in ((float('nan'), 10), (-1, 10), (0, float('inf')), (20, 25), (5, 3)):
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                media.render_clips(self.source, [{'start_time': start, 'end_time': end}], Path(directory))

    def test_job_deadline_and_missing_time_budget(self):
        import time
        with self.assertRaises(TimeoutError), media.job_deadline(0.02):
            time.sleep(0.1)
        with patch.object(worker, 'reap_stale_jobs'), patch.object(worker, 'claim_job') as claim, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run_queue(FakeQueue(), budget_seconds=1), 0)
            claim.assert_not_called()


if __name__ == '__main__':
    unittest.main()

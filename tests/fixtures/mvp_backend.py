"""Local Supabase HTTP contract fixture. No real credentials or remote writes.

Runs the actual worker with the Python SDK. Used by mvp-workflow.test.mjs to
exercise the actual Next routes with the JS SDK, then download real MP4 bytes.
"""
import contextlib
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import media
import worker
from supabase import create_client

OWNER = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
TOKENS = {'owner-token': OWNER, 'other-token': OTHER}
SERVICE = 'fixture-service-key'
SIGN_KEY = b'local-only-signature-fixture'
root = Path(tempfile.mkdtemp(prefix='clipper-mvp-proof-'))
source = root / 'owned.mp4'
subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'testsrc2=size=320x180:rate=24', '-f', 'lavfi', '-i',
                'sine=frequency=440:sample_rate=44100', '-t', '18', '-c:v', 'libx264',
                '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(source)], check=True)
rows = []
lock = threading.RLock()
requests = queue.Queue()


def token_for(path, expires):
    signature = hmac.new(SIGN_KEY, f'{path}:{expires}'.encode(), hashlib.sha256).hexdigest()
    return f'{expires}.{signature}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def response(self, status, value):
        contents = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(contents)))
        self.end_headers()
        self.wfile.write(contents)

    def media_response(self, path, download=None):
        if not path.is_file():
            return self.response(404, {'message': 'missing object'})
        contents = path.read_bytes()
        start, end, status = 0, len(contents) - 1, 200
        if self.headers.get('Range'):
            values = self.headers['Range'].removeprefix('bytes=').split('-')
            start, end, status = int(values[0]), min(int(values[1] or end), end), 206
        self.send_response(status)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(contents)}')
        if download is not None:
            self.send_header('Content-Disposition', f'attachment; filename="{download or path.name}"')
        self.end_headers()
        self.wfile.write(contents[start:end + 1])

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def do_PATCH(self):
        self.handle_api()

    def do_DELETE(self):
        self.handle_api()

    def handle_api(self):
        parsed = urlsplit(self.path)
        path, params = unquote(parsed.path), parse_qs(parsed.query, keep_blank_values=True)
        token = self.headers.get('Authorization', '').removeprefix('Bearer ')
        owner = TOKENS.get(token)
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if path == '/owned.mp4' and self.command == 'GET':
            return self.media_response(source)
        if path == '/fixture/process':
            requests.put('process')
            return self.response(202, {})
        if path == '/fixture/stop':
            requests.put('stop')
            return self.response(200, {})
        if path == '/auth/v1/user':
            if not owner:
                return self.response(401, {'message': 'Unauthorized'})
            return self.response(200, {'id': owner, 'aud': 'authenticated', 'role': 'authenticated',
                                       'email': 'fixture@example.invalid', 'app_metadata': {}, 'user_metadata': {},
                                       'created_at': '2026-11-11T00:00:00Z'})
        if path.startswith('/rest/v1/'):
            with lock:
                if path.endswith('/rpc/reap_stale_jobs') and token == SERVICE:
                    return self.response(200, 0)
                if path.endswith('/rpc/claim_job') and token == SERVICE:
                    row = next((row for row in rows if row['status'] == 'pending'), None)
                    if row:
                        row.update(status='processing', attempts=row['attempts'] + 1,
                                   processing_started_at='2026-11-11T00:00:00Z')
                    return self.response(200, [row] if row else [])
                if path != '/rest/v1/clip_jobs' or (not owner and token != SERVICE):
                    return self.response(401, {'message': 'Unauthorized'})
                if self.command == 'POST':
                    inserts = json.loads(body)
                    inserts = inserts if isinstance(inserts, list) else [inserts]
                    for insert in inserts:
                        if insert.get('user_id') != owner or set(insert) != {'user_id', 'video_url'}:
                            return self.response(403, {'message': 'Owner/state rejected'})
                    created = []
                    for insert in inserts:
                        row = {**insert, 'id': str(uuid.uuid4()), 'status': 'pending', 'clips': [],
                               'attempts': 0, 'created_at': '2026-11-11T00:00:00Z', 'error_message': None}
                        rows.append(row)
                        created.append(row)
                    return self.response(201, created[0] if 'application/vnd.pgrst.object+json' in self.headers.get('Accept', '') else created)
                matches = [row for row in rows if token == SERVICE or row['user_id'] == owner]
                for name, values in params.items():
                    if values[0].startswith('eq.'):
                        matches = [row for row in matches if str(row.get(name)) == values[0][3:]]
                if self.command == 'PATCH':
                    if token != SERVICE:
                        return self.response(403, {'message': 'Worker only'})
                    for row in matches:
                        row.update(json.loads(body))
                    (root / 'job.json').write_text(json.dumps(rows, indent=2))
                return self.response(200, matches[0] if matches and 'application/vnd.pgrst.object+json' in self.headers.get('Accept', '') else matches)
        if path == '/storage/v1/bucket/clips' and token == SERVICE:
            return self.response(200, {'id': 'clips', 'name': 'clips', 'owner': '', 'public': False,
                                       'file_size_limit': 104857600, 'allowed_mime_types': ['video/mp4'],
                                       'created_at': '2026-11-11T00:00:00Z', 'updated_at': '2026-11-11T00:00:00Z'})
        sign_prefix = '/storage/v1/object/sign/clips/'
        object_prefix = '/storage/v1/object/clips/'
        if path.startswith(sign_prefix):
            object_path = path[len(sign_prefix):]
            if self.command == 'POST':
                if not owner or not object_path.startswith(owner + '/'):
                    return self.response(403, {'message': 'Owner only'})
                expires = int(time.time()) + json.loads(body)['expiresIn']
                return self.response(200, {'signedURL': f'/object/sign/clips/{object_path}?token={token_for(object_path, expires)}'})
            signed = params.get('token', [''])[0]
            try:
                expires = int(signed.split('.')[0])
            except ValueError:
                expires = 0
            if expires <= time.time() or not hmac.compare_digest(signed, token_for(object_path, expires)):
                return self.response(403, {'message': 'Expired or invalid signature'})
            return self.media_response(root / 'objects' / object_path,
                                       params.get('download', [None])[0])
        if path.startswith(object_prefix):
            object_path = path[len(object_prefix):]
            if token != SERVICE:
                return self.response(403, {'message': 'Private object'})
            destination = root / 'objects' / object_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if self.headers.get('Content-Type', '').startswith('multipart/form-data'):
                message = BytesParser(policy=email_policy).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                uploaded = next(part for part in message.iter_parts() if part.get_filename())
                if uploaded.get_content_type() != 'video/mp4':
                    return self.response(400, {'message': 'MP4 content type required'})
                body = uploaded.get_payload(decode=True)
            destination.write_bytes(body)
            return self.response(200, {'Key': 'clips/' + object_path, 'Id': str(uuid.uuid4())})
        if path == '/storage/v1/object/clips' and self.command == 'DELETE' and token == SERVICE:
            for object_path in json.loads(body)['prefixes']:
                (root / 'objects' / object_path).unlink(missing_ok=True)
            return self.response(200, [])
        return self.response(404, {'message': 'Unknown fixture endpoint'})


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base_url = f'http://127.0.0.1:{server.server_port}'
print(json.dumps({'url': base_url, 'artifacts': str(root), 'owner': OWNER}), flush=True)
client = create_client(base_url, SERVICE)
actual_public_target = media.public_target
try:
    while requests.get() != 'stop':
        def fixture_target(url):
            if url != base_url + '/owned.mp4':
                return actual_public_target(url)
            return urlsplit(url), '127.0.0.1', server.server_port
        with patch.dict(os.environ, {'LLM_API_KEY': '', 'AGENTROUTER_API_KEY': '', 'LLM_BASE_URL': '',
                                     'CLIPS_STORAGE_BUCKET': 'clips'}), \
             patch.object(media, 'public_target', side_effect=fixture_target), \
             contextlib.redirect_stdout(io.StringIO()):
            worker.run_queue(client, max_jobs=1, budget_seconds=1500)
finally:
    server.shutdown()
    server.server_close()
    thread.join()

"""Offline worker/database contract tests. No Supabase or LLM client is created."""
import contextlib
import io
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import worker


class FakeQueue:
    def __init__(self):
        self.row = {"id": "11111111-1111-4111-8111-111111111111",
                    "user_id": "22222222-2222-4222-8222-222222222222", "video_url": "https://example.com/video",
                    "status": "processing", "attempts": 1}
        self.filters = []
        self.payloads = []
        self.storage = FakeStorage()
        self.selecting = False

    def table(self, name):
        assert name == "clip_jobs"
        self.filters = []
        self.selecting = False
        return self

    def select(self, columns):
        self.selecting = True
        return self

    def update(self, payload):
        self.payloads.append(payload)
        return self

    def eq(self, name, value):
        self.filters.append((name, value))
        return self

    def execute(self):
        if all(self.row.get(name) == value for name, value in self.filters):
            if not self.selecting:
                self.row.update(self.payloads[-1])
            return SimpleNamespace(data=[dict(self.row)])
        return SimpleNamespace(data=[])


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.removed = []
        self.fail_upload = False

    def get_bucket(self, name):
        assert name == 'clips'
        return {'public': False}

    def from_(self, name):
        assert name == 'clips'
        return self

    def upload(self, path, handle, options):
        assert options['content-type'] == 'video/mp4'
        self.objects[path] = handle.read()
        if self.fail_upload:
            raise RuntimeError('upload failed')

    def remove(self, paths):
        self.removed.extend(paths)
        for path in paths:
            self.objects.pop(path, None)


class WorkerQueueTests(unittest.TestCase):
    def process(self, queue, analysis):
        claimed = dict(queue.row)
        def build(*_):
            clips = analysis()
            return [(path, {**clip, 'clip_number': index}) for index, clip in enumerate(clips, 1)], 'youtube_captions'
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(worker, "build_video", side_effect=build):
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'fixture')
            return worker.process_video_job(queue, claimed)

    def test_current_attempt_can_complete(self):
        queue = FakeQueue()
        clips = [{"title": "clip"}]
        completed = self.process(queue, lambda *_: clips)
        self.assertEqual(completed[0]['title'], 'clip')
        self.assertTrue(completed[0]['object_path'].startswith(queue.row['user_id'] + '/'))
        self.assertEqual(queue.row["status"], "completed")
        self.assertEqual(queue.row["clips"], completed)
        self.assertEqual(queue.row["transcript_source"], "youtube_captions")

    def test_late_result_cannot_overwrite_reclaimed_attempt(self):
        queue = FakeQueue()

        def reclaim(*_):
            queue.row.update(attempts=2, clips=[{"title": "newer result"}])
            return [{"title": "old result"}]

        self.assertIsNone(self.process(queue, reclaim))
        self.assertEqual(queue.row["attempts"], 2)
        self.assertEqual(queue.row["status"], "processing")
        self.assertEqual(queue.row["clips"], [{"title": "newer result"}])
        self.assertFalse(queue.storage.objects)

    def test_late_failure_cannot_fail_reclaimed_attempt(self):
        queue = FakeQueue()

        def reclaim_then_fail(*_):
            queue.row.update(attempts=2)
            raise RuntimeError("old attempt failed")

        self.assertIsNone(self.process(queue, reclaim_then_fail))
        self.assertEqual(queue.row["status"], "processing")
        self.assertNotIn("error_message", queue.row)

    def test_current_attempt_can_record_failure(self):
        queue = FakeQueue()

        def fail(*_):
            raise RuntimeError("analysis failed")

        self.assertIsNone(self.process(queue, fail))
        self.assertEqual(queue.row["status"], "failed")
        self.assertEqual(queue.row["error_message"], "analysis failed")

    def test_reaped_pending_job_cannot_be_completed_by_old_attempt(self):
        queue = FakeQueue()

        def reap(*_):
            queue.row["status"] = "pending"
            return [{"title": "stale result"}]

        self.assertIsNone(self.process(queue, reap))
        self.assertEqual(queue.row["status"], "pending")
        self.assertFalse(queue.storage.objects)

    def test_partial_upload_failure_cleans_objects(self):
        queue = FakeQueue()
        queue.storage.fail_upload = True
        self.assertIsNone(self.process(queue, lambda: [{'title': 'clip'}]))
        self.assertEqual(queue.row['status'], 'failed')
        self.assertEqual(queue.row['clips'], [])
        self.assertFalse(queue.storage.objects)
        self.assertEqual(len(queue.storage.removed), 1)

    def test_lost_completion_response_retains_committed_objects(self):
        queue = FakeQueue()
        execute = queue.execute
        def lost_response():
            result = execute()
            if not queue.selecting and result.data and queue.payloads[-1].get('status') == 'completed':
                raise TimeoutError('lost response')
            return result
        queue.execute = lost_response
        self.assertIsNone(self.process(queue, lambda: [{'title': 'clip'}]))
        self.assertEqual(queue.row['status'], 'completed')
        self.assertEqual(len(queue.storage.objects), 1)
        self.assertFalse(queue.storage.removed)

    def test_missing_owner_does_not_process(self):
        queue = FakeQueue()
        del queue.row['user_id']
        with patch.object(worker, 'build_video') as build:
            self.assertIsNone(worker.process_video_job(queue, dict(queue.row)))
            build.assert_not_called()

    def test_claim_accepts_setof_and_empty_queue(self):
        for data, expected in [([], None), ([{"id": "job-1"}], {"id": "job-1"}), (None, None)]:
            queue = SimpleNamespace(rpc=lambda name: SimpleNamespace(
                execute=lambda: SimpleNamespace(data=data)))
            self.assertEqual(worker.claim_job(queue), expected)

    def test_missing_queue_rpc_fails_the_run_instead_of_silent_success(self):
        queue = SimpleNamespace(rpc=lambda *_args: (_ for _ in ()).throw(RuntimeError('RPC absent')))
        with self.assertRaises(RuntimeError):
            worker.claim_job(queue)
        with self.assertRaises(RuntimeError):
            worker.reap_stale_jobs(queue)


if __name__ == "__main__":
    unittest.main()

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from googleapiclient.http import MediaIoBaseUpload
import workflow


def http_error(status):
    error = RuntimeError(str(status))
    error.resp = type('Response', (), {'status': status})()
    return error


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict('os.environ', {'OUTPUT_DIR': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.store = workflow.Store.__new__(workflow.Store)
        self.store.client = MagicMock()
        self.store.folder = 'folder'
        self.store.check_private = MagicMock()
        self.store.check_quota = MagicMock()
        self.store.find = MagicMock(return_value='existing-id')
        sleep = patch('workflow.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)
        self.store.verified = MagicMock(return_value=True)

    def media(self, resumable=True):
        return MediaIoBaseUpload(io.BytesIO(b'abc'), mimetype='text/plain', resumable=resumable)

    def test_existing_identical_file_skips_upload_and_quota(self):
        self.store.put('key', self.media(), 3)
        self.store.client.files().update.assert_not_called()
        self.store.check_quota.assert_not_called()

    def test_chunk_timeout_continues_same_session(self):
        self.store.verified.side_effect = [False, False, True]
        request = self.store.client.files().update.return_value
        request.next_chunk.side_effect = [TimeoutError(), (None, None), (None, {'id': 'existing-id'})]
        self.store.put('key', self.media(), 3)
        self.assertEqual(request.next_chunk.call_count, 3)
        self.assertEqual(self.store.client.files().update.call_count, 1)
        self.store.client.files().create.assert_not_called()

    def test_ambiguous_success_does_not_repeat_upload(self):
        self.store.verified.side_effect = [False, True]
        request = self.store.client.files().update.return_value
        request.next_chunk.side_effect = TimeoutError()
        self.store.put('key', self.media(), 3)
        self.assertEqual(request.next_chunk.call_count, 1)

    def test_ambiguous_create_reuses_id_after_process_retry(self):
        self.store.find.return_value = None
        self.store.client.files().generateIds().execute.return_value = {'ids': ['new-id']}
        self.store.client.files().generateIds.reset_mock()
        self.store.verified.return_value = False
        request = self.store.client.files().create.return_value
        request.next_chunk.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.store.put('key', self.media(), 3)
        self.assertEqual(request.next_chunk.call_count, 5)
        self.store.verified.side_effect = [False, True]
        request.next_chunk.side_effect = [(None, {'id': 'new-id'})]
        self.store.put('key', self.media(), 3)
        self.assertEqual(self.store.client.files().generateIds.call_count, 1)
        self.assertEqual({c.kwargs['body']['id'] for c in self.store.client.files().create.call_args_list}, {'new-id'})

    def test_expired_session_restarts_using_same_id(self):
        self.store.verified.side_effect = [False, False, True]
        self.store.client.files().update().next_chunk.side_effect = [http_error(410), (None, {'id': 'existing-id'})]
        self.store.client.files().update.reset_mock()
        self.store.put('key', self.media(), 3)
        self.assertEqual(self.store.client.files().update.call_count, 2)
        self.assertTrue(all(c.kwargs['fileId'] == 'existing-id' for c in self.store.client.files().update.call_args_list))

    def test_permission_failure_is_not_retried(self):
        self.store.verified.return_value = False
        request = self.store.client.files().update.return_value
        request.next_chunk.side_effect = http_error(403)
        with self.assertRaises(RuntimeError):
            self.store.put('key', self.media(), 3)
        self.assertEqual(request.next_chunk.call_count, 1)

    def test_mismatched_checksum_rejects_success(self):
        self.store.verified.return_value = False
        self.store.client.files().update().next_chunk.return_value = (None, {'id': 'existing-id'})
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            self.store.put('key', self.media(), 3)

    def test_conflict_does_not_overwrite(self):
        self.store.find.return_value = None
        self.store.verified.return_value = False
        self.store.client.files().generateIds().execute.return_value = {'ids': ['new-id']}
        self.store.client.files().create().next_chunk.side_effect = http_error(409)
        with self.assertRaisesRegex(RuntimeError, 'conflict'):
            self.store.put('key', self.media(), 3)
        self.store.client.files().update.assert_not_called()

    def test_json_checkpoint_retry_verified_and_idempotent(self):
        self.store.verified.side_effect = [False, False, True]
        request = self.store.client.files().update.return_value
        request.execute.side_effect = [TimeoutError(), {'id': 'existing-id'}]
        self.store.save('cleanup.json', {'complete': True})
        self.assertEqual(request.execute.call_count, 2)
        request.next_chunk.assert_not_called()
        media = self.store.client.files().update.call_args.kwargs['media_body']
        self.assertFalse(media.resumable())

    def test_real_verification_checks_identity_size_hash_and_privacy(self):
        del self.store.verified
        remote = {'parents': ['folder'], 'appProperties': {'workflow_key': hashlib.sha256(b'key').hexdigest()},
            'size': '3', 'md5Checksum': hashlib.md5(b'abc').hexdigest()}
        self.store.client.files().get().execute.return_value = remote
        self.assertTrue(self.store.verified('id', 'key', 3, hashlib.md5(b'abc').hexdigest()))
        self.assertFalse(self.store.verified('id', 'key', 4, hashlib.md5(b'abc').hexdigest()))
        remote['parents'] = ['other']
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            self.store.verified('id', 'key', 3, 'hash')

    def test_read_retries_bounded(self):
        call = MagicMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            workflow.drive_retry(call)
        self.assertEqual(call.call_count, 5)

    def test_creation_metadata_preserves_cleanup_scope(self):
        with patch.dict('os.environ', {'RUN_SCOPED_CLEANUP': 'true'}):
            self.assertEqual(self.store.metadata('runs/daily-123/video.mp4', 'id')['appProperties']['disposable'], 'v1')
            self.assertNotIn('disposable', self.store.metadata('runs/daily-123/state.json', 'id')['appProperties'])

    def test_download_failure_preserves_existing_destination(self):
        target = Path(self.temp.name)/'clip.mp4'
        target.write_bytes(b'original')
        self.store.client.files().get().execute.return_value = {'size': '3', 'md5Checksum': hashlib.md5(b'abc').hexdigest()}
        downloader = MagicMock()
        downloader.next_chunk.side_effect = TimeoutError()
        with patch('googleapiclient.http.MediaIoBaseDownload', return_value=downloader):
            with self.assertRaises(TimeoutError):
                self.store.download('id', target)
        self.assertEqual(target.read_bytes(), b'original')
        self.assertFalse(target.with_name(target.name + '.download-part').exists())

    def test_download_checksum_failure_never_promotes_partial_file(self):
        target = Path(self.temp.name)/'clip.mp4'
        self.store.client.files().get().execute.return_value = {'size': '3', 'md5Checksum': hashlib.md5(b'abc').hexdigest()}
        downloader = MagicMock()
        downloader.next_chunk.return_value = (None, True)
        with patch('googleapiclient.http.MediaIoBaseDownload', return_value=downloader):
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                self.store.download('id', target)
        self.assertFalse(target.exists())

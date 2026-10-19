import hashlib
import unittest
from unittest.mock import MagicMock
from daily_publish import cleanup, disposable, validate_remote


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        self.run = 'daily-123'
        self.state = {'phase': 'verified_private', 'video_id': 'video', 'verification': {
            'video_id': 'video', 'hd': True, 'processed': True, 'metadata': True, 'private': True}}
        self.entry = {'id': 'file', 'name': 'runs__daily-123__video.mp4', 'parents': ['folder'],
            'mimeType': 'video/mp4', 'appProperties': {'automation_run': self.run,
                'disposable': 'v1', 'workflow_key': hashlib.sha256(b'runs/daily-123/video.mp4').hexdigest()}}
        self.store.folder = 'folder'
        self.store.load.return_value = {'run': self.run, 'video_id': 'video', 'files': [self.entry], 'deleted': []}
        self.store.client.files().get().execute.return_value = self.entry

    def test_failed_and_incomplete_uploads_never_delete(self):
        for phase in ('prepared', 'upload_started', 'uploaded', 'complete'):
            with self.assertRaises(RuntimeError):
                cleanup(self.store, self.run, dict(self.state, phase=phase))
        self.store.client.files().delete.assert_not_called()

    def test_missing_hd_or_metadata_never_deletes(self):
        for flag in ('hd', 'metadata', 'processed', 'private'):
            state = dict(self.state, verification=dict(self.state['verification'], **{flag: False}))
            with self.assertRaises(RuntimeError):
                cleanup(self.store, self.run, state)
        self.store.client.files().delete.assert_not_called()

    def test_unrelated_moved_or_essential_file_rejected(self):
        for edit in ({'parents': ['other']}, {'name': 'runs__daily-123__state.json'},
                     {'appProperties': {'automation_run': 'daily-999'}}):
            self.store.client.files().get().execute.return_value = dict(self.entry, **edit)
            with self.assertRaises(ValueError):
                cleanup(self.store, self.run, self.state)
        self.store.client.files().delete.assert_not_called()

    def test_success_deletes_only_exact_manifest_id_and_is_idempotent(self):
        cleanup(self.store, self.run, self.state)
        self.store.client.files().delete.assert_called_once_with(fileId='file')
        cleanup(self.store, self.run, self.state)
        self.store.client.files().delete.assert_called_once_with(fileId='file')

    def test_already_deleted_id_is_checkpointed(self):
        error = RuntimeError('gone')
        error.resp = type('Response', (), {'status': 404})()
        self.store.client.files().get().execute.side_effect = error
        cleanup(self.store, self.run, self.state)
        self.assertTrue(self.store.load.return_value['complete'])
        self.store.client.files().delete.assert_not_called()

    def test_preserves_essential_data(self):
        for name in ('state.json', 'story.json', 'history.json', 'config.json', 'token.json', 'validation.json'):
            self.assertFalse(disposable(name))
        self.assertTrue(disposable('shot-123.qa.json'))

    def test_metadata_and_hd_gate(self):
        state = {'video_id': 'v', 'story': {'title': 'Title', 'tags': ['money'], 'description': 'Fiction'}}
        video = {'id': 'v', 'snippet': {'channelId': 'c', 'title': 'Title', 'tags': ['money'], 'description': 'Fiction'},
                 'status': {'uploadStatus': 'processed'}, 'processingDetails': {'processingStatus': 'succeeded'},
                 'contentDetails': {'definition': 'sd'}}
        self.assertFalse(validate_remote(video, state, 'c'))
        video['contentDetails']['definition'] = 'hd'
        self.assertTrue(validate_remote(video, state, 'c'))
        with self.assertRaises(RuntimeError):
            validate_remote(video, state, 'wrong-channel')

class RecoveryTests(unittest.TestCase):
    def test_verified_private_run_cannot_be_overwritten_or_uploaded_again(self):
        import long_video
        from unittest.mock import patch
        store = MagicMock()
        store.load.return_value = {'phase': 'verified_private', 'video_id': 'existing'}
        with patch('long_video.w.Store', return_value=store):
            with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
                long_video.save_preparation()
        store.upload.assert_not_called()
        store.save.assert_not_called()

    def test_cleanup_transient_failure_retries_same_file_without_upload(self):
        from unittest.mock import patch
        fixture = CleanupTests()
        fixture.setUp()
        execute = fixture.store.client.files().delete().execute
        fixture.store.client.files().delete.reset_mock()
        execute.side_effect = [RuntimeError('temporary'), {}]
        with patch('daily_publish.time.sleep'):
            cleanup(fixture.store, fixture.run, fixture.state)
        self.assertEqual(execute.call_count, 2)
        for call in fixture.store.client.files().delete.call_args_list:
            self.assertEqual(call.kwargs, {'fileId': 'file'})
        fixture.store.upload.assert_not_called()

    def test_cleanup_manifest_is_saved_before_delete_and_all_pages_are_read(self):
        fixture = CleanupTests()
        fixture.setUp()
        fixture.store.load.return_value = None
        fixture.store.client.files().list().execute.side_effect = [
            {'files': [fixture.entry], 'nextPageToken': 'page2'}, {'files': []}]
        saved = []
        fixture.store.save.side_effect = lambda key, value: saved.append(key)
        def delete():
            self.assertTrue(saved)
            return {}
        fixture.store.client.files().delete().execute.side_effect = delete
        cleanup(fixture.store, fixture.run, fixture.state)
        self.assertEqual(fixture.store.client.files().list().execute.call_count, 2)

    def test_exact_description_required(self):
        state = {'video_id': 'v', 'upload_description': 'Fiction\n\nMusic credit',
                 'story': {'title': 'T', 'description': 'Fiction', 'tags': []}}
        video = {'id': 'v', 'snippet': {'title': 'T', 'description': 'Fiction with unexpected text',
                 'tags': [], 'channelId': 'c'}, 'status': {'uploadStatus': 'processed'}}
        with self.assertRaisesRegex(RuntimeError, 'metadata'):
            validate_remote(video, state, 'c')

    def test_disposable_drive_files_tagged_without_tagging_credentials(self):
        import workflow
        from unittest.mock import patch
        store = workflow.Store.__new__(workflow.Store)
        store.client, store.folder = MagicMock(), 'folder'
        store.find, store.check_private, store.check_quota = MagicMock(return_value=None), MagicMock(), MagicMock()
        with patch.dict('os.environ', {'RUN_SCOPED_CLEANUP': 'true'}):
            body = store.metadata('runs/daily-123/video.mp4', 'id')
            self.assertEqual(body['appProperties']['automation_run'], 'daily-123')
            body = store.metadata('runs/daily-123/state.json', 'id')
            self.assertNotIn('disposable', body['appProperties'])

class PrivateVisibilityTests(unittest.TestCase):
    def run_private_verification(self, privacy):
        import daily_publish
        from unittest.mock import patch
        state = {'phase': 'complete', 'video_id': 'v', 'thumbnail_uploaded': True,
            'hash': 'hash', 'story': {'title': 'T', 'description': 'Fiction', 'tags': []}}
        video = {'id': 'v', 'snippet': {'channelId': 'c', 'title': 'T', 'description': 'Fiction', 'tags': []},
            'status': {'privacyStatus': privacy, 'uploadStatus': 'processed'},
            'processingDetails': {'processingStatus': 'succeeded'}, 'contentDetails': {'definition': 'hd'}}
        store, client = MagicMock(), MagicMock()
        store.load.side_effect = [state, []]
        client.videos().list().execute.return_value = {'items': [video]}
        env = {'SAVED_RUN_ID': 'daily-123', 'YOUTUBE_TOKEN_JSON': '{}', 'EXPECTED_CHANNEL_ID': 'c'}
        with patch.dict('os.environ', env), patch('workflow.Store', return_value=store), \
             patch.dict('sys.modules', {
                 'google.oauth2.credentials': MagicMock(),
                 'googleapiclient.discovery': MagicMock(build=MagicMock(return_value=client))}), \
             patch('daily_publish.cleanup') as clean:
            if privacy == 'private':
                daily_publish.main()
                self.assertEqual(state['phase'], 'verified_private')
                clean.assert_called_once_with(store, 'daily-123', state)
            else:
                with self.assertRaisesRegex(RuntimeError, 'remain private'):
                    daily_publish.main()
                clean.assert_not_called()
            client.videos().update.assert_not_called()
            client.videos().insert.assert_not_called()

    def test_verified_private_upload_never_changes_visibility_or_reuploads(self):
        self.run_private_verification('private')

    def test_public_or_unlisted_video_preserves_media(self):
        for privacy in ('public', 'unlisted'):
            self.run_private_verification(privacy)

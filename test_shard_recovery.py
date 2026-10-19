import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import long_video as lv
import shard_backup


class ShardRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {'shots': [{'index': 9}, {'index': 10}, {'index': 29}]}
        for name, value in [('plan.json', self.plan), ('story.json', {}), ('duration.json', {})]:
            (self.root/name).write_text(json.dumps(value))
        for target, kwargs in [('long_video.ROOT', {'new': self.root}),
                ('long_video.verify_preparation', {}), ('workflow.Store', {}),
                ('os.environ', {'values': {'SAVED_RUN_ID': 'daily-123',
                    'DRIVE_TOKEN_JSON': json.dumps({'refresh_token': 'test-secret'})}})]:
            obj = patch.dict(target, **kwargs) if target == 'os.environ' else patch(target, **kwargs)
            result = obj.start()
            self.addCleanup(obj.stop)
            if target == 'workflow.Store':
                self.store = result.return_value
        self.store.load.return_value = {'phase': 'prepared'}
        self.store.find.return_value = None

    def populate(self):
        for name in lv.shard_names(9, self.plan):
            (self.root/name).write_bytes(b'private work')

    def test_missing_bundle_stops_before_upload_or_generation(self):
        with patch('long_video.restore_preparation'), patch('long_video.generate_shard') as generate:
            with self.assertRaisesRegex(RuntimeError, 'refusing regeneration'):
                lv.recover_shard(9)
            self.store.upload.assert_not_called()
            self.store.download.assert_not_called()
            generate.assert_not_called()

    def test_partial_local_bundle_is_not_uploaded(self):
        (self.root/'shot-009.png').write_bytes(b'partial')
        with self.assertRaisesRegex(RuntimeError, 'Incomplete local'):
            lv.save_shard(9)
        self.store.upload.assert_not_called()
        self.store.save.assert_not_called()

    def test_verified_assets_checkpoint_only_after_all_uploads(self):
        self.populate()
        self.store.upload.return_value = {'id': 'file'}
        lv.save_shard(9)
        checkpoint = self.store.save.call_args.args[1]
        self.assertEqual(len(checkpoint['files']), 6)
        self.assertTrue(checkpoint['complete'])
        self.store.upload.side_effect = TimeoutError()
        self.store.save.reset_mock()
        with self.assertRaises(TimeoutError):
            lv.save_shard(9)
        self.store.save.assert_not_called()

    def test_existing_youtube_upload_stops_recovery(self):
        self.store.load.return_value = {'phase': 'upload_started'}
        with self.assertRaisesRegex(RuntimeError, 'no existing YouTube'):
            lv.recover_shard(9)
        self.store.upload.assert_not_called()

    def test_changed_preparation_rejects_local_backup(self):
        def changed(*args):
            (self.root/'plan.json').write_text('{}')
        with patch('long_video.restore_preparation', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                lv.recover_shard(9)
        self.store.upload.assert_not_called()

    def test_complete_remote_shard_recovers_without_generation(self):
        self.store.find.return_value = 'remote'
        self.store.download.side_effect = lambda fid, path: path.write_bytes(b'remote')
        self.store.upload.return_value = {'id': 'remote'}
        with patch('long_video.restore_preparation'), patch('long_video.generate_shard') as generate:
            lv.recover_shard(9)
        self.assertEqual(self.store.download.call_count, 6)
        self.assertTrue(self.store.save.call_args.args[1]['complete'])
        generate.assert_not_called()

    def test_encrypted_backup_round_trip_and_wrong_run_rejected(self):
        self.populate()
        archive = self.root/'backup.enc'
        shard_backup.transfer('pack', 9, archive)
        self.assertNotIn(b'private work', archive.read_bytes())
        (self.root/'shot-009.png').unlink()
        shard_backup.transfer('unpack', 9, archive)
        self.assertEqual((self.root/'shot-009.png').read_bytes(), b'private work')
        with patch.dict('os.environ', {'SAVED_RUN_ID': 'daily-456'}):
            with self.assertRaises(Exception):
                shard_backup.transfer('unpack', 9, archive)

    def test_invalid_shard_rejected(self):
        for shard in (-1, 20):
            with self.assertRaises(ValueError):
                lv.shard_names(shard, self.plan)

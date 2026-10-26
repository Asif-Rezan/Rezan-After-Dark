import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import long_video as lv
from resilience import produce_file, retry_local


class ResilienceTests(unittest.TestCase):
    def setup_plan(self, root):
        (root/'story.json').write_text(json.dumps({'scenes': [{'narration': 'A keeper opens the lighthouse door.'}]}))
        plan = {'shots': [{'index': i, 'chapter': 0,
            'narration': 'A keeper opens the lighthouse door.'} for i in range(2)]}
        (root/'plan.json').write_text(json.dumps(plan))
        return plan

    def response(self, values):
        return {'choices': [{'message': {'content': json.dumps(values)}}]}

    def test_invalid_length_repairs_only_failed_shot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_plan(root)
            good = 'The keeper opens the lighthouse door beside a broken lantern.'
            ask = Mock(side_effect=[self.response({'shot_000': good, 'shot_001': 'word '*50}),
                self.response({'shot_000': 'changed valid scene with five words', 'shot_001': good})])
            plan = lv.storyboard(root, ask=ask, pause=lambda _: None)
            self.assertEqual(ask.call_count, 2)
            self.assertEqual(plan['shots'][0]['visual_action'], good)
            self.assertNotIn('storyboard_advisory', plan['shots'][1])

    def test_malformed_missing_truncated_and_backend_failure_fall_back(self):
        failures = [self.response({}), {'choices': []},
                    {'choices': [{'finish_reason': 'length'}]},
                    {'choices': [{'message': {'content': 'invalid JSON'}}]},
                    self.response([]), RuntimeError('backend unavailable')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.setup_plan(root)
                ask = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
                plan = lv.storyboard(root, ask=ask, pause=lambda _: None)
                self.assertEqual(ask.call_count, 3)
                self.assertTrue(plan['storyboard_verified'])
                for shot in plan['shots']:
                    self.assertTrue(lv.valid_visual(shot['prompt']))
                    self.assertIn('keeper opens the lighthouse door', shot['prompt'])
                    self.assertIn('storyboard_advisory', shot)

    def test_restart_preserves_partial_storyboard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.setup_plan(root)
            plan['shots'][0]['visual_action'] = 'The keeper opens the lighthouse door beside a lantern.'
            lv.write_json(root/'plan.json', plan)
            ask = Mock(return_value=self.response({'shot_001': 'The lantern illuminates the keeper beside the lighthouse door.'}))
            result = lv.storyboard(root, ask=ask, pause=lambda _: None)
            schema = ask.call_args.args[1]['response_format']['json_schema']['schema']
            self.assertEqual(schema['required'], ['shot_001'])
            self.assertIn('pattern', schema['properties']['shot_001'])
            self.assertEqual(result['shots'][0]['visual_action'], plan['shots'][0]['visual_action'])

    def test_missing_source_still_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.setup_plan(root)
            plan['shots'][0]['narration'] = ''
            lv.write_json(root/'plan.json', plan)
            with self.assertRaisesRegex(ValueError, 'narrated words'):
                lv.storyboard(root, ask=Mock(return_value=self.response({})), pause=lambda _: None)

    def test_transient_retry_bounded_and_permanent_errors_propagate(self):
        operation = Mock(side_effect=[TimeoutError(), 'ok'])
        self.assertEqual(retry_local(operation, 'test', pause=lambda _: None), 'ok')
        operation = Mock(side_effect=ConnectionError())
        with self.assertRaises(ConnectionError):
            retry_local(operation, 'test', pause=lambda _: None)
        self.assertEqual(operation.call_count, 3)
        operation = Mock(side_effect=ValueError('bad configuration'))
        with self.assertRaises(ValueError):
            retry_local(operation, 'test', pause=lambda _: None)
        self.assertEqual(operation.call_count, 1)

    def test_failed_output_never_replaces_good_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'shot.mp4'
            target.write_bytes(b'good output')
            def producer(candidate):
                candidate.write_bytes(b'partial output')
                raise RuntimeError('render failed')
            with self.assertRaises(RuntimeError):
                produce_file(target, producer, 'test')
            self.assertEqual(target.read_bytes(), b'good output')
            self.assertFalse(list(Path(temp).glob('*.pending.*')))

    def test_scene_scoring_outage_keeps_safety_approved_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shot = {'index': 0, 'seed': 1700, 'duration': 10, 'prompt': 'lighthouse'}
            lv.write_json(root/'plan.json', {'storyboard_verified': True, 'scene_composition_version': 3, 'shots': [shot]})
            with patch('workflow.make_image', side_effect=lambda prompt, path: path.write_bytes(b'image')), \
                 patch('scene_check.check_scene', side_effect=OSError('model unavailable')), \
                 patch('long_video.picture_clip', side_effect=lambda image, duration, path: path.write_bytes(b'clip')):
                lv.generate_shard(0, root, count=1)
            report = json.loads((root/'shot-000.qa.json').read_text())
            self.assertTrue(report['advisory'])
            self.assertEqual(lv.validate_scene_reports({'shots': [shot]}, [report]), [0])
            self.assertTrue((root/'shot-000.mp4').exists())

    def test_failed_shot_does_not_prevent_remaining_shots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shots = [{'index': i, 'prompt': 'lighthouse'} for i in range(3)]
            lv.write_json(root/'plan.json', {'storyboard_verified': True, 'scene_composition_version': 3, 'shots': shots})
            with patch('long_video.generate_shot', side_effect=[RuntimeError('blocked'), None, None]) as generate:
                with self.assertRaisesRegex(RuntimeError, 'unfinished shots'):
                    lv.generate_shard(0, root, count=1)
            self.assertEqual(generate.call_count, 3)

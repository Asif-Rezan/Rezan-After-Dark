import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
import workflow as w


class Checks(unittest.TestCase):

    def test_safety_blocked_image_uses_non_graphic_retry_without_disabling_checker(self):
        import sys
        import types
        import image_cpu
        class FakeGenerator:
            def __init__(self, device):
                self.device = device
            def manual_seed(self, seed):
                return self
        fake_torch = types.SimpleNamespace(
            set_num_threads=lambda count: None, float32='float32', Generator=FakeGenerator)
        class FakePipeline:
            def __init__(self):
                self.calls = []
                self.scheduler = types.SimpleNamespace(config={})
            def to(self, device):
                return self
            def enable_attention_slicing(self):
                pass
            def enable_vae_slicing(self):
                pass
            def __call__(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return types.SimpleNamespace(nsfw_content_detected=[True], images=[Image.new('RGB', (256, 256), 'black')])
                return types.SimpleNamespace(nsfw_content_detected=[False], images=[Image.new('RGB', (256, 256), 'blue')])
        fake = FakePipeline()
        previous = image_cpu._pipeline
        try:
            image_cpu._pipeline = fake
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp)/'safe.png'
                with patch.dict(sys.modules, {'torch': fake_torch, 'diffusers': types.SimpleNamespace(DiffusionPipeline=object, DPMSolverMultistepScheduler=object)}), patch.dict('os.environ', {'CPU_IMAGE_WIDTH': '256', 'CPU_IMAGE_HEIGHT': '256', 'CPU_IMAGE_STEPS': '2'}):
                    image_cpu.generate('bloody confrontation in an office', path)
                self.assertEqual(len(fake.calls), 2)
                self.assertIn('fully clothed', fake.calls[1]['prompt'])
                self.assertNotIn('bloody', fake.calls[1]['prompt'])
                self.assertTrue(path.exists())
        finally:
            image_cpu._pipeline = previous

    def test_five_safety_attempts_then_rewritten_prompt_and_bounded_failure(self):
        import sys
        import types
        import image_cpu
        class FakeGenerator:
            def __init__(self, device):
                pass
            def manual_seed(self, seed):
                self.seed = seed
                return self
        class Pipeline:
            def __init__(self, success_at):
                self.calls = []
                self.success_at = success_at
            def __call__(self, **kwargs):
                self.calls.append(kwargs)
                blocked = len(self.calls) < self.success_at
                return types.SimpleNamespace(nsfw_content_detected=[blocked],
                    images=[Image.new('RGB', (256, 256), 'black' if blocked else 'blue')])
        fake_torch = types.SimpleNamespace(set_num_threads=lambda n: None,
            float32='float32', Generator=FakeGenerator)
        for success_at in (1, 5, 6, 10, 11):
            with self.subTest(success_at=success_at), tempfile.TemporaryDirectory() as temp:
                target = Path(temp)/'image.png'
                pipeline = Pipeline(success_at)
                with patch.object(image_cpu, '_pipeline', pipeline), patch.dict(sys.modules,
                    {'torch': fake_torch, 'diffusers': types.SimpleNamespace(
                        DiffusionPipeline=object, DPMSolverMultistepScheduler=object)}), patch.dict(
                    'os.environ', {'CPU_IMAGE_WIDTH': '256', 'CPU_IMAGE_HEIGHT': '256', 'CPU_IMAGE_STEPS': '2'}):
                    if success_at > 10:
                        with self.assertRaisesRegex(image_cpu.ImageSafetyBlocked, 'five initial and five rewritten'):
                            image_cpu.generate('Workers studying limestone blocks at Giza', target)
                        self.assertFalse(target.exists())
                    else:
                        image_cpu.generate('Workers studying limestone blocks at Giza', target)
                        with Image.open(target) as image:
                            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))
                calls = pipeline.calls
                self.assertEqual(len(calls), min(success_at, 10))
                self.assertEqual(len({c['generator'].seed for c in calls}), len(calls))
                self.assertEqual(len({c['prompt'] for c in calls[:5]}), 1)
                if len(calls) > 5:
                    self.assertNotEqual(calls[4]['prompt'], calls[5]['prompt'])
                    self.assertEqual(len({c['prompt'] for c in calls[5:]}), 1)
                    self.assertIn('limestone blocks at Giza', calls[5]['prompt'])
                    self.assertIn('no people depicted', calls[5]['prompt'])

    def test_visual_quality_advisory_is_recorded_without_hiding_failures(self):
        from long_video import validate_scene_reports
        plan = {'shots': [{'index': 23}, {'index': 24}]}
        reports = [{'passed': False, 'advisory': True, 'warning': 'CLIP inconclusive'},
                   {'passed': True, 'required': False}]
        self.assertEqual(validate_scene_reports(plan, reports), [23])
        with self.assertRaisesRegex(RuntimeError, 'Missing visual review'):
            validate_scene_reports(plan, reports[:1])
        with self.assertRaisesRegex(RuntimeError, 'invalid visual'):
            validate_scene_reports(plan, [{'passed': False}, reports[1]])
        with self.assertRaisesRegex(RuntimeError, 'invalid visual'):
            validate_scene_reports(plan, [{'passed': False, 'advisory': True}, reports[1]])

    def test_failed_clip_score_keeps_last_safety_approved_render(self):
        from long_video import generate_shard
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shot = {'index': 0, 'seed': 123, 'prompt': 'Woman studying a letter at a desk',
                    'focus': 'group', 'negative_prompt': '', 'duration': 7}
            plan = {'storyboard_verified': True, 'scene_composition_version': 3, 'shots': [shot]}
            (root/'plan.json').write_text(json.dumps(plan))
            calls = []
            def fake_render(prompt, path, reference_image=None):
                calls.append(prompt)
                Image.new('RGB', (256, 256), 'blue').save(path)
            with (patch('workflow.make_image', side_effect=fake_render),
                  patch('scene_check.check_scene', return_value={'passed': False, 'required': True}),
                  patch('long_video.picture_clip', side_effect=lambda image, seconds, target: target.write_bytes(b'clip'))):
                generate_shard(0, root)
            self.assertEqual(len(calls), 3)
            self.assertTrue((root/'shot-000.png').exists())
            self.assertTrue((root/'shot-000.mp4').exists())
            qa = json.loads((root/'shot-000.qa.json').read_text())
            self.assertFalse(qa['passed'])
            self.assertTrue(qa['advisory'])

    def test_historical_shots_do_not_use_reference(self):
        from long_video import generate_shard
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shots = [{'index': 0, 'seed': 123, 'prompt': 'Ancient Egyptian workers shape limestone blocks.',
                      'focus': 'group', 'negative_prompt': '', 'duration': 7}]
            (root/'plan.json').write_text(json.dumps({
                'storyboard_verified': True, 'scene_composition_version': 3, 'shots': shots}))
            prompts = []
            def fake_render(prompt, path):
                prompts.append(prompt)
                Image.new('RGB', (1024, 576), 'blue').save(path)
            with (patch('workflow.make_image', side_effect=fake_render),
                  patch('scene_check.check_scene', return_value={'passed': True}),
                  patch('long_video.picture_clip', side_effect=lambda image, seconds, target: target.write_bytes(b'clip'))):
                generate_shard(0, root, count=1)
            self.assertEqual(len(prompts), 1)
            self.assertIn('limestone', prompts[0])

    def test_global_ten_second_timeline_and_spoken_events(self):
        from long_video import shot_plan
        story = {'scenes': [{'narration': 'She lifts a red suitcase.'},
                            {'narration': 'He repairs a broken clock.'}]}
        words = [[dict(word='suitcase', start=0, end=7.2)],
                 [dict(word='clock', start=0, end=8.3)]]
        plan = shot_plan(story, [7.2, 8.3], words)
        self.assertEqual([s['start'] for s in plan['shots']], [0, 10])
        self.assertEqual([s['duration'] for s in plan['shots']], [10, 5.5])
        self.assertEqual(plan['shots'][0]['narration'], 'suitcase clock')
        self.assertEqual(plan['shots'][1]['narration'], 'clock')
        self.assertEqual(plan['shots'][1]['chapter'], 1)
        self.assertNotIn('prompt', plan['shots'][0])
        with self.assertRaises(ValueError):
            shot_plan(story, [7.2, 8.3], [[], []])

    def test_storyboard_uses_spoken_window_and_refuses_missing_shots(self):
        from long_video import shot_plan, storyboard, generate_shard
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            story = {'character_description': 'Maya, black hair, blue coat',
                     'scenes': [{'narration': 'Maya opens the red suitcase beside the station bench.'}]}
            plan = shot_plan(story, [12], [[dict(word='red suitcase', start=0, end=6),
                                         dict(word='station bench', start=7, end=12)]])
            (root/'story.json').write_text(json.dumps(story))
            (root/'plan.json').write_text(json.dumps(plan))
            with self.assertRaisesRegex(RuntimeError, 'storyboard'):
                generate_shard(0, root)
            def ask(endpoint, payload):
                source = payload['messages'][0]['content']
                self.assertIn('red suitcase', source)
                self.assertIn('station bench', source)
                values = {'shot_000': 'Maya opens a red suitcase beside the wooden station bench.',
                          'shot_001': 'Maya sits beside the station bench holding her open red suitcase.'}
                return {'choices': [{'message': {'content': json.dumps(values)}}]}
            aligned = storyboard(root, ask=ask, pause=lambda _: None)
            self.assertTrue(aligned['storyboard_verified'])
            self.assertIn('red suitcase', aligned['shots'][0]['prompt'])
            self.assertNotIn('wizard', aligned['shots'][0]['prompt'])
            def incomplete(endpoint, payload):
                return {'choices': [{'message': {'content': '{}'}}]}
            aligned.pop('storyboard_verified')
            (root/'plan.json').write_text(json.dumps(aligned))
            with self.assertRaisesRegex(ValueError, 'every narration window'):
                storyboard(root, ask=incomplete, pause=lambda _: None)

    def test_scene_refinement_uses_historical_action_without_reference(self):
        from long_video import refine_visual_plan
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            story = {'category': 'Ancient Egypt', 'scenes': [{'narration': 'A sealed tomb was found.'}]}
            plan = {'shots': [{'index': 0, 'visual_action': 'A sealed tomb entrance in limestone'},
                              {'index': 1, 'visual_action': 'Workers examine the ancient stone blocks'}]}
            (root/'story.json').write_text(json.dumps(story))
            (root/'plan.json').write_text(json.dumps(plan))
            result = refine_visual_plan(root)
            tomb, workers = result['shots']
            self.assertEqual(tomb['focus'], 'object')
            self.assertEqual(workers['focus'], 'group')
            self.assertIn('sealed tomb', tomb['prompt'])
            self.assertIn('horror illustration', workers['prompt'])
            self.assertNotIn('Recurring wizard', workers['prompt'])

    def test_actual_ten_second_image_change(self):
        from long_video import picture_clip
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for i, color in enumerate(((230, 20, 20), (20, 20, 230))):
                Image.new('RGB', (640, 360), color).save(root/f'{i}.png')
                picture_clip(root/f'{i}.png', 10, root/f'{i}.mp4')
                self.assertAlmostEqual(w.duration(root/f'{i}.mp4'), 10, places=2)
            (root/'list.txt').write_text("file '0.mp4'\nfile '1.mp4'\n")
            w.shell('ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(root/'list.txt'),
                '-c', 'copy', str(root/'joined.mp4'))
            def pixel(at):
                return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at),
                    '-i', str(root/'joined.mp4'), '-frames:v', '1', '-s', '1x1',
                    '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
            before, after = pixel(9.8), pixel(10.2)
            self.assertGreater(before[0], before[2])
            self.assertGreater(after[2], after[0])

    def test_music_is_present_and_balanced(self):
        from long_video import mix_audio
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            w.shell('ffmpeg', '-y', '-f', 'lavfi', '-i',
                'sine=frequency=220:duration=5', str(root/'voice.wav'))
            w.shell('ffmpeg', '-y', '-f', 'lavfi', '-i',
                'sine=frequency=660:duration=2', str(root/'music.wav'))
            result = mix_audio(root/'voice.wav', root/'music.wav', root/'mix.wav', 5)
            self.assertTrue(result['music_audibility_passed'])
            self.assertTrue(5 < result['music_gap_db'] < 22)
            self.assertAlmostEqual(w.duration(root/'mix.wav'), 5, places=1)
            w.shell('ffmpeg', '-y', '-f', 'lavfi', '-i',
                'anullsrc=r=24000:cl=stereo', '-t', '2', str(root/'silent.wav'))
            with self.assertRaisesRegex(RuntimeError, 'inaudible'):
                mix_audio(root/'voice.wav', root/'silent.wav', root/'bad.wav', 5)

    def test_closing_cta_is_required(self):
        from long_video import validate_engagement
        story = {'character_description': 'a young carpenter in a blue coat',
            'scenes': [{'narration': 'Like this story, comment with your next step, and subscribe.'}]}
        validate_engagement(story)
        story['scenes'][0]['narration'] = 'Thank you for watching.'
        with self.assertRaisesRegex(ValueError, 'like, comment, and subscribe'):
            validate_engagement(story)

    def test_time_rounding(self):
        self.assertEqual(w.timestamp(59.9996), '00:01:00,000')

    def test_speech_input_limit(self):
        text = 'A small daily step can rebuild your confidence. ' * 30
        chunks = w.speech_chunks(text)
        self.assertTrue(all(0 < len(c) <= 200 for c in chunks))
        self.assertEqual(' '.join(chunks), ' '.join(text.split()))
        with self.assertRaises(ValueError):
            w.speech_chunks('x' * 201)

    def test_daily_gate(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                w.run(daily=True)

    def test_missing_credentials(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'DRIVE_TOKEN_JSON'):
                w.preflight()

    def test_free_drive_quota_guard(self):
        store = w.Store.__new__(w.Store)
        store.client = MagicMock()
        query = store.client.about.return_value.get.return_value.execute
        query.return_value = {'storageQuota': {'limit': str(15*1024**3), 'usage': '0'}}
        store.check_quota(1024)
        query.return_value['storageQuota']['usage'] = str(15*1024**3 - 50*1024**2)
        with self.assertRaisesRegex(RuntimeError, 'Free storage'):
            store.check_quota(1024)
        query.return_value = {'storageQuota': {'usage': '0'}}
        with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
            store.check_quota(1024)

    def test_shared_drive_file_rejected(self):
        store = w.Store.__new__(w.Store)
        store.client = MagicMock()
        query = store.client.permissions.return_value.list.return_value.execute
        query.return_value = {'permissions': [{'type': 'user', 'role': 'owner'}]}
        store.check_private('folder')
        query.return_value['permissions'].append({'type': 'anyone', 'role': 'reader'})
        with self.assertRaisesRegex(RuntimeError, 'private'):
            store.check_private('folder')

    def test_story_validation(self):
        story = dict(title='The Last Step', description='An original fictional story.', tags=['motivation'],
                     thumbnail_text='ONE MORE STEP', thumbnail_prompt='A determined runner',
                     scenes=[dict(narration=' '.join(['word']*450), image_prompt='cinematic road') for _ in range(4)])
        self.assertEqual(w.validate_story(story), story)
        story['title'] = 'x'*101
        with self.assertRaises(ValueError):
            w.validate_story(story)

    def test_aligned_captions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'captions.srt'
            w.aligned_subtitles([[dict(word='Keep', start=0.2, end=0.5), dict(word='going', start=0.6, end=1.2)]], [2], path)
            self.assertIn('00:00:00,200 --> 00:00:01,200', path.read_text())
            with self.assertRaises(ValueError):
                w.aligned_subtitles([[dict(word='Bad',start=1,end=4)]],[2],path)

    def test_real_hd_render(self):
        # Synthetic test fixtures only; this is not the requested AI story test.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new('RGB', (1024,576), (25,40,60)).save(root/'image.png')
            w.shell('ffmpeg','-y','-f','lavfi','-i','sine=frequency=440:duration=2',str(root/'audio.wav'))
            length = w.render_scene(root/'image.png',root/'audio.wav',root/'clip.mp4')
            self.assertAlmostEqual(length, 2, places=1)
            metadata = w.check_video(root/'clip.mp4', min_duration=1, max_duration=900)
            self.assertEqual(metadata['streams'][0]['width'],1920)
            with self.assertRaises(AssertionError):
                w.check_video(root/'clip.mp4')


if __name__ == '__main__':
    unittest.main()

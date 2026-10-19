import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duration_control as dc
import long_video as lv
import workflow as w


CTA = (" Which part of this story frightened you most? "
       "Leave a comment with your answer, like this video, and subscribe for more stories.")


def story_fixture():
    scenes = [{'summary': f'Event {i}', 'image_prompt': 'A tense room',
               'narration': ' '.join([f'chapter{i}'] * 84)} for i in range(20)]
    scenes[-1]['narration'] += CTA
    return {'title': 'The Locked Letter', 'description': 'A fictional story.', 'tags': ['fiction'],
            'thumbnail_text': 'THE LETTER', 'thumbnail_prompt': 'A locked letter',
            'character_description': 'Maya in a blue coat', 'scenes': scenes}


class DurationDecisionTests(unittest.TestCase):
    def test_requested_duration_boundaries(self):
        expected = {880: False, 900: True, 1020: True, 1140: True, 1200: True,
                    1204.3: False, 1300: False, 1500: False}
        for seconds, accepted in expected.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(dc.valid(seconds), accepted)
        self.assertIsNone(dc.tempo_factor(1300))
        self.assertAlmostEqual(dc.tempo_factor(1204.3), 1204.3/dc.TEMPO_TARGET)

    def test_valid_audio_does_not_regenerate_or_rewrite(self):
        for total in (900, 1020, 1140, 1200):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as temp:
                story = story_fixture()
                with patch('long_video.ensure_chapter_audio', return_value=total/20) as audio, \
                     patch('workflow.revise_story_duration') as revise, \
                     patch('long_video.adjust_tempo') as tempo:
                    result, lengths = lv.correct_duration(story, Path(temp))
                self.assertIs(result, story)
                self.assertAlmostEqual(sum(lengths), total)
                self.assertEqual(audio.call_count, 20)
                revise.assert_not_called()
                tempo.assert_not_called()

    def test_1204_point_3_uses_minimal_tempo(self):
        story = story_fixture()
        with tempfile.TemporaryDirectory() as temp, \
             patch('long_video.ensure_chapter_audio', return_value=1204.3/20), \
             patch('long_video.adjust_tempo', return_value=[1199.8/20]*20) as tempo, \
             patch('workflow.revise_story_duration') as revise:
            _, lengths = lv.correct_duration(story, Path(temp))
        self.assertAlmostEqual(sum(lengths), 1199.8)
        self.assertAlmostEqual(tempo.call_args.args[-1], 1204.3/dc.TEMPO_TARGET)
        revise.assert_not_called()

    def test_shortening_and_expansion_regenerate_only_selected_chapters(self):
        for total, direction in ((880, 'expand'), (1300, 'shorten'), (1500, 'shorten')):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as temp:
                original = story_fixture()
                current_ratio = [1.0]
                def audio(root, index, text):
                    return total/20 * (current_ratio[0] if text.startswith('revised ') else 1)
                def revise(story, ratio, requested_direction, selected):
                    self.assertEqual(requested_direction, direction)
                    self.assertLessEqual(len(selected), 20)
                    current_ratio[0] = ratio
                    result = copy.deepcopy(story)
                    for i in selected:
                        result['scenes'][i]['narration'] = 'revised ' + result['scenes'][i]['narration']
                    return result
                with patch('long_video.ensure_chapter_audio', side_effect=audio) as make_audio, \
                     patch('workflow.revise_story_duration', side_effect=revise) as rewrite:
                    fixed, lengths = lv.correct_duration(original, Path(temp))
                self.assertTrue(dc.valid(sum(lengths)))
                selected = rewrite.call_args.args[-1]
                self.assertEqual(make_audio.call_count, 20 + len(selected))
                self.assertTrue(fixed['scenes'][-1]['narration'].endswith(CTA))

    def test_three_attempt_limit_and_groq_failure_preserve_original(self):
        original = story_fixture()
        with tempfile.TemporaryDirectory() as temp, \
             patch('long_video.ensure_chapter_audio', return_value=65), \
             patch('workflow.revise_story_duration', return_value=original) as revise:
            with self.assertRaisesRegex(RuntimeError, 'after 3 correction attempts'):
                lv.correct_duration(original, Path(temp))
            self.assertEqual(revise.call_count, 3)
        with tempfile.TemporaryDirectory() as temp, \
             patch('long_video.ensure_chapter_audio', return_value=65), \
             patch('workflow.revise_story_duration', side_effect=RuntimeError('Groq unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Groq unavailable'):
                lv.correct_duration(original, Path(temp))
            self.assertTrue(original['scenes'][-1]['narration'].endswith(CTA))


class CacheAndAlignmentTests(unittest.TestCase):
    def test_prepare_rebuilds_storyboard_after_one_chapter_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            story = story_fixture()
            synthesized = []
            def make(text, path):
                synthesized.append(text)
                path.write_bytes(text.encode())
            def seconds(path):
                return 1000 if path.name == 'narration.wav' else 50
            def shell(*args):
                Path(args[-1]).write_bytes(b'joined narration')
            with patch('long_video.w.make_narration', side_effect=make), \
                 patch('long_video.w.duration', side_effect=seconds), \
                 patch('long_video.w.transcribe', return_value=[{'word': 'Maya', 'start': 1, 'end': 2}]) as transcribe, \
                 patch('long_video.w.shell', side_effect=shell):
                first = lv.prepare(story, root)
                self.assertEqual(len(synthesized), 20)
                self.assertEqual(transcribe.call_count, 20)
                old_hash = first['source_hash']
                second = lv.prepare(story, root)
                self.assertEqual(len(synthesized), 20)
                self.assertEqual(transcribe.call_count, 20)
                self.assertEqual(second['source_hash'], old_hash)
                changed = copy.deepcopy(story)
                changed['scenes'][5]['narration'] += ' Maya discovers the hidden note.'
                third = lv.prepare(changed, root)
                self.assertEqual(len(synthesized), 21)
                self.assertEqual(transcribe.call_count, 21)
                self.assertNotEqual(third['source_hash'], old_hash)
                self.assertNotIn('storyboard_verified', third)
                self.assertEqual(third['shots'][0]['start'], 0)
                self.assertEqual(third['shots'][1]['start'], 10)

    def test_audio_and_transcript_hashes_invalidate_stale_chapter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def make(text, path):
                path.write_bytes(text.encode())
            with patch('long_video.w.make_narration', side_effect=make) as synth, \
                 patch('long_video.w.duration', return_value=40), \
                 patch('long_video.w.transcribe', return_value=[{'word': 'Maya', 'start': 0, 'end': 1}]) as transcribe:
                lv.ensure_chapter_audio(root, 0, 'Maya opens a letter')
                lv.ensure_chapter_audio(root, 0, 'Maya opens a letter')
                self.assertEqual(synth.call_count, 1)
                audio = root/'narration-00.wav'
                lv.ensure_words(root, 0, audio, 'Maya opens a letter')
                lv.ensure_words(root, 0, audio, 'Maya opens a letter')
                self.assertEqual(transcribe.call_count, 1)
                lv.ensure_chapter_audio(root, 0, 'Maya burns the letter')
                lv.ensure_words(root, 0, audio, 'Maya burns the letter')
                self.assertEqual(synth.call_count, 2)
                self.assertEqual(transcribe.call_count, 2)

    def test_tempo_adjustment_changes_measured_audio_and_refreshes_word_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            story = {'scenes': [{'narration': 'Maya opens a letter'}]}
            audio = root/'narration-00.wav'
            w.shell('ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', str(audio))
            old = w.duration(audio)
            (root/'words-00.json').write_text('[{"word":"stale","start":0,"end":1}]')
            lengths = lv.adjust_tempo(root, story, [old], 1.02)
            self.assertLess(lengths[0], old)
            self.assertFalse((root/'words-00.json').exists())
            with patch('long_video.w.transcribe', return_value=[{'word': 'Maya', 'start': 0, 'end': .8}]) as transcribe:
                words = lv.ensure_words(root, 0, audio, story['scenes'][0]['narration'])
            transcribe.assert_called_once()
            self.assertEqual(words[0]['word'], 'Maya')
            plan = lv.shot_plan(story, lengths, [words])
            self.assertEqual(plan['image_interval_seconds'], 10)
            self.assertEqual(plan['shots'][0]['narration'], 'Maya')

    def test_rebuild_and_checkpoint_reject_stale_story(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            story = story_fixture()
            (root/'story.json').write_text(json.dumps(story))
            (root/'plan.json').write_text(json.dumps({'source_hash': 'current', 'storyboard_verified': True}))
            (root/'duration.json').write_text(json.dumps({'duration_seconds': 1080,
                'story_sha256': lv.file_hash(root/'story.json'), 'plan_source_hash': 'current'}))
            lv.verify_preparation(root, images_only=True)
            story['scenes'][0]['narration'] += ' changed'
            (root/'story.json').write_text(json.dumps(story))
            with self.assertRaisesRegex(RuntimeError, 'checkpoint do not match'):
                lv.verify_preparation(root, images_only=True)


class StoryRevisionTests(unittest.TestCase):
    def test_selected_chapter_revision_preserves_cta_and_neighbor_context(self):
        story = story_fixture()
        prompts = []
        def response(endpoint, payload):
            prompts.append(payload['messages'][0]['content'])
            keys = payload['response_format']['json_schema']['schema']['required']
            values = {key: ' '.join(['Maya']*65) for key in keys}
            return {'choices': [{'message': {'content': json.dumps(values)}}]}
        with patch('workflow.request_groq', side_effect=response), patch('workflow.time.sleep'):
            revised = w.revise_story_duration(story, .78, 'shorten', [3, 4])
        self.assertTrue(revised['scenes'][-1]['narration'].endswith(CTA))
        self.assertEqual(revised['scenes'][2], story['scenes'][2])
        self.assertIn('chapter2', prompts[0])
        self.assertIn('chapter5', prompts[0])
        self.assertEqual(len(revised['scenes']), 20)

    def test_failed_groq_revision_does_not_mutate_story(self):
        story = story_fixture()
        before = json.dumps(story)
        with patch('workflow.request_groq', side_effect=RuntimeError('rate limited')):
            with self.assertRaisesRegex(RuntimeError, 'rate limited'):
                w.revise_story_duration(story, .8, 'shorten', [3])
        self.assertEqual(json.dumps(story), before)


if __name__ == '__main__':
    unittest.main()

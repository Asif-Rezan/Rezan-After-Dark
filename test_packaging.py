import tempfile
import copy
import unittest
from pathlib import Path
from PIL import Image
from video_packaging import artwork_candidate, choose_package, description_parts, request_package
from thumbnail_design import hook_layout, render_thumbnail


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.topic = {'search_phrase': 'Great Pyramid of Giza', 'category': 'Ancient Egypt',
                      'facts': ['The Giza pyramid complex preserves ancient stone monuments.'],
                      'sources': ['https://whc.unesco.org/en/list/86/'], 'thumbnail_anchor': 'limestone blocks',
                      'thumbnail_visual_story': 'A narrow dark seam between colossal fitted limestone blocks dominates the right foreground, with one small human figure for scale',
                      'thumbnail_forbidden_terms': ['pyramid']}
        self.story = {'title': 'Draft', 'scenes': [{'summary': 'Limestone blocks remain'}]}
        self.payload = {
            'option_a': {'title': 'Great Pyramid of Giza: What the Stonework Tells Us',
                         'thumbnail_text': 'HOW WAS IT BUILT?',
                         'thumbnail_prompt': 'Ancient Egyptian limestone blocks on the right, one sunlit construction detail and open dark space on the left, no written letters.'},
            'option_b': {'title': 'The Shocking Great Pyramid of Giza Secret Exposed',
                         'thumbnail_text': 'WHAT WAS HIDDEN?',
                         'thumbnail_prompt': 'Limestone blocks fill the right side in natural light, with empty dark space on the left, no text.'},
            'option_c': {'title': 'Great Pyramid of Giza and the Questions Its Stones Raise',
                         'thumbnail_text': 'WHY THESE STONES?',
                         'thumbnail_prompt': 'Limestone blocks and a deep stone seam appear at right, dark open desert foreground at left, no letters.'},
            'description_intro': 'The Great Pyramid of Giza still raises questions about ancient Egyptian building. This documentary examines its surviving stonework.',
            'description_details': 'We follow the visible remains, the larger Memphis necropolis and the limits of the surviving evidence. Where the record is uncertain, the narration says so.',
            'tags': ['Giza pyramid', 'Egyptian pyramids', 'unrelated trending topic']}

    def test_metadata_frontloads_subject_and_rejects_clickbait(self):
        result = choose_package(self.story, self.topic, self.payload)
        self.assertTrue(result['title'].startswith('Great Pyramid of Giza'))
        self.assertNotIn('Shocking', result['title'])
        self.assertIn('Great Pyramid of Giza', result['description'].splitlines()[0])
        self.assertIn('All characters and events are fictional', result['description'])
        self.assertEqual(result['tags'][0], self.topic['search_phrase'])
        self.assertLessEqual(len(result['tags']), 12)
        self.assertNotIn('unrelated trending topic', result['tags'])
        self.assertTrue(result['thumbnail_prompt'].startswith(self.topic['thumbnail_visual_story']))
        self.assertLessEqual(len(result['thumbnail_prompt'].split()), 55)

    def test_rejects_unrelated_title_and_bad_opening(self):
        self.payload['option_a']['title'] = 'A Hidden Secret Nobody Wants You to Discover Today'
        self.payload['option_c']['title'] = 'An Unrelated Story About Ancient Sites and Objects'
        with self.assertRaisesRegex(ValueError, 'No accurate'):
            choose_package(self.story, self.topic, self.payload)

    def test_rejects_vague_or_unanchored_thumbnail_question(self):
        self.payload['option_a']['thumbnail_text'] = 'WHAT HAPPENED?'
        self.payload['option_c']['thumbnail_text'] = 'WHAT IS THIS?'
        with self.assertRaisesRegex(ValueError, 'No accurate'):
            choose_package(self.story, self.topic, self.payload)

    def test_malformed_artwork_uses_curated_composition_without_retry(self):
        for prompt in ('A generic desert panorama with empty space on the left.',
                       'Limestone blocks ' + 'in sunlight ' * 30):
            with self.subTest(prompt=prompt):
                payload = copy.deepcopy(self.payload)
                for key in ('option_a', 'option_b', 'option_c'):
                    payload[key]['thumbnail_prompt'] = prompt
                calls = []
                def ask(*args):
                    calls.append(args)
                    return payload
                result = request_package(copy.deepcopy(self.story), self.topic, ask,
                    pause=lambda _: self.fail('Curated artwork should avoid retry'))
                self.assertEqual(len(calls), 1)
                self.assertTrue(result['thumbnail_prompt'].startswith(self.topic['thumbnail_visual_story']))
                self.assertNotIn('generic desert', result['thumbnail_prompt'])

    def test_curated_artwork_still_rejects_forbidden_elements(self):
        self.topic['thumbnail_visual_story'] = 'Limestone blocks beside a pyramid in sunlight with dark space on the left'
        for key in ('option_a', 'option_b', 'option_c'):
            self.payload[key]['thumbnail_prompt'] = 'Too short'
        with self.assertRaisesRegex(ValueError, 'forbidden visual element'):
            choose_package(self.story, self.topic, self.payload)

    def test_every_topic_has_a_valid_curated_artwork_fallback(self):
        import json
        for topic in json.loads(Path('horror_topics.json').read_text()):
            with self.subTest(topic=topic['id']):
                prompt = artwork_candidate('', topic)
                self.assertTrue(25 <= len(prompt) <= 220)
                self.assertIn(topic['thumbnail_anchor'].casefold(), prompt.casefold())

    def test_thumbnail_is_wide_readable_and_small_enough(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source.png', Path(temp)/'thumbnail.jpg'
            Image.new('RGB', (1024, 576), (194, 138, 69)).save(source)
            render_thumbnail(source, 'HOW WAS IT BUILT?', target, 'Ancient Egypt and pyramids')
            with Image.open(target) as image:
                self.assertEqual(image.size, (1280, 720))
                self.assertLess(sum(image.getpixel((80, 590))), sum(image.getpixel((1200, 590))))
                self.assertGreater(sum(image.getpixel((900, 360))), sum(image.getpixel((900, 20))))
            self.assertLess(target.stat().st_size, 2*1024*1024)

    def test_long_valid_question_wraps_and_renders(self):
        hook = 'WHERE WERE MONUMENTS BUILT?'
        lines, size = hook_layout(hook)
        self.assertGreaterEqual(size, 56)
        self.assertLessEqual(len(lines), 3)
        self.payload['option_a']['thumbnail_text'] = hook
        result = choose_package(self.story, self.topic, self.payload)
        self.assertEqual(result['thumbnail_text'], hook)
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source.png', Path(temp)/'thumbnail.jpg'
            Image.new('RGB', (1024, 576), (194, 138, 69)).save(source)
            render_thumbnail(source, hook, target)
            self.assertTrue(target.exists())

    def test_unrenderable_question_is_rejected_during_packaging(self):
        self.payload['option_a']['thumbnail_text'] = 'WHAT SUPERCALIFRAGILISTIC?'
        self.payload['option_c']['thumbnail_text'] = 'WHAT SUPERCALIFRAGILISTIC?'
        with self.assertRaisesRegex(ValueError, 'cannot fit at readable size'):
            choose_package(self.story, self.topic, self.payload)

    def test_rejected_metadata_is_corrected_without_replacing_script(self):
        bad = copy.deepcopy(self.payload)
        for key in ('option_a', 'option_b', 'option_c'):
            bad[key]['thumbnail_prompt'] = 'Limestone blocks with no pyramid in the image, dark joint at right.'
        prompts, pauses = [], []
        def ask(prompt, limit, schema):
            prompts.append(prompt)
            return bad if len(prompts) == 1 else self.payload
        scenes = copy.deepcopy(self.story['scenes'])
        result = request_package(self.story, self.topic, ask, pause=pauses.append)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(pauses, [60])
        self.assertIn('forbidden visual element', prompts[1])
        self.assertEqual(result['scenes'], scenes)
        self.assertNotIn('pyramid', result['thumbnail_prompt'].lower())

    def test_retries_are_bounded_and_never_accept_invalid_metadata(self):
        attempts, pauses = [], []
        def ask(prompt, limit, schema):
            attempts.append(prompt)
            return {**self.payload, 'description_intro': 'Shocking proof exposed'}
        original = copy.deepcopy(self.story)
        with self.assertRaisesRegex(ValueError, 'prohibited clickbait'):
            request_package(self.story, self.topic, ask, pause=pauses.append)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(pauses, [60, 60])
        self.assertEqual(self.story, original)

    def test_description_failures_recover_without_regenerating_story(self):
        payload = copy.deepcopy(self.payload)
        payload.update(description_intro='A different subject opens this otherwise long enough description. More historical evidence follows.',
                       description_details='Too short')
        scenes = copy.deepcopy(self.story['scenes'])
        result = request_package(self.story, self.topic, lambda *args: payload,
                                 pause=lambda _: self.fail('Unnecessary regeneration'))
        self.assertTrue(result['description'].startswith(self.topic['search_phrase']))
        self.assertIn(self.topic['facts'][0], result['description'])
        self.assertEqual(result['scenes'], scenes)

    def test_curated_descriptions_cover_every_topic(self):
        import json
        for topic in json.loads(Path('horror_topics.json').read_text()):
            with self.subTest(topic=topic['id']):
                intro, details = description_parts({}, topic)
                self.assertTrue(80 <= len(intro) <= 400)
                self.assertTrue(80 <= len(details) <= 800)
                self.assertIn(topic['search_phrase'], intro.split(':')[0])
                for fact in topic['facts']:
                    self.assertIn(fact, details)

    def test_valid_description_is_preserved(self):
        self.assertEqual(description_parts(self.payload, self.topic),
            (self.payload['description_intro'], self.payload['description_details']))

    def test_decoder_schema_enforces_thumbnail_question_format(self):
        from jsonschema import Draft202012Validator
        from video_packaging import HOOK_SCHEMA
        validator = Draft202012Validator(HOOK_SCHEMA)
        for hook in ('WHO ANSWERS?', 'WHY THIS FLOOR?', 'WHAT IS BEHIND THE DOOR?'):
            self.assertTrue(validator.is_valid(hook), hook)
        for hook in ('THE LOCKED DOOR', 'WHO?', 'WHEN DID IT OPEN?',
                     'WHO ANSWERS', 'WHY  THIS FLOOR?',
                     'WHAT IS BEHIND THE LOCKED BASEMENT DOOR?',
                     'WHAT SUPERCALIFRAGILISTIC?'):
            self.assertFalse(validator.is_valid(hook), hook)
        def ask(prompt, limit, schema):
            for key in ('option_a', 'option_b', 'option_c'):
                self.assertEqual(schema['properties'][key]['properties']['thumbnail_text'],
                                 HOOK_SCHEMA)
            return self.payload
        request_package(self.story, self.topic, ask,
                        pause=lambda _: self.fail('Unnecessary retry'))

    def test_successful_metadata_does_not_retry(self):
        calls = []
        def ask(prompt, limit, schema):
            calls.append(prompt)
            return self.payload
        request_package(self.story, self.topic, ask, pause=lambda _: self.fail('Unnecessary pause'))
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()

class VisualPromptTests(unittest.TestCase):
    def test_sanitization_preserves_visual_anchor_at_start(self):
        from image_cpu import safer_prompt
        scene = 'Close-up of fitted limestone blocks with a dark joint at Giza.'
        prompt = safer_prompt(scene)
        self.assertTrue(prompt.startswith(scene))
        self.assertIn('fully clothed', prompt)
        self.assertIn('no violence', prompt)

    def test_horror_topics_are_fictional_and_have_unique_anchors(self):
        import json
        topics = json.loads(Path('horror_topics.json').read_text())
        self.assertGreaterEqual(len(topics), 12)
        self.assertEqual(len({topic['id'] for topic in topics}), len(topics))
        for topic in topics:
            self.assertEqual(topic['sources'], [])
            self.assertIn(topic['thumbnail_anchor'], topic['thumbnail_visual_story'])

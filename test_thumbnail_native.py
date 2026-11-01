"""Native thumbnail text is requested from the model, never painted afterward."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from thumbnail_design import complete_thumbnail_prompt, generate_thumbnail


class NativeThumbnailTests(unittest.TestCase):
    def test_thumbnail_failure_keeps_completed_artwork_and_does_not_change_scene_settings(self):
        story=dict(thumbnail_prompt='A locked red door with an unexplained shadow',
                   thumbnail_text='WHO LOCKED IT?')
        def fail(prompt,path,**kwargs):
            Path(path).write_bytes(b'incomplete')
            raise RuntimeError('Generation failed')
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{
                'IMAGE_BACKEND':'cpu','IMAGE_SEED':'42','IMAGE_NEGATIVE_PROMPT':'scene exclusions'}),patch(
                'image_cpu.generate',side_effect=fail):
            root=Path(temp)
            (root/'thumbnail-source.png').write_bytes(b'completed source')
            (root/'thumbnail.jpg').write_bytes(b'completed thumbnail')
            with self.assertRaisesRegex(RuntimeError,'Generation failed'):
                generate_thumbnail(story,root,'episode-a')
            self.assertEqual((root/'thumbnail-source.png').read_bytes(),b'completed source')
            self.assertEqual((root/'thumbnail.jpg').read_bytes(),b'completed thumbnail')
            self.assertFalse((root/'thumbnail-source.pending.png').exists())
            self.assertEqual(os.environ['IMAGE_SEED'],'42')
            self.assertEqual(os.environ['IMAGE_NEGATIVE_PROMPT'],'scene exclusions')

    def test_prompt_requests_exact_headline_brightness_and_varied_art_direction(self):
        prompts=[complete_thumbnail_prompt('A chess king on the right with dark space on the left, no text.',
                 'WHY FOLLOW HIM?',f'episode-{i}') for i in range(12)]
        self.assertGreaterEqual(len(set(prompts)),6)
        for prompt in prompts:
            self.assertIn('"WHY FOLLOW HIM?"',prompt)
            self.assertIn('saturated clean colors',prompt)
            self.assertIn('unresolved visual question',prompt)
            self.assertNotIn('no text',prompt)
            self.assertNotIn('dark space on the left',prompt)
            self.assertIn('without revealing the ending',prompt)

    def test_generator_gets_native_text_flag_and_episode_seed_then_export_preserves_art(self):
        story=dict(thumbnail_prompt='A chess king facing smaller pieces',thumbnail_text='WHY FOLLOW HIM?')
        def generate(prompt,path,**kwargs):
            self.assertTrue(kwargs['allow_text'])
            Image.new('RGB',(1024,576),(220,170,80)).save(path)
        with tempfile.TemporaryDirectory() as temp,patch('image_cpu.generate',side_effect=generate) as model:
            root=Path(temp)
            target=generate_thumbnail(story,root,'episode-a')
            first=model.call_args.kwargs['seed']
            with Image.open(target) as image:
                self.assertEqual(image.size,(1280,720))
                # No added typography, badges, gradients or dimming.
                self.assertLess(max(abs(a-b) for a,b in zip(image.getpixel((100,100)),(220,170,80))),4)
            manifest=json.loads((root/'thumbnail-design.json').read_text())
            self.assertEqual(manifest['text_mode'],'model-generated')
            generate_thumbnail(story,root,'episode-b')
            self.assertNotEqual(first,model.call_args.kwargs['seed'])

    def test_diffusion_allows_letters_only_for_thumbnail_requests(self):
        import types
        import image_cpu
        from unittest.mock import Mock
        model=Mock(return_value=types.SimpleNamespace(
            nsfw_content_detected=[False],images=[Image.new('RGB',(256,256),'yellow')]))
        torch=types.SimpleNamespace(set_num_threads=lambda _:None,
            Generator=lambda **kwargs:types.SimpleNamespace(manual_seed=lambda seed:seed))
        with tempfile.TemporaryDirectory() as temp,patch.object(image_cpu,'_pipeline',model),patch.dict(
                'sys.modules',{'torch':torch,'diffusers':types.SimpleNamespace(
                    DiffusionPipeline=object,DPMSolverMultistepScheduler=object)}):
            image_cpu.generate('Bright image with WHY FOLLOW HIM?',Path(temp)/'thumb.png',allow_text=True,seed=123)
            sent=model.call_args.kwargs
            self.assertNotIn('text, letters',sent['negative_prompt'])
            self.assertIn('misspelled words',sent['negative_prompt'])
            self.assertEqual(sent['generator'],123)
            image_cpu.generate('Text-free video scene',Path(temp)/'scene.png')
            self.assertIn('text, letters',model.call_args.kwargs['negative_prompt'])

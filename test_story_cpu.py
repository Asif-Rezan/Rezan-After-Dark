import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import story_cpu as s
import workflow as w


class QwenTests(unittest.TestCase):
    def payload(self):
        return {'model': s.MODEL_ID, 'messages': [{'role': 'user', 'content': 'private narration'}],
            'response_format': {'type': 'json_schema', 'json_schema': {'schema': {
                'type': 'object', 'properties': {'ready': {'type': 'boolean'}},
                'required': ['ready'], 'additionalProperties': False}}},
            'max_completion_tokens': 256}

    def result(self, content='{"ready":true}', finish='stop'):
        return {'choices': [{'finish_reason': finish, 'message': {'content': content}}]}

    def model(self, **kwargs):
        return Mock(tokenize=Mock(return_value=[1]), **kwargs)

    def test_local_generation_constrains_and_validates_original_schema(self):
        model = self.model(create_chat_completion=Mock(return_value=self.result()))
        payload = self.payload()
        original = json.dumps(payload)
        with patch.object(s, 'load_model', return_value=model), patch('requests.post') as post:
            self.assertEqual(w.request_story('chat/completions', payload), self.result())
        post.assert_not_called()
        self.assertEqual(json.dumps(payload), original)
        sent = model.create_chat_completion.call_args.kwargs
        self.assertEqual(sent['response_format'], {'type': 'json_object',
            'schema': payload['response_format']['json_schema']['schema']})
        self.assertNotIn('reasoning_effort', sent)
        self.assertIn('JSON Schema', sent['messages'][-1]['content'])

    def test_invalid_type_extra_fields_and_truncation_never_accepted(self):
        for content, finish in [('{"ready":"yes"}', 'stop'),
                                ('{"ready":true,"extra":1}', 'stop'),
                                ('private broken output', 'stop'),
                                ('{"ready":true}', 'length')]:
            with self.subTest(content=content, finish=finish):
                model = self.model(create_chat_completion=Mock(return_value=self.result(content, finish)))
                with patch.object(s, 'load_model', return_value=model), patch('builtins.print') as output:
                    with self.assertRaisesRegex(s.StoryGenerationError, 'three attempts') as caught:
                        s.request_story('chat/completions', self.payload())
                self.assertEqual(model.create_chat_completion.call_count, 3)
                self.assertNotIn(content, str(caught.exception))
                self.assertNotIn('private broken output', str(output.call_args_list))

    def test_invalid_json_can_recover_on_same_local_model(self):
        model = self.model(create_chat_completion=Mock(side_effect=[self.result('{}'), self.result()]))
        with patch.object(s, 'load_model', return_value=model):
            s.request_story('chat/completions', self.payload())
        self.assertEqual(model.create_chat_completion.call_count, 2)

    def test_context_overflow_stops_before_inference(self):
        model = self.model()
        model.tokenize.return_value = [1] * s.CONTEXT_TOKENS
        with patch.object(s, 'load_model', return_value=model):
            with self.assertRaisesRegex(s.StoryGenerationError, 'context budget'):
                s.request_story('chat/completions', self.payload())
        model.create_chat_completion.assert_not_called()

    def test_model_substitution_and_non_text_endpoints_are_refused(self):
        payload = self.payload()
        payload['model'] = 'different/model'
        with patch.object(s, 'load_model') as load:
            with self.assertRaisesRegex(s.StoryGenerationError, 'STORY_MODEL'):
                s.request_story('chat/completions', payload)
            with self.assertRaisesRegex(s.StoryGenerationError, 'script JSON'):
                s.request_story('audio/speech', self.payload())
        load.assert_not_called()

    def test_runtime_failure_does_not_print_or_retry_private_content(self):
        model = self.model(create_chat_completion=Mock(side_effect=RuntimeError('private narration')))
        with patch.object(s, 'load_model', return_value=model):
            with self.assertRaises(s.StoryGenerationError) as caught:
                s.request_story('chat/completions', self.payload())
        self.assertNotIn('private narration', str(caught.exception))
        model.create_chat_completion.assert_called_once()

    def test_model_checksum_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'model.gguf'
            path.write_bytes(b'expected weights')
            with patch.object(s, 'GGUF_SHA256', hashlib.sha256(b'expected weights').hexdigest()):
                self.assertTrue(s.verify_model(path))
                path.write_bytes(b'corrupt weights')
                self.assertFalse(s.verify_model(path))

    def test_model_load_is_pinned_anonymous_cpu_and_reused(self):
        llama = Mock(return_value=self.model())
        with patch.object(s, '_model', None), patch('huggingface_hub.hf_hub_download', return_value='/weights.gguf') as download, patch.dict('sys.modules', {'llama_cpp': types.SimpleNamespace(Llama=llama)}), patch.object(s, 'verify_model', return_value=True):
            model = s.load_model()
            self.assertIs(s.load_model(), model)
        download.assert_called_once_with(repo_id=s.GGUF_REPO, filename=s.GGUF_FILE,
            revision=s.GGUF_REVISION, token=False, force_download=False)
        self.assertEqual(llama.call_args.kwargs['n_gpu_layers'], 0)
        self.assertFalse(llama.call_args.kwargs['verbose'])
        llama.assert_called_once()

    def test_bad_download_is_bounded_and_runtime_does_not_load(self):
        llama = Mock()
        with patch.object(s, '_model', None), patch('huggingface_hub.hf_hub_download', return_value='/weights.gguf') as download, patch.dict('sys.modules', {'llama_cpp': types.SimpleNamespace(Llama=llama)}), patch.object(s, 'verify_model', return_value=False), patch.object(s.time, 'sleep'):
            with self.assertRaisesRegex(s.StoryGenerationError, 'three attempts'):
                s.load_model()
        self.assertEqual(download.call_count, 3)
        llama.assert_not_called()

    def test_hosted_audio_backends_cannot_make_remote_calls(self):
        with patch.dict('os.environ', {'TTS_BACKEND': 'hosted', 'CAPTION_BACKEND': 'hosted'}), patch('requests.post') as post:
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                w.make_narration('hello', Path('audio.wav'))
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                w.transcribe(Path('audio.wav'))
        post.assert_not_called()

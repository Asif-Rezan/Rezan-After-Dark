import unittest
from unittest.mock import Mock, patch
import requests
import workflow as w
from groq_client import GroqError, provider_error

@patch.dict('os.environ', {'GROQ_API_KEY': 'test-key'})
class GroqOnlyTests(unittest.TestCase):
    def response(self, status=200, content='{"ok":true}', finish='stop', headers=None):
        return Mock(ok=status==200,status_code=status,headers=headers or {},json=Mock(return_value={
            'choices':[{'finish_reason':finish,'message':{'content':content}}]}))

    def test_success_uses_only_groq(self):
        with patch('requests.post',return_value=self.response()) as post:
            w.request_groq('chat/completions',{})
        self.assertEqual(post.call_args.args[0],'https://api.groq.com/openai/v1/chat/completions')

    def test_temporary_failures_retry_same_provider(self):
        for failure in (self.response(429), self.response(503), requests.Timeout()):
            with self.subTest(failure=failure), patch('requests.post',side_effect=[failure,self.response()]) as post, patch.object(w.time,'sleep') as sleep:
                w.request_groq('chat/completions',{})
                self.assertEqual(post.call_count,2)
                self.assertTrue(all(c.args[0].startswith('https://api.groq.com/') for c in post.call_args_list))
                sleep.assert_called_once_with(2)

    def test_persistent_failure_is_bounded(self):
        with patch('requests.post',return_value=self.response(429)) as post, patch.object(w.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'HTTP 429'):
                w.request_groq('chat/completions',{})
        self.assertEqual(post.call_count,5)
        self.assertEqual(sleep.call_count,4)

    def test_auth_and_invalid_output_stop(self):
        for response in (self.response(401),self.response(content='invalid'),self.response(finish='length')):
            with self.subTest(response=response),patch('requests.post',return_value=response) as post,patch.object(w.time,'sleep') as sleep:
                with self.assertRaises(RuntimeError): w.request_groq('chat/completions',{})
                post.assert_called_once()
                sleep.assert_not_called()

    def test_cooldown_and_missing_key(self):
        with patch('requests.post',side_effect=[self.response(429,headers={'retry-after':'12.5'}),self.response()]),patch.object(w.time,'sleep') as sleep:
            w.request_groq('chat/completions',{})
        sleep.assert_called_once_with(13)
        with patch('requests.post',return_value=self.response(429,headers={'retry-after':'3600'})),patch.object(w.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'cooldown'): w.request_groq('chat/completions',{})
            sleep.assert_not_called()
        with patch.dict('os.environ',{'GROQ_API_KEY':''}),patch('requests.post') as post:
            with self.assertRaisesRegex(RuntimeError,'GROQ_API_KEY'): w.request_groq('chat/completions',{})
            post.assert_not_called()

    def structured_payload(self):
        return {'model': 'openai/gpt-oss-120b', 'messages': [{'role': 'user', 'content': 'Private prompt'}],
            'response_format': {'type': 'json_schema', 'json_schema': {'strict': True, 'schema': {
                'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                'required': ['ok'], 'additionalProperties': False}}}}

    def failure(self, code, message='Generated JSON does not match the expected schema'):
        return Mock(ok=False, status_code=400, headers={},
                    json=Mock(return_value={'error': {'code': code, 'message': message}}))

    def test_json_generation_failure_retries_then_validates_json_mode(self):
        payload = self.structured_payload()
        failure = self.failure('json_validate_failed')
        with patch('requests.post', side_effect=[failure, failure, self.response()]) as post, patch.object(w.time, 'sleep'):
            result = w.request_groq('chat/completions', payload)
        self.assertEqual(result['choices'][0]['message']['content'], '{"ok":true}')
        # Snapshot the format at request time; the helper must not mutate callers' payloads.
        self.assertEqual(payload['response_format']['type'], 'json_schema')
        self.assertEqual(post.call_args.kwargs['json']['response_format'], {'type': 'json_object'})
        self.assertIn('JSON Schema', post.call_args.kwargs['json']['messages'][-1]['content'])
        self.assertEqual(post.call_count, 3)

    def test_fallback_never_accepts_schema_invalid_output(self):
        failure = self.failure('invalid_json_schema', 'Unsupported schema')
        with patch('requests.post', side_effect=[failure] + [self.response(content='{"ok":"wrong type"}')] * 4) as post, patch.object(w.time, 'sleep'):
            with self.assertRaisesRegex(GroqError, 'invalid_json_output'):
                w.request_groq('chat/completions', self.structured_payload())
        self.assertEqual(post.call_count, 5)

    def test_generic_bad_request_and_model_errors_are_not_retried(self):
        for response in (self.failure(None, 'Invalid request'),
                         self.failure('model_decommissioned', 'Model decommissioned')):
            with self.subTest(response=response), patch('requests.post', return_value=response) as post, patch.object(w.time, 'sleep') as sleep:
                with self.assertRaises(GroqError) as caught:
                    w.request_groq('chat/completions', self.structured_payload())
                self.assertEqual(caught.exception.http_status, 400)
                post.assert_called_once()
                sleep.assert_not_called()

    def test_diagnostics_do_not_expose_provider_content_or_credentials(self):
        private = 'gsk_private-secret unpublished narration someone@example.com'
        response = Mock(status_code=400, headers={'x-request-id': private}, json=Mock(return_value={
            'error': {'message': private, 'code': private, 'type': private,
                      'param': private, 'failed_generation': private}}))
        error = provider_error(response)
        self.assertNotIn(private, str(error))
        self.assertNotIn('gsk_', str(error))
        self.assertIsNone(error.request_id)
        self.assertIsNone(error.param)

    def test_daily_schema_is_shared_with_readiness(self):
        from groq_check import check
        schema = w.story_outline_schema()
        self.assertEqual(len(schema['properties']['scenes']['properties']), 20)
        def placeholder(node):
            if node['type'] == 'object':
                return {key: placeholder(value) for key, value in node['properties'].items()}
            if node['type'] == 'array':
                return [placeholder(node['items'])]
            return 'test'
        with patch('requests.post', return_value=self.response(content=__import__('json').dumps(placeholder(schema)))) as post:
            self.assertEqual(check()['chapters'], 20)
        self.assertEqual(post.call_args.kwargs['json']['response_format']['json_schema']['schema'], schema)

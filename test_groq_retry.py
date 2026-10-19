import unittest
from unittest.mock import Mock, patch
import requests
import workflow as w

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

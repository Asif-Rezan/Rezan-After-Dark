"""Exercise the actual daily model/outline schema without generating private content."""
import json
import os

import workflow as w
from groq_client import GroqError


def check():
    result = w.request_groq('chat/completions', {
        'model': os.getenv('STORY_MODEL', 'openai/gpt-oss-120b'),
        'messages': [{'role': 'user', 'content':
            'This is an API readiness test. Return a JSON outline matching the schema. '
            'Use short placeholder strings, a single tag, and one short image_prompts item '
            'for each chapter. Include all twenty chapter keys. No story narration.'}],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'story_payload', 'strict': True, 'schema': w.story_outline_schema()}},
        'reasoning_effort': 'low', 'temperature': .8, 'max_completion_tokens': 5200})
    # request_groq locally validates the same schema used by real story generation.
    return {'check': 'Groq daily structured-output readiness', 'passed': True,
            'chapters': len(json.loads(result['choices'][0]['message']['content'])['scenes'])}


if __name__ == '__main__':
    try:
        print(json.dumps(check()), flush=True)
    except GroqError as error:
        print(json.dumps({'check': 'Groq daily structured-output readiness',
                          'passed': False, **error.diagnostics()}), flush=True)
        raise SystemExit(1)

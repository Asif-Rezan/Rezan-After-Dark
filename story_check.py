"""Credential-free smoke test of the actual Qwen runtime and structured output."""
import json
import os
from pathlib import Path

from story_cpu import MODEL_ID, StoryGenerationError, request_story


def check():
    result = request_story('chat/completions', {'model': MODEL_ID,
        'messages': [{'role': 'user', 'content':
            'Write a short, non-graphic fictional horror opening, about 70 words, '
            'in the narration field. Set ready to true.'}],
        'response_format': {'type': 'json_schema', 'json_schema': {'schema': {
            'type': 'object', 'properties': {'ready': {'type': 'boolean'},
                'narration': {'type': 'string'}},
            'required': ['ready', 'narration'], 'additionalProperties': False}}},
        'max_completion_tokens': 256, 'temperature': .7})
    content = json.loads(result['choices'][0]['message']['content'])
    if content['ready'] is not True or len(content['narration'].split()) < 30:
        raise StoryGenerationError('Qwen readiness output did not contain a usable narration')
    return {'check': 'Qwen script generation readiness', 'passed': True, 'model': MODEL_ID}


if __name__ == '__main__':
    try:
        print(json.dumps(check()), flush=True)
    except (StoryGenerationError, ImportError) as error:
        reason = str(error) if isinstance(error, StoryGenerationError) else 'Install requirements-story.txt before running Qwen'
        print(json.dumps({'check': 'Qwen script generation readiness', 'passed': False, 'reason': reason}), flush=True)
        if os.getenv('GITHUB_ACTIONS') == 'true':
            print('::error title=Qwen readiness failed::' + reason, flush=True)
            if os.getenv('GITHUB_STEP_SUMMARY'):
                with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
                    stream.write('## Qwen readiness failed\n\n' + reason + '\n')
        raise SystemExit(1)

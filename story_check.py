"""Credential-free smoke test of the actual Qwen runtime and structured output."""
import json
import os
from pathlib import Path

from story_cpu import MODEL_ID, StoryGenerationError, request_story
from workflow import narration_batch


def check():
    def ask(prompt, limit, schema):
        result = request_story('chat/completions', {'model': MODEL_ID,
            'messages': [{'role': 'user', 'content': prompt}],
            'response_format': {'type': 'json_schema', 'json_schema': {'schema': schema}},
            'max_completion_tokens': 512, 'temperature': .7})
        return json.loads(result['choices'][0]['message']['content'])
    parts = narration_batch('Write chapter_01 as an original non-graphic fictional horror opening. '
        'An adult woman finds wet footprints leading into her locked basement. '
        'Write about 110 words, building suspense through concrete action and sensory details.',
        ['chapter_01'], 110, ask)
    return {'check': 'Qwen script generation readiness', 'passed': True, 'model': MODEL_ID,
            'narration_words': len(parts[0].split())}


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

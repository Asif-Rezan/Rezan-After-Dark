"""Exercise the actual daily model/outline schema without generating private content."""
import json
import os

import workflow as w
from groq_client import GroqError


def failure_guidance(error):
    if error.code == 'organization_restricted':
        return ('Groq has restricted the organization associated with GROQ_API_KEY. '
                'Restore access with Groq support. If GitHub contains a stale key from '
                'a different organization, replace the repository secret with the intended '
                'authorized account key. Retrying, changing models, or rotating a key '
                'inside the restricted organization will not restore access.')
    return {
        'authentication_failed': 'Update the repository GROQ_API_KEY using the intended authorized Groq account.',
        'access_denied': 'Check Groq organization and model permissions for the repository key.',
        'model_unavailable': 'Check STORY_MODEL against the models available to this Groq project.',
        'context_limit': 'Reduce the request context before retrying.',
        'token_budget_rejected': 'Review the Groq project token limits and completion budget.',
    }.get(error.reason, 'Inspect the safe error category and request ID; repair persistent errors before rerunning the daily job.')


def report_failure(error):
    from pathlib import Path
    guidance = failure_guidance(error)
    print(json.dumps({'check': 'Groq daily structured-output readiness',
                      'passed': False, **error.diagnostics(), 'action': guidance}), flush=True)
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print('::error title=Groq readiness blocked::' + guidance, flush=True)
        summary = os.getenv('GITHUB_STEP_SUMMARY')
        if summary:
            with Path(summary).open('a') as stream:
                stream.write('## Groq generation blocked\n\n' + guidance + '\n\n'
                    '[Groq console](https://console.groq.com/) · '
                    '[Repository Actions secrets](https://github.com/Asif-Rezan/Rezan-After-Dark/settings/secrets/actions)\n\n'
                    'No video generation or upload was started by this readiness check.\n')


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
        report_failure(error)
        raise SystemExit(1)
    except RuntimeError:
        # Missing key is a configuration failure, never print its value.
        report_failure(GroqError(None, 'authentication_failed'))
        raise SystemExit(1)

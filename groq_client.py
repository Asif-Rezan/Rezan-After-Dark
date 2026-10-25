"""Groq-only JSON requests with bounded recovery and privacy-safe diagnostics."""
import copy
import hashlib
import json
import math
import re
import time

import requests
from jsonschema import Draft202012Validator, ValidationError


class GroqError(RuntimeError):
    def __init__(self, status, reason, *, code=None, param=None, request_id=None):
        self.http_status = status
        self.reason = reason
        self.code = code
        self.param = param
        self.request_id = request_id
        details = {k: v for k, v in self.diagnostics().items() if v is not None}
        super().__init__(f'Groq request failed with HTTP {status}: ' + json.dumps(details, sort_keys=True))

    def diagnostics(self):
        return dict(http_status=self.http_status, reason=self.reason, code=self.code,
                    param=self.param, request_id=self.request_id)


def provider_error(response):
    """Never copy raw provider messages/failed_generation into public Actions logs."""
    try:
        error = response.json().get('error', {})
        if not isinstance(error, dict):
            error = {}
    except (ValueError, AttributeError):
        error = {}
    message = str(error.get('message', '')).lower()
    known_codes = {'json_validate_failed', 'invalid_json_schema', 'context_length_exceeded',
                   'model_decommissioned', 'model_not_found', 'invalid_api_key',
                   'rate_limit_exceeded', 'organization_restricted', 'model_permission_blocked'}
    code = error.get('code')
    code = code if isinstance(code, str) and code in known_codes else None
    reason = 'bad_request'
    if code in ('json_validate_failed',) or 'generated json' in message or 'failed to generate json' in message:
        reason = 'json_generation_failed'
    elif code == 'invalid_json_schema' or ('schema' in message and any(
            word in message for word in ('invalid', 'unsupported', 'not supported', 'too many', 'exceed'))):
        reason = 'schema_rejected'
    elif 'response_format' in message and any(word in message for word in ('unsupported', 'not supported')):
        reason = 'structured_outputs_unavailable'
    elif code == 'context_length_exceeded' or any(word in message for word in ('context length', 'context window', 'reduce the length')):
        reason = 'context_limit'
    elif code in ('model_decommissioned', 'model_not_found') or any(
            word in message for word in ('decommissioned', 'does not exist', 'model not found')):
        reason = 'model_unavailable'
    elif response.status_code == 401:
        reason = 'authentication_failed'
    elif response.status_code == 403 or code in ('organization_restricted', 'model_permission_blocked'):
        reason = 'access_denied'
    elif response.status_code == 429:
        reason = 'rate_limited'
    elif response.status_code >= 500:
        reason = 'provider_unavailable'
    elif any(word in message for word in ('max_completion_tokens', 'max_tokens', 'token limit', 'tokens per minute')):
        reason = 'token_budget_rejected'
    elif any(word in message for word in ('reasoning_effort', 'temperature')):
        reason = 'parameter_rejected'
    param = error.get('param')
    param = param if isinstance(param, str) and param in {
        'model', 'messages', 'response_format', 'response_format.json_schema',
        'max_completion_tokens', 'max_tokens', 'temperature', 'reasoning_effort'} else None
    request_id = response.headers.get('x-request-id')
    if not isinstance(request_id, str) or not re.fullmatch(r'req_[A-Za-z0-9_-]{1,100}', request_id):
        request_id = None
    # Fingerprint unknown errors for correlation without disclosing their body.
    if reason == 'bad_request':
        code = 'unclassified_' + hashlib.sha256(message.encode()).hexdigest()[:12]
    return GroqError(response.status_code, reason, code=code, param=param, request_id=request_id)


def request_json(endpoint, payload, key, binary=False):
    body = copy.deepcopy(payload)
    schema = body.get('response_format', {}).get('json_schema', {}).get('schema')
    validator = None
    if schema is not None:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    json_failures = 0
    for attempt in range(5):
        delay = min(2 ** (attempt + 1), 30)
        response = None
        failure = None
        try:
            response = requests.post('https://api.groq.com/openai/v1/' + endpoint,
                headers={'Authorization': 'Bearer ' + key}, json=body, timeout=180)
        except requests.RequestException:
            failure = GroqError(None, 'transport_failed')
        else:
            if response.ok:
                if binary:
                    return response.content
                try:
                    result = response.json()
                    choice = result['choices'][0]
                    if choice.get('finish_reason') != 'stop' or not choice['message'].get('content'):
                        raise ValueError('Incomplete response')
                    value = json.loads(choice['message']['content'])
                    if validator:
                        validator.validate(value)
                    return result
                except (ValueError, KeyError, IndexError, TypeError, ValidationError):
                    failure = GroqError(response.status_code, 'invalid_json_output')
            else:
                failure = provider_error(response)
        recoverable_json = validator is not None and failure.reason in {
            'json_generation_failed', 'schema_rejected', 'structured_outputs_unavailable', 'invalid_json_output'}
        if recoverable_json:
            json_failures += 1
            # Try strict generation again once; fall back only to Groq JSON mode.
            # Local validation enforces the SAME schema before any content is used.
            if (json_failures >= 2 or failure.reason in {'schema_rejected', 'structured_outputs_unavailable'}) and body.get('response_format', {}).get('type') == 'json_schema':
                body['response_format'] = {'type': 'json_object'}
                body['messages'] = body.get('messages', []) + [{'role': 'user', 'content':
                    'Return ONLY a JSON object that exactly matches this JSON Schema. '
                    'All required keys must be present, with no extra keys. Schema: ' + json.dumps(schema)}]
        retryable = recoverable_json or failure.http_status in (429, 500, 502, 503, 504) or failure.reason == 'transport_failed'
        if not retryable or attempt == 4:
            raise failure from None
        if response is not None:
            retry_after = response.headers.get('retry-after')
            if retry_after:
                try:
                    seconds = float(retry_after)
                except (TypeError, ValueError):
                    seconds = 0
                if not math.isfinite(seconds) or seconds > 120:
                    raise GroqError(failure.http_status, 'cooldown_exceeds_retry_budget') from None
                delay = max(delay, math.ceil(seconds))
        print(json.dumps({'check': 'Groq retry', 'attempt': attempt + 1,
            'retry_seconds': delay, 'format': body.get('response_format', {}).get('type'),
            **failure.diagnostics()}), flush=True)
        time.sleep(delay)

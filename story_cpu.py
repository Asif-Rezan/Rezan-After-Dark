"""Run Qwen3-4B-Instruct-2507 locally; no hosted text API or credentials."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time

from jsonschema import Draft202012Validator, ValidationError

MODEL_ID = 'Qwen/Qwen3-4B-Instruct-2507'
GGUF_REPO = 'unsloth/Qwen3-4B-Instruct-2507-GGUF'
GGUF_REVISION = 'a06e946bb6b655725eafa393f4a9745d460374c9'
GGUF_FILE = 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
GGUF_SHA256 = '3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597'
CONTEXT_TOKENS = 16384
_model = None


class StoryGenerationError(RuntimeError):
    """Only static, privacy-safe messages may be emitted in public job logs."""


def verify_model(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == GGUF_SHA256


def load_model():
    global _model
    if _model is not None:
        return _model
    from huggingface_hub import hf_hub_download
    from llama_cpp import Llama
    for attempt in range(3):
        try:
            path = hf_hub_download(repo_id=GGUF_REPO, filename=GGUF_FILE,
                revision=GGUF_REVISION, token=False, force_download=attempt > 0)
            if not verify_model(path):
                raise StoryGenerationError('Qwen model checksum verification failed')
            break
        except Exception:
            if attempt == 2:
                raise StoryGenerationError('Qwen model download or checksum failed after three attempts') from None
            print(json.dumps({'check': 'Qwen model download retry', 'attempt': attempt + 1}), flush=True)
            time.sleep(2 ** (attempt + 1))
    threads = min(max(1, int(os.getenv('OMP_NUM_THREADS', '4'))), os.cpu_count() or 1)
    try:
        _model = Llama(model_path=path, n_ctx=CONTEXT_TOKENS, n_threads=threads,
            n_threads_batch=threads, n_gpu_layers=0, n_batch=256, use_mmap=True,
            chat_format='chatml', verbose=False)
    except Exception:
        raise StoryGenerationError('Qwen CPU runtime could not load the verified model') from None
    print(json.dumps({'check': 'Qwen CPU model loaded', 'model': MODEL_ID,
        'quantization': 'Q4_K_M', 'threads': threads}), flush=True)
    return _model


def request_story(endpoint, payload):
    if endpoint != 'chat/completions':
        raise StoryGenerationError('Qwen supports script JSON generation only')
    if payload.get('model', MODEL_ID) != MODEL_ID or os.getenv('STORY_MODEL', MODEL_ID) != MODEL_ID:
        raise StoryGenerationError('STORY_MODEL must be Qwen/Qwen3-4B-Instruct-2507')
    schema = payload.get('response_format', {}).get('json_schema', {}).get('schema')
    if not isinstance(schema, dict):
        raise StoryGenerationError('Script generation requires an explicit JSON schema')
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    messages = copy.deepcopy(payload['messages'])
    messages.append({'role': 'user', 'content': 'Return only JSON matching this schema. '
        'Do not add commentary or markdown. JSON Schema: ' + json.dumps(schema)})
    limit = int(payload.get('max_completion_tokens', 4200))
    if not 1 <= limit <= 6000:
        raise StoryGenerationError('Script output token budget must be between 1 and 6000')
    model = load_model()
    # Include chat markers and the BOS token in the conservative context allowance.
    prompt_tokens = sum(len(model.tokenize(message['content'].encode(), add_bos=False)) + 16
                        for message in messages) + 32
    if prompt_tokens + limit > CONTEXT_TOKENS:
        raise StoryGenerationError('Qwen context budget exceeded; reduce recent-story context')
    for attempt in range(3):
        started = time.monotonic()
        try:
            result = model.create_chat_completion(messages=messages,
                response_format={'type': 'json_object', 'schema': schema},
                max_tokens=limit, temperature=max(.2, float(payload.get('temperature', .7)) - .15 * attempt),
                top_p=.8, top_k=20, repeat_penalty=1.05, seed=1700 + attempt)
        except Exception:
            # Runtime/configuration errors are not content errors; don't spin on them.
            raise StoryGenerationError('Qwen CPU generation failed; inspect runner memory and runtime compatibility') from None
        try:
            choice = result['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ValueError('Incomplete output')
            validator.validate(json.loads(choice['message']['content']))
        except (ValueError, TypeError, KeyError, IndexError, ValidationError):
            print(json.dumps({'check': 'Qwen JSON validation retry', 'attempt': attempt + 1}), flush=True)
            if attempt == 2:
                raise StoryGenerationError('Qwen returned incomplete or invalid script JSON after three attempts') from None
            continue
        print(json.dumps({'check': 'Qwen script JSON', 'passed': True,
            'seconds': round(time.monotonic() - started, 1),
            'completion_tokens': result.get('usage', {}).get('completion_tokens')}), flush=True)
        return result

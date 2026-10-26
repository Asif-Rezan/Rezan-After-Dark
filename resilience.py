"""Bounded retries for repeatable local work; never wrap video insertion."""
import json
import socket
import time
from pathlib import Path


def transient(error):
    import requests
    import httpx
    status = getattr(error, 'http_status', None) or getattr(getattr(error, 'response', None), 'status_code', None)
    return (isinstance(error, (TimeoutError, ConnectionError, socket.timeout,
                               requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                               httpx.TransportError))
            or status in (408, 429, 500, 502, 503, 504))


def retry_local(operation, label, pause=None):
    pause = pause or time.sleep
    for attempt in range(3):
        try:
            return operation()
        except Exception as error:
            if not transient(error) or attempt == 2:
                raise
            # Exceptions can contain credentials or unpublished content. Log types only.
            print(json.dumps({'check': label, 'retry': attempt+1,
                              'error_type': type(error).__name__}), flush=True)
            pause(2 ** attempt)


def produce_file(path, producer, label):
    """Publish a completed file atomically; failed candidates are never reused."""
    path = Path(path)
    candidate = path.with_name(path.stem + '.pending' + path.suffix)
    def produce():
        try:
            producer(candidate)
            if not candidate.is_file() or candidate.stat().st_size == 0:
                raise RuntimeError('Producer returned no usable output')
            candidate.replace(path)
        finally:
            candidate.unlink(missing_ok=True)
    return retry_local(produce, label)

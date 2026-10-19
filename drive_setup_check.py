"""Configure only the app's private folder and inspect readiness safely."""
import json
from pathlib import Path
import tempfile
from workflow import Store, load_music

try:
    store = Store()
    print(json.dumps({'check': 'Private Drive workflow folder', 'passed': True}))
    manifest = store.load('music-manifest.json', None)
    if not manifest:
        print(json.dumps({'check': 'YouTube Audio Library music', 'configured': False}))
    else:
        with tempfile.TemporaryDirectory() as folder:
            load_music(store, Path(folder))
        print(json.dumps({'check': 'YouTube Audio Library music', 'configured': True, 'hash_verified': True}))
except Exception as error:
    status = getattr(getattr(error, 'resp', None), 'status', None)
    print(json.dumps({'check': 'Drive setup and music readiness', 'passed': False,
                      'error_type': type(error).__name__, 'http_status': status}))
    raise SystemExit(1)

"""Verify an existing private upload; permanently clean only tagged run media."""
import hashlib
import json
import re
import time


def disposable(name):
    return name in {'plan.json', 'narration.wav', 'captions.srt', 'video.mp4',
        'thumbnail.jpg', 'music.audio', 'thumbnail-source.png'} or bool(
        re.fullmatch(r'shot-\d+\.(png|mp4|qa\.json)', name))


def validate_remote(video, state, channel):
    if video.get('id') != state.get('video_id'):
        raise RuntimeError('Video identity mismatch')
    snippet, status = video['snippet'], video['status']
    story = state['story']
    if (snippet['channelId'] != channel or snippet['title'] != story['title']
        or set(snippet.get('tags', [])) != set(story['tags'])
        or snippet['description'] != state.get('upload_description', story['description'])):
        raise RuntimeError('Channel or metadata mismatch')
    if (status.get('uploadStatus') in ('failed', 'rejected', 'deleted')
        or video.get('processingDetails', {}).get('processingStatus') in ('failed', 'terminated')):
        raise RuntimeError('YouTube upload failed')
    return (status.get('uploadStatus') == 'processed'
        and video.get('processingDetails', {}).get('processingStatus') == 'succeeded'
        and video.get('contentDetails', {}).get('definition') == 'hd')


def cleanup(store, run, state):
    if not re.fullmatch(r'daily-\d+', run):
        raise ValueError('Invalid daily run ID')
    proof = state.get('verification', {})
    if (state.get('phase') != 'verified_private' or proof.get('video_id') != state.get('video_id')
        or not all(proof.get(k) is True for k in ('hd', 'metadata', 'processed', 'private'))):
        raise RuntimeError('Verified private upload required before deletion')
    key = f'runs/{run}/cleanup.json'
    journal = store.load(key, None)
    if journal is None:
        files, token = [], None
        while True:
            result = store.client.files().list(q=f"'{store.folder}' in parents and trashed = false and "
                f"appProperties has {{ key='automation_run' and value='{run}' }} and "
                "appProperties has { key='disposable' and value='v1' }",
                fields='nextPageToken,files(id,name,appProperties,parents,mimeType)',
                pageSize=1000, pageToken=token).execute()
            files.extend(result.get('files', []))
            token = result.get('nextPageToken')
            if not token:
                break
        journal = {'run': run, 'video_id': state['video_id'], 'files': files, 'deleted': []}
        store.save(key, journal)  # Durable ID manifest before any destructive operation.
    if journal['run'] != run or journal['video_id'] != state['video_id']:
        raise RuntimeError('Cleanup journal identity mismatch')
    for entry in journal['files']:
        fid = entry['id']
        if fid in journal['deleted']:
            continue
        for attempt in range(4):
            try:
                remote = store.client.files().get(fileId=fid,
                    fields='id,name,parents,appProperties,mimeType').execute()
                name = remote['name'].removeprefix(f'runs__{run}__')
                props = remote.get('appProperties', {})
                if (remote['name'] != f'runs__{run}__{name}' or not disposable(name)
                    or store.folder not in remote.get('parents', [])
                    or props.get('automation_run') != run or props.get('disposable') != 'v1'
                    or props.get('workflow_key') != hashlib.sha256(f'runs/{run}/{name}'.encode()).hexdigest()
                    or remote.get('mimeType') == 'application/vnd.google-apps.folder'):
                    raise ValueError('Unsafe cleanup target rejected')
                store.check_private(fid)
                store.client.files().delete(fileId=fid).execute()
                break
            except ValueError:
                raise
            except Exception as error:
                if getattr(getattr(error, 'resp', None), 'status', None) == 404:
                    break  # Previous deletion succeeded before checkpoint was saved.
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        journal['deleted'].append(fid)
        if len(journal['deleted']) % 10 == 0:
            store.save(key, journal)
            print(json.dumps({'check': 'Drive cleanup', 'deleted': len(journal['deleted']),
                              'total': len(journal['files'])}), flush=True)
    journal['complete'] = True
    store.save(key, journal)


def main():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from workflow import Store, required
    run = required('SAVED_RUN_ID')
    if not re.fullmatch(r'daily-\d+', run):
        raise ValueError('Only daily run IDs are accepted')
    store = Store()
    key = f'runs/{run}/state.json'
    state = store.load(key, {})
    if state.get('phase') not in ('uploaded', 'complete', 'verified_private') or not state.get('video_id'):
        raise RuntimeError('No successful upload to verify; no cleanup allowed')
    if state.get('thumbnail_uploaded') is not True:
        raise RuntimeError('Thumbnail upload unconfirmed; preserve all media')
    client = build('youtube', 'v3', credentials=Credentials.from_authorized_user_info(
        json.loads(required('YOUTUBE_TOKEN_JSON'))), cache_discovery=False)
    deadline = time.monotonic() + 1800
    while True:
        items = client.videos().list(part='snippet,status,processingDetails,contentDetails',
            id=state['video_id']).execute().get('items', [])
        if not items:
            raise RuntimeError('Uploaded video unavailable')
        video = items[0]
        if validate_remote(video, state, required('EXPECTED_CHANNEL_ID')):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('HD processing pending; preserve all media')
        time.sleep(20)
    if video['status']['privacyStatus'] != 'private':
        raise RuntimeError('Video must remain private; preserve all media')
    state.update(phase='verified_private', verification={'video_id': state['video_id'],
        'hd': True, 'processed': True, 'metadata': True, 'private': True})
    store.save(key, state)
    history = store.load('history.json', [])
    if not any(h.get('run') == run for h in history):
        history.append({'run': run, 'video_id': state['video_id'], 'title': state['story']['title'], 'hash': state['hash']})
    store.save('history.json', history)
    cleanup(store, run, state)
    print(json.dumps({'run': run, 'video_id': state['video_id'], 'privacy': 'private', 'verified': True, 'cleanup_complete': True}))


if __name__ == '__main__':
    main()

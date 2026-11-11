"""Verify an existing private upload; permanently clean only tagged run media."""
import hashlib
import json
import re
import time


def disposable(name):
    return name in {'plan.json', 'narration.wav', 'captions.srt', 'video.mp4',
        'thumbnail.jpg', 'music.audio', 'thumbnail-source.png'} or bool(
        re.fullmatch(r'(?:shot-\d+\.(?:png|mp4|qa\.json)|speech-[a-f0-9]{64}\.wav|render-0(?:0[0-9]|1[0-5])\.mp4)', name))


def validate_remote(video, state, channel):
    if video.get('id') != state.get('video_id'):
        raise RuntimeError('Video identity mismatch')
    snippet, status = video['snippet'], video['status']
    story = state['story']
    metadata = {
        'channel': snippet.get('channelId') == channel,
        'title': snippet.get('title') == story['title'],
        'description': snippet.get('description') == state.get('upload_description', story['description']),
    }
    mismatches = [field for field, matches in metadata.items() if not matches]
    if mismatches:
        raise RuntimeError('YouTube metadata mismatch: ' + ', '.join(mismatches))
    if state.get('format') == 'english-fluency':
        from fluency_lessons import PREFIX
        details = {
            'requested_first_tag': bool(story.get('tags')) and story['tags'][0] == PREFIX,
            'education_category': snippet.get('categoryId') == '27',
            'metadata_language': snippet.get('defaultLanguage') == 'en',
            'audio_language': snippet.get('defaultAudioLanguage') == 'en',
            'youtube_license': status.get('license') == 'youtube',
            'adult_audience': status.get('selfDeclaredMadeForKids') is False,
            'embedding': status.get('embeddable') is True,
            'public_statistics': status.get('publicStatsViewable') is True,
            'english_captions': state.get('captions_uploaded') is True,
        }
        failures = [name for name, passed in details.items() if not passed]
        if failures:
            raise RuntimeError('English lesson verification incomplete: ' + ', '.join(failures))
    if (status.get('uploadStatus') in ('failed', 'rejected', 'deleted')
        or video.get('processingDetails', {}).get('processingStatus') in ('failed', 'terminated')):
        raise RuntimeError('YouTube upload failed')
    ready = (status.get('uploadStatus') == 'processed'
        and video.get('processingDetails', {}).get('processingStatus') == 'succeeded'
        and video.get('contentDetails', {}).get('definition') == 'hd')
    if (ready and state.get('format') != 'english-fluency'
            and set(snippet.get('tags', [])) != set(story['tags'])):
        raise RuntimeError('YouTube metadata mismatch: tags')
    return ready


def restore_tags(client, video, tags):
    snippet = video['snippet']
    # Updating the snippet replaces its writable properties, so retain the
    # verified values and send only the snippet part to preserve video status.
    fields = ('title', 'description', 'categoryId', 'defaultLanguage', 'defaultAudioLanguage')
    body = {'id': video['id'], 'snippet': {field: snippet[field] for field in fields}}
    body['snippet']['tags'] = tags
    response = client.videos().update(part='snippet', body=body).execute()
    returned_tags = response.get('snippet', {}).get('tags', [])
    print(json.dumps({'check': 'YouTube tag update', 'expected_count': len(tags),
                      'response_count': len(returned_tags),
                      'response_matches': set(returned_tags) == set(tags)}), flush=True)


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
    print(f"::notice title=Drive cleanup complete::{run}: {len(journal['deleted'])} tagged files accounted for.",
          flush=True)


def verify_run(store, client, run, channel, *, repair_tags=False, wait=True):
    from googleapiclient.errors import HttpError

    key = f'runs/{run}/state.json'
    state = store.load(key, {})
    if state.get('phase') not in ('uploaded', 'complete', 'verified_private') or not state.get('video_id'):
        raise RuntimeError('No successful upload to verify; no cleanup allowed')
    if state.get('thumbnail_uploaded') is not True:
        raise RuntimeError('Thumbnail upload unconfirmed; preserve all media')
    deadline = time.monotonic() + 1800
    tag_update_attempted = False
    while True:
        items = client.videos().list(part='snippet,status,processingDetails,contentDetails',
            id=state['video_id']).execute().get('items', [])
        if not items:
            raise RuntimeError('Uploaded video unavailable')
        video = items[0]
        if video['status']['privacyStatus'] != 'private':
            raise RuntimeError('Video must remain private; preserve all media')
        if validate_remote(video, state, channel):
            returned_tags = video['snippet'].get('tags', [])
            expected_tags = state['story']['tags']
            tags_match = set(returned_tags) == set(expected_tags)
            if not tags_match and repair_tags and not tag_update_attempted:
                tag_update_attempted = True
                try:
                    restore_tags(client, video, expected_tags)
                except HttpError as error:
                    print(f'::warning title=YouTube tag update failed::HTTP {error.resp.status}. '
                          'Checking the private upload again.', flush=True)
                continue
            if not tags_match:
                print(f'::warning title=YouTube tag mismatch::{run}: YouTube returned different tags. '
                      'The verified private upload is eligible for run-scoped Drive cleanup.', flush=True)
            break
        if not wait or time.monotonic() >= deadline:
            raise RuntimeError('HD processing pending; preserve all media')
        time.sleep(20)
    if not wait and state.get('format') == 'english-fluency':
        caption_id = state.get('caption_id')
        if not caption_id:
            raise RuntimeError('Saved English caption ID is missing')
        captions = client.captions().list(
            part='snippet', videoId=state['video_id'], id=caption_id).execute().get('items', [])
        if not any(track['id'] == caption_id
                   and track['snippet'].get('language') == 'en'
                   and track['snippet'].get('status') == 'serving'
                   and not track['snippet'].get('isDraft') for track in captions):
            raise RuntimeError('Saved English captions are not serving')
    state.update(phase='verified_private', verification={'video_id': state['video_id'],
        'hd': True, 'processed': True, 'metadata': True, 'private': True,
        'tags_match': tags_match,
        'tag_order_normalized': tags_match and returned_tags != expected_tags})
    store.save(key, state)
    history = store.load('history.json', [])
    if not any(h.get('run') == run for h in history):
        history.append({'run': run, 'video_id': state['video_id'], 'title': state['story']['title'], 'hash': state['hash']})
    store.save('history.json', history)
    return state


def cleanup_previous_runs(store, client, current_run, channel):
    from googleapiclient.errors import HttpError
    from workflow import drive_retry

    previous_runs = set()
    token = None
    while True:
        result = drive_retry(lambda: store.client.files().list(
            q=f"'{store.folder}' in parents and trashed = false and "
              "appProperties has { key='disposable' and value='v1' }",
            fields='nextPageToken,files(appProperties)', pageSize=1000, pageToken=token).execute())
        for entry in result.get('files', []):
            run = entry.get('appProperties', {}).get('automation_run', '')
            if re.fullmatch(r'daily-\d+', run) and int(run[6:]) < int(current_run[6:]):
                previous_runs.add(run)
        token = result.get('nextPageToken')
        if not token:
            break

    cleaned = retained = 0
    for run in sorted(previous_runs):
        try:
            state = verify_run(store, client, run, channel, wait=False)
        except (RuntimeError, HttpError) as error:
            reason = f'YouTube HTTP {error.resp.status}' if isinstance(error, HttpError) else str(error)
            print(f'::warning title=Previous Drive media retained::{run}: {reason}', flush=True)
            retained += 1
            continue
        cleanup(store, run, state)
        cleaned += 1
    print(f'::notice title=Previous Drive cleanup complete::{cleaned} previous runs cleaned, '
          f'{retained} retained because upload verification was incomplete.', flush=True)


def main(cleanup_media=True, *, include_previous=False):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from workflow import Store, required

    run = required('SAVED_RUN_ID')
    if not re.fullmatch(r'daily-\d+', run):
        raise ValueError('Only daily run IDs are accepted')
    store = Store()
    client = build('youtube', 'v3', credentials=Credentials.from_authorized_user_info(
        json.loads(required('YOUTUBE_TOKEN_JSON'))), cache_discovery=False)
    channel = required('EXPECTED_CHANNEL_ID')
    state = verify_run(store, client, run, channel, repair_tags=not cleanup_media)
    if cleanup_media:
        cleanup(store, run, state)
        if include_previous:
            cleanup_previous_runs(store, client, run, channel)
    print(json.dumps({'run': run, 'video_id': state['video_id'], 'privacy': 'private', 'verified': True, 'cleanup_complete': cleanup_media}))
    return True


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    main(cleanup_media=not args.verify_only)

"""Read back the actual uploaded private video, without model-generation credentials."""
import json
import os
import time
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from workflow import Store, required

def main():
    store = Store()
    record = store.load('last-private-test.json', None)
    if not record or not record.get('passed'):
        raise RuntimeError('No completed private upload is recorded')
    client = build('youtube', 'v3', credentials=Credentials.from_authorized_user_info(
        json.loads(required('YOUTUBE_TOKEN_JSON'))), cache_discovery=False)
    expected = store.load(f"runs/{record['run']}/state.json", {})
    deadline = time.monotonic() + 600
    while True:
        items = client.videos().list(part='snippet,status,contentDetails,processingDetails',
            id=record['video_id']).execute().get('items', [])
        if not items:
            raise RuntimeError('Uploaded video could not be read back')
        video = items[0]
        if video['snippet']['channelId'] != required('EXPECTED_CHANNEL_ID'):
            raise RuntimeError('Uploaded video is on a different channel')
        if video['status']['privacyStatus'] != 'private':
            raise RuntimeError('Uploaded video is not private')
        if video['snippet']['title'] != expected['story']['title']:
            raise RuntimeError('Uploaded title differs from the generated title')
        if set(video['snippet'].get('tags', [])) != set(expected['story']['tags']):
            raise RuntimeError('Uploaded tags differ from the generated tags')
        if not video['snippet']['description'].startswith(expected['story']['description']):
            raise RuntimeError('Uploaded description differs from the generated description')
        if video['contentDetails'].get('definition') == 'hd' and video['processingDetails'].get('processingStatus') == 'succeeded':
            break
        if time.monotonic() > deadline:
            raise RuntimeError('YouTube HD processing has not completed')
        print(json.dumps({'check': 'YouTube HD readback', 'status': 'waiting'}), flush=True)
        time.sleep(20)
    report = {'private_test_passed': True, 'video_id': video['id'],
        'channel_id': video['snippet']['channelId'], 'title': video['snippet']['title'],
        'privacy': video['status']['privacyStatus'], 'definition': video['contentDetails']['definition'],
        'duration': video['contentDetails']['duration'],
        'has_custom_thumbnail': video['contentDetails'].get('hasCustomThumbnail'),
        'processing': video['processingDetails']['processingStatus'], 'metadata_verified': True}
    record['remote_verification'] = report
    store.save('last-private-test.json', record)
    store.save(f"runs/{record['run']}/youtube-verification.json", report)
    print(json.dumps(report), flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'private_test_passed': False, 'error_type': type(error).__name__,
            'http_status': getattr(getattr(error, 'resp', None), 'status', None)}), flush=True)
        raise SystemExit(1)

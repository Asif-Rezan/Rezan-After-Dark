"""Explicit English video details and recoverable English caption-track upload."""
import json
import time


def video_body(story, description, category):
    body = {'snippet': {'title':story['title'], 'description':description, 'tags':story['tags'],
        'categoryId':category, 'defaultLanguage':'en'},
        'status': {'privacyStatus':'private', 'selfDeclaredMadeForKids':False}}
    if story.get('format') == 'english-fluency':
        body['snippet']['defaultAudioLanguage'] = 'en'
        body['status'].update(license='youtube', embeddable=True, publicStatsViewable=True)
    return body


def ensure_captions(store, key, state, root):
    """List-before-insert; an ambiguous insert cannot create another track silently."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from workflow import required
    client = build('youtube','v3', credentials=Credentials.from_authorized_user_info(
        json.loads(required('YOUTUBE_TOKEN_JSON'))), cache_discovery=False)
    if not state.get('thumbnail_uploaded'):
        path = root/'thumbnail.jpg'
        if not path.exists():
            file_id=store.find(f"runs/{required('SAVED_RUN_ID')}/thumbnail.jpg")
            if not file_id:
                raise RuntimeError('Saved thumbnail missing; retain private media')
            store.download(file_id,path)
        client.thumbnails().set(videoId=state['video_id'],media_body=MediaFileUpload(str(path))).execute()
        state['thumbnail_uploaded']=True; store.save(key,state)
    name = 'English conversation'
    items = client.captions().list(part='snippet',videoId=state['video_id']).execute().get('items',[])
    matches = [item for item in items if item['snippet'].get('language')=='en' and item['snippet'].get('name')==name]
    if len(matches)>1:
        raise RuntimeError('Duplicate English caption tracks require reconciliation')
    if not matches:
        if state.get('captions_upload_started'):
            raise RuntimeError('Caption insert outcome ambiguous. Reconcile existing track before another insertion')
        path = root/'captions.srt'
        if not path.exists():
            file_id = store.find(f"runs/{required('SAVED_RUN_ID')}/captions.srt")
            if not file_id:
                raise RuntimeError('Saved English captions missing; keep video private and retain media')
            store.download(file_id,path)
        state['captions_upload_started']=True; store.save(key,state)
        result=client.captions().insert(part='snippet', body={'snippet':{
            'videoId':state['video_id'],'language':'en','name':name,'isDraft':False}},
            media_body=MediaFileUpload(str(path),mimetype='application/octet-stream')).execute()
        caption_id=result['id']
    else:
        caption_id=matches[0]['id']
    state['caption_id']=caption_id; store.save(key,state)
    deadline=time.monotonic()+600
    while True:
        items=client.captions().list(part='snippet',videoId=state['video_id'],id=caption_id).execute().get('items',[])
        if not items or items[0]['snippet'].get('status')=='failed':
            raise RuntimeError('English captions unavailable or failed processing')
        if items[0]['snippet'].get('status')=='serving':
            break
        if time.monotonic()>=deadline:
            raise RuntimeError('English captions still processing; retain private assets')
        time.sleep(10)
    state['captions_uploaded']=True; store.save(key,state)

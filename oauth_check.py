"""Read-only OAuth checks. Never print credentials or raw API error bodies."""
import json
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

failed = False

def credentials(name):
    raw = os.getenv(name, '')
    if not raw:
        raise RuntimeError('missing_secret')
    try:
        payload = json.loads(raw)
    except ValueError:
        raise RuntimeError('invalid_json') from None
    if not isinstance(payload, dict):
        raise RuntimeError('invalid_json') from None
    if not all(payload.get(k) for k in ('client_id', 'client_secret', 'refresh_token')):
        raise RuntimeError('missing_oauth_fields')
    creds = Credentials.from_authorized_user_info(payload)
    creds.refresh(Request())
    return creds

def report_error(name, error):
    global failed
    failed = True
    status = getattr(getattr(error, 'resp', None), 'status', None)
    safe = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    if safe not in ('missing_secret', 'invalid_json', 'missing_oauth_fields', 'channel_mismatch', 'scope_check_failed', 'upload_scope_missing', 'publish_scope_missing'):
        safe = type(error).__name__
    for code in ('invalid_grant', 'invalid_client', 'access_denied'):
        if code in str(error):
            safe = code
            break
    print(json.dumps({'check': name, 'passed': False, 'error': safe, 'http_status': status}))

try:
    creds = credentials('YOUTUBE_TOKEN_JSON')
    client = build('youtube', 'v3', credentials=creds, cache_discovery=False)
    channels = client.channels().list(part='id,snippet', mine=True).execute().get('items', [])
    print(json.dumps({'check': 'Authorized YouTube channels', 'channels': [
        {'id': c['id'], 'title': c['snippet']['title'], 'handle': c['snippet'].get('customUrl')} for c in channels]}), flush=True)
    expected = os.getenv('EXPECTED_CHANNEL_ID') or 'UCwNhdtzL9XZMIAMXm81UCbw'
    if expected not in [c['id'] for c in channels]:
        raise RuntimeError('channel_mismatch')
    scopes = set(creds.granted_scopes or [])
    if not scopes:
        import requests
        # Diagnostic token goes only to Google's official tokeninfo endpoint.
        # Never print the request URL, token or raw response.
        info = requests.get('https://oauth2.googleapis.com/tokeninfo',
            params={'access_token': creds.token}, timeout=30)
        if info.status_code != 200:
            raise RuntimeError('scope_check_failed')
        scopes = set(info.json().get('scope', '').split())
    if not scopes.intersection({'https://www.googleapis.com/auth/youtube.upload',
            'https://www.googleapis.com/auth/youtube', 'https://www.googleapis.com/auth/youtube.force-ssl'}):
        raise RuntimeError('upload_scope_missing')
    publish_allowed = bool(scopes.intersection({'https://www.googleapis.com/auth/youtube',
        'https://www.googleapis.com/auth/youtube.force-ssl', 'https://www.googleapis.com/auth/youtubepartner'}))
    if os.getenv('REQUIRE_PUBLIC_PUBLISH') == 'true' and not publish_allowed:
        raise RuntimeError('publish_scope_missing')
    print(json.dumps({'check': 'YouTube OAuth and expected channel', 'passed': True,
        'upload_scope': 'verified', 'publish_scope': 'verified' if publish_allowed else 'missing'}))
except Exception as error:
    report_error('YouTube OAuth', error)

try:
    creds = credentials('DRIVE_TOKEN_JSON')
    client = build('drive', 'v3', credentials=creds, cache_discovery=False)
    about = client.about().get(fields='storageQuota,user(emailAddress)').execute()
    if about.get('user', {}).get('emailAddress', '').casefold() != 'asifrezanit@gmail.com':
        raise RuntimeError('drive_account_mismatch')
    quota = about.get('storageQuota', {})
    print(json.dumps({'check': 'Drive OAuth', 'passed': True,
        'storage_limit_available': 'limit' in quota}))
    folder = os.getenv('DRIVE_FOLDER_ID')
    if not folder:
        raise RuntimeError('missing_folder_id')
    if folder:
        info = client.files().get(fileId=folder, fields='mimeType,trashed,appProperties').execute()
        permissions = client.permissions().list(fileId=folder,fields='permissions(role,type)').execute().get('permissions', [])
        private = not any(p.get('role') != 'owner' for p in permissions)
        valid = (info.get('mimeType') == 'application/vnd.google-apps.folder'
            and not info.get('trashed') and private
            and info.get('appProperties', {}).get('rezan_after_dark_root') == 'v1')
        print(json.dumps({'check': 'Drive workflow folder', 'passed': valid}))
        if not valid:
            failed = True
    else:
        print(json.dumps({'check': 'Drive workflow folder', 'configured': False}))
except Exception as error:
    report_error('Drive OAuth', error)

print(json.dumps({'check': 'Script generation backend', 'model': 'Qwen/Qwen3-4B-Instruct-2507',
                  'runtime': 'local CPU', 'api_key_required': False}))
raise SystemExit(1 if failed else 0)

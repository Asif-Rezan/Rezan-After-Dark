"""Obtain separate OAuth tokens for Rezan After Dark and its private Drive folder.

Run `python authorize_google.py youtube` or `python authorize_google.py drive` locally.
Open the printed Google authorization URL, then complete consent in the browser.
Credentials stay in git-ignored, owner-only local files; they are never printed.
"""
import json
import os
from pathlib import Path
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CHANNEL_ID = 'UCwNhdtzL9XZMIAMXm81UCbw'
SCOPES = {
    'youtube': ['https://www.googleapis.com/auth/youtube.upload',
                'https://www.googleapis.com/auth/youtube.force-ssl'],
    'drive': ['https://www.googleapis.com/auth/drive.file'],
}


def save_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SCOPES:
        raise SystemExit('Usage: python authorize_google.py youtube|drive')
    kind = sys.argv[1]
    target = Path(f'{kind}_token.json')
    if target.exists():
        raise RuntimeError('Local token already exists; refusing to overwrite it')
    client = Path('client_secrets.json')
    if not client.is_file():
        raise RuntimeError('client_secrets.json is required in this directory')
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES[kind],
        autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host='127.0.0.1', port=0, open_browser=False,
        prompt='consent', access_type='offline', timeout_seconds=900,
        login_hint='asifrezanit@gmail.com',
        authorization_prompt_message='Open this Google authorization URL:\n{url}\n',
        success_message='Authorization received. You may close this tab.')
    if not credentials.refresh_token:
        raise RuntimeError('No refresh token returned')
    granted = set(credentials.granted_scopes or credentials.scopes or [])
    if not set(SCOPES[kind]).issubset(granted):
        raise RuntimeError('Required scopes were not granted')
    if kind == 'youtube':
        youtube = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
        channels = youtube.channels().list(part='id', mine=True).execute().get('items', [])
        if CHANNEL_ID not in [channel['id'] for channel in channels]:
            raise RuntimeError('Authorized account did not select Rezan After Dark')
    else:
        drive = build('drive', 'v3', credentials=credentials, cache_discovery=False)
        user = drive.about().get(fields='user(emailAddress)').execute().get('user', {})
        if user.get('emailAddress', '').casefold() != 'asifrezanit@gmail.com':
            raise RuntimeError('Select asifrezanit@gmail.com for Drive authorization')
    save_private(target, credentials.to_json())
    print(f'{kind} authorization verified; token saved privately to {target.name}.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Authorization failed: ' + type(error).__name__ + '. No credentials were printed.')
        raise SystemExit(1)

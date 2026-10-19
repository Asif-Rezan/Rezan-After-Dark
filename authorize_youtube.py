"""Run on your own computer: Google consent, GitHub secret update, optional test."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

REPO = 'Asif-Rezan/Rezan-After-Dark'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube.force-ssl']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path, default=Path('client_secrets.json'))
    parser.add_argument('--run-test', action='store_true', help='Dispatch one daily-pipeline test after saving the secret')
    args = parser.parse_args()
    if not args.client.is_file():
        parser.error('Download your Desktop app OAuth client JSON from Google Cloud and save it as client_secrets.json')
    config = json.loads(args.client.read_text())
    if 'installed' not in config:
        parser.error('Use a Desktop app OAuth client, not a Web application client')
    if not shutil.which('gh'):
        parser.error('Install GitHub CLI (macOS: brew install gh), then run gh auth login')
    subprocess.run(['gh', 'auth', 'status'], check=True, capture_output=True)
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host='127.0.0.1', port=0, prompt='consent',
        access_type='offline', authorization_prompt_message='Approve YouTube access in the browser that opens.',
        success_message='Authorization received. You can close this tab.', timeout_seconds=300)
    if not credentials.refresh_token:
        raise RuntimeError('No refresh token returned; GitHub secret was not changed')
    if not set(SCOPES).issubset(set(credentials.granted_scopes or credentials.scopes or [])):
        raise RuntimeError('Required scopes were not approved; GitHub secret was not changed')
    youtube = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    channels = youtube.channels().list(part='id,snippet', mine=True).execute().get('items', [])
    if 'UCwNhdtzL9XZMIAMXm81UCbw' not in [c['id'] for c in channels]:
        raise RuntimeError('Select Rezan After Dark during Google authorization; GitHub secret was not changed')
    # Tokens go directly to GitHub CLI stdin, never stdout, command arguments, or disk.
    subprocess.run(['gh', 'secret', 'set', 'YOUTUBE_TOKEN_JSON', '--repo', REPO],
        input=credentials.to_json(), text=True, check=True, capture_output=True)
    print('YouTube authorization verified and YOUTUBE_TOKEN_JSON updated securely.')
    if args.run_test:
        subprocess.run(['gh', 'workflow', 'run', 'daily-upload.yml', '--ref', 'main', '--repo', REPO],
            check=True, capture_output=True)
        print('One test run requested. Daily 01:00 Asia/Dhaka schedule is unchanged.')
    print('View progress: https://github.com/' + REPO + '/actions/workflows/daily-upload.yml')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # OAuth exceptions may contain callback URLs; never print raw responses.
        print('Authorization/setup failed: ' + type(error).__name__ +
              '. Check the selected channel, client file, Google consent and gh auth login. No tokens were printed.')
        raise SystemExit(1)

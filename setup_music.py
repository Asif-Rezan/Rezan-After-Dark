"""Import a track you downloaded directly from YouTube Studio Audio Library."""
import argparse
import hashlib
from pathlib import Path
from workflow import Store
from googleapiclient.http import MediaFileUpload

parser = argparse.ArgumentParser()
parser.add_argument('file', type=Path)
parser.add_argument('--title', required=True)
parser.add_argument('--artist', required=True)
parser.add_argument('--license', required=True)
parser.add_argument('--attribution', default='')
parser.add_argument('--attribution-required', action='store_true')
args = parser.parse_args()
if not args.file.is_file():
    parser.error('Downloaded music file not found')
if args.attribution_required and not args.attribution.strip():
    parser.error('Copy the required attribution from Audio Library')
store = Store()
result = store.put('music/background.mp3',
    MediaFileUpload(str(args.file), resumable=True),
    args.file.stat().st_size)
store.save('music-manifest.json', {'source': 'YouTube Audio Library', 'title': args.title,
    'artist': args.artist, 'license': args.license, 'attribution_required': args.attribution_required,
    'attribution': args.attribution, 'sha256': hashlib.sha256(args.file.read_bytes()).hexdigest(),
    'file_id': result['id']})
print('Private music file and license manifest saved')

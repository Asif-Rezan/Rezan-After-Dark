"""Encrypt private runner work for GitHub artifact storage; no credentials in the archive."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import long_video as lv


def key():
    token = json.loads(os.environ['DRIVE_TOKEN_JSON'])['refresh_token']
    return hashlib.sha256(('rezan-after-dark-shard-backup-v1:' + token).encode()).digest()


def transfer(mode, shard, archive=Path('shard-backup.enc')):
    run = os.environ['SAVED_RUN_ID']
    if not re.fullmatch(r'daily-\d+', run) or not 0 <= shard < lv.SHARDS:
        raise ValueError('Explicit daily run and valid shard required')
    context = json.dumps({'run': run, 'shard': shard}, sort_keys=True).encode()
    base = ['story.json', 'plan.json', 'duration.json']
    if mode == 'pack':
        lv.verify_preparation(lv.ROOT, images_only=True)
        names = base + lv.shard_names(shard, json.loads((lv.ROOT/'plan.json').read_text()))
        names += [str(p.relative_to(lv.ROOT)) for p in (lv.ROOT/'.drive-upload-ids').glob('*.json')]
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name in names:
                if (lv.ROOT/name).is_file():
                    bundle.writestr(name, (lv.ROOT/name).read_bytes())
        nonce = os.urandom(12)
        archive.write_bytes(nonce + AESGCM(key()).encrypt(nonce, stream.getvalue(), context))
    elif mode == 'unpack':
        data = archive.read_bytes()
        payload = AESGCM(key()).decrypt(data[:12], data[12:], context)
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            plan = json.loads(bundle.read('plan.json'))
            allowed = set(base + lv.shard_names(shard, plan))
            for member in bundle.infolist():
                if member.filename not in allowed and not re.fullmatch(r'\.drive-upload-ids/[a-f0-9]{64}\.json', member.filename):
                    raise ValueError('Unexpected backup entry')
                target = lv.ROOT/member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(member))
        lv.verify_preparation(lv.ROOT, images_only=True)
    else:
        raise ValueError('Unknown backup operation')


if __name__ == '__main__':
    transfer(sys.argv[1], int(sys.argv[2]))

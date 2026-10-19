"""Render without Google credentials, then privately upload in a separate process."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import textwrap
from zoneinfo import ZoneInfo
import workflow as w

ROOT = Path('output/free-private-test')


def saved_run_id():
    return os.getenv('SAVED_RUN_ID') or (dt.datetime.now(ZoneInfo('Asia/Dhaka')).date().isoformat() + '-free-private-test')


def render():
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from music_cpu import generate
    ROOT.mkdir(parents=True, exist_ok=True)
    story = w.validate_story(json.loads(Path('private-test-story.json').read_text()))
    (ROOT/'story.json').write_text(json.dumps(story, indent=2))
    generate(ROOT/'music.audio')
    lengths, clips, word_lists = [], [], []
    for i, scene in enumerate(story['scenes']):
        print(json.dumps({'scene': i+1, 'of': len(story['scenes']), 'phase': 'generating'}), flush=True)
        image, audio, clip = [ROOT/f'{i:02}.{ext}' for ext in ('png', 'wav', 'mp4')]
        w.make_narration(scene['narration'], audio)
        words = w.transcribe(audio)
        w.make_image(scene['image_prompt'], image)
        word_lists.append(words)
        lengths.append(w.render_scene(image, audio, clip))
        clips.append(clip)
        print(json.dumps({'scene': i+1, 'phase': 'rendered', 'seconds': lengths[-1], 'caption_words': len(words)}), flush=True)
    if not 900 <= sum(lengths) <= 1200:
        raise RuntimeError('Narration duration outside 15–20 minutes')
    w.aligned_subtitles(word_lists, lengths, ROOT/'captions.srt')
    (ROOT/'clips.txt').write_text(''.join(f"file '{p.name}'\n" for p in clips))
    w.shell('ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(ROOT/'clips.txt'),
        '-c', 'copy', str(ROOT/'joined.mp4'))
    w.shell('ffmpeg', '-y', '-i', str(ROOT/'joined.mp4'), '-stream_loop', '-1', '-i', str(ROOT/'music.audio'),
        '-filter_complex', '[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,asplit=2[voice][side];'
        '[1:a]volume=0.09[music];[music][side]sidechaincompress=threshold=0.02:ratio=8[duck];'
        '[voice][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]',
        '-vf', f"subtitles={ROOT/'captions.srt'}:force_style='FontName=DejaVu Sans,FontSize=22,Outline=2,MarginV=35'",
        '-map', '0:v', '-map', '[a]', '-t', str(sum(lengths)), '-c:v', 'libx264', '-preset', 'fast',
        '-crf', '21', '-c:a', 'aac', '-movflags', '+faststart', str(ROOT/'video.mp4'))
    meta = w.check_video(ROOT/'video.mp4')
    source = ROOT/'thumbnail-source.png'
    w.make_image(story['thumbnail_prompt'], source)
    image = ImageOps.fit(Image.open(source).convert('RGB'), (1280, 720))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 80)
    draw.rectangle((0, 500, 1280, 720), fill=(15, 18, 24))
    draw.text((55, 540), story['thumbnail_text'].upper(), font=font, fill='white')
    image.save(ROOT/'thumbnail.jpg', quality=90)
    if (ROOT/'thumbnail.jpg').stat().st_size >= 2*1024*1024:
        raise RuntimeError('Thumbnail exceeds YouTube size limit')
    report = {'render_passed': True, 'duration_seconds': float(meta['format']['duration']),
        'width': 1920, 'height': 1080, 'scenes': len(clips), 'captions': len(word_lists),
        'voice': 'Kokoro am_michael', 'music': 'Magenta MusicRNN basic_rnn', 'privacy': 'private'}
    (ROOT/'validation.json').write_text(json.dumps(report))
    print(json.dumps(report), flush=True)


def upload():
    report = json.loads((ROOT/'validation.json').read_text())
    if not report.get('render_passed'):
        raise RuntimeError('No validated rendered video')
    w.check_video(ROOT/'video.mp4')
    store = w.Store()
    run_id = saved_run_id()
    key = f'runs/{run_id}/state.json'
    if store.load(key, {}).get('phase') in ('upload_started', 'uploaded', 'complete'):
        raise RuntimeError('Upload exists or needs reconciliation; refusing duplicate insertion')
    story = json.loads((ROOT/'story.json').read_text())
    digest = hashlib.sha256(' '.join(s['narration'] for s in story['scenes']).encode()).hexdigest()
    state = {'phase': 'rendered', 'story': story, 'hash': digest, 'validation': report}
    for name in ('video.mp4', 'thumbnail.jpg', 'captions.srt', 'story.json', 'music.audio', 'validation.json'):
        store.upload(ROOT/name, f'runs/{run_id}/{name}')
    store.save(key, state)
    w.upload_private(ROOT/'video.mp4', ROOT/'thumbnail.jpg', story, state, store, key,
        'Background music: original Magenta MusicRNN melody with synthesized ambient piano.')
    history = store.load('history.json', [])
    history.append({'title': story['title'], 'hash': digest, 'run': run_id})
    store.save('history.json', history)
    store.save('last-private-test.json', {'passed': True, 'run': run_id,
        'video_id': state['video_id'], 'processing_status': state['processing_status'], 'validation': report})
    print(json.dumps({'private_test_passed': True, 'video_id': state['video_id'],
        'url': 'https://www.youtube.com/watch?v='+state['video_id'], **report}), flush=True)



def restore():
    ROOT.mkdir(parents=True, exist_ok=True)
    store = w.Store()
    run_id = saved_run_id()
    key = f'runs/{run_id}/state.json'
    state = store.load(key, None)
    if not state or not state.get('validation', {}).get('render_passed'):
        raise RuntimeError('No validated saved render for this date')
    if state.get('phase') != 'rendered':
        raise RuntimeError('Upload state needs reconciliation; refusing a duplicate insertion')
    for name in ('video.mp4', 'thumbnail.jpg', 'captions.srt', 'story.json', 'music.audio', 'validation.json'):
        file_id = store.find(f'runs/{run_id}/{name}')
        if not file_id:
            raise RuntimeError('Saved render asset is missing')
        store.download(file_id, ROOT/name)
    w.check_video(ROOT/'video.mp4')
    print(json.dumps({'restored_render': True, 'run': run_id}), flush=True)


def resume():
    restore()
    upload()

if __name__ == '__main__':
    import sys
    try:
        {'render': render, 'upload': upload, 'resume': resume, 'restore': restore}[sys.argv[1]]()
    except Exception as error:
        # Keep API response bodies and credentials out of public Actions logs.
        print(json.dumps({'private_test_passed': False, 'error_type': type(error).__name__,
            'http_status': getattr(getattr(error, 'resp', None), 'status', None)}), flush=True)
        if sys.argv[1] == 'render':
            raise
        raise SystemExit(1)

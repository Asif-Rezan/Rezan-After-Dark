"""CPU English lesson production, animated characters and guarded private uploads."""
import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import wave

from fluency_lessons import generate_lesson, validate_lesson
from fluency_render import CharacterAnimator, lesson_frame, thumbnail

BASE = Path(__file__).resolve().parent


def run(args):
    subprocess.run([str(a) for a in args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(8*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def seconds(path):
    result = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)])
    return float(result)


def make_speech(text, speaker, output, backend):
    male = speaker == 'Alex'
    if backend == 'kokoro':
        from voice_cpu import generate
        generate(text, output, voice='am_michael' if male else 'af_heart', speed=.9, pitch=0.)
    elif backend == 'edge':
        import edge_tts
        async def speak():
            temp = output.with_suffix('.mp3')
            await edge_tts.Communicate(text, 'en-US-GuyNeural' if male else 'en-US-JennyNeural', rate='-12%').save(str(temp))
            run(['ffmpeg', '-y', '-v', 'error', '-i', temp, '-ar', '24000', '-ac', '1', output])
            temp.unlink()
        asyncio.run(speak())
    else:
        raise ValueError('Unknown speech backend')


def prepare_audio(lesson, root, backend, require_cached=False):
    """Content-addressed speech includes speaker and settings; repeat turns reuse audio."""
    root.mkdir(parents=True, exist_ok=True)
    cache = root / 'speech'
    cache.mkdir(exist_ok=True)
    events, cursor = [], 0
    combined = root / 'narration.wav'
    with wave.open(str(combined), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
        for turn in lesson['turns']:
            key = digest([turn['text'], turn['speaker'], backend, 'fluency-voice-v1', .9, 0])
            path = cache / f'{key}.wav'
            if not path.exists():
                if require_cached:
                    raise RuntimeError('Required parallel voice asset is missing; refusing synthesis in timing stage')
                temp = path.with_name(key + '-pending.wav')
                make_speech(turn['text'], turn['speaker'], temp, backend)
                with wave.open(str(temp)) as check:
                    if (check.getnchannels(), check.getsampwidth(), check.getframerate()) != (1, 2, 24000) or check.getnframes() < 2400:
                        raise ValueError('Invalid generated speech')
                temp.replace(path)
            with wave.open(str(path), 'rb') as audio:
                frames = audio.readframes(audio.getnframes())
                count = audio.getnframes()
            stream.writeframes(frames)
            duration = count / 24000
            events.append(dict(start=cursor, end=cursor+duration, turn=turn, practice=False, audio=str(path)))
            cursor += duration
            pause = round(turn.get('pause', .35) * 24000)
            if pause:
                stream.writeframes(b'\0\0' * pause)
                events.append(dict(start=cursor, end=cursor+pause/24000, turn=turn,
                                   practice=turn['section'] in ('REPEAT', 'YOUR TURN')))
                cursor += pause / 24000
    if not math.isfinite(cursor) or cursor <= 0:
        raise ValueError('Invalid lesson duration')
    write_json(root / 'timeline.json', events)
    # Captions follow exact line audio bounds, not estimated words.
    def stamp(value):
        ms = round(value*1000); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    entries = [e for e in events if 'audio' in e or e.get('practice')]
    (root/'captions.srt').write_text('\n\n'.join(f"{i+1}\n{stamp(e['start'])} --> {stamp(e['end'])}\n{e['turn']['text']}" for i,e in enumerate(entries)) + '\n')
    return events, cursor


def speech_energy(path, fps):
    import array
    with wave.open(str(path)) as source:
        values = array.array('h', source.readframes(source.getnframes()))
    step = 24000 // fps
    out = []
    for i in range(0, len(values), step):
        chunk = values[i:i+step]
        rms = math.sqrt(sum(v*v for v in chunk)/max(1,len(chunk))) / 32768
        out.append(min(1., rms * 18))
    # Short smoothing window reduces abrupt jaw chatter between waveform blocks.
    return [sum(out[max(0,i-1):i+2])/len(out[max(0,i-1):i+2]) for i in range(len(out))]


def frame_ranges(frame_count, parts=4):
    if not isinstance(parts,int) or not 1 <= parts <= 16 or frame_count < parts:
        raise ValueError('Invalid render partition')
    return [(frame_count*i//parts, frame_count*(i+1)//parts) for i in range(parts)]


def frame_at(lesson, animator, events, energy, total, fps, frame_index, event_index=0):
    t = frame_index/fps
    while event_index < len(events)-1 and t >= events[event_index]['end']:
        event_index += 1
    e = events[event_index]
    amplitude = energy[min(frame_index,len(energy)-1)] if 'audio' in e and energy else 0.
    speaker = e['turn']['speaker'] if 'audio' in e else None
    bg = animator.frame(t,speaker,amplitude)
    return lesson_frame(bg,lesson,e['turn'],t/total,e.get('practice',False)),event_index


def render_frames(lesson, root, events, total, fps, start_frame, end_frame, output, with_audio=False):
    if not 0 <= start_frame < end_frame <= math.ceil(total*fps):
        raise ValueError('Invalid global frame range')
    print(json.dumps({'stage':'render','start_frame':start_frame,'end_frame':end_frame}),flush=True)
    animator = CharacterAnimator()
    energy = speech_energy(root/'narration.wav',fps)
    command = ['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1280x720',
               '-r',str(fps),'-i','-']
    if with_audio:
        command += ['-i',str(root/'narration.wav'),'-map','0:v','-map','1:a']
    command += ['-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p']
    if with_audio:
        command += ['-c:a','aac','-b:a','192k','-af','loudnorm=I=-16:TP=-1.5:LRA=7','-t',str(total)]
    else:
        command += ['-an']
    command += ['-movflags','+faststart',str(output)]
    with (root/f'{output.stem}.log').open('wb') as log:
        process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log)
        event_index=0
        try:
            for frame_index in range(start_frame,end_frame):
                im,event_index=frame_at(lesson,animator,events,energy,total,fps,frame_index,event_index)
                if frame_index == min(start_frame+12,end_frame-1):
                    im.save(root/f'{output.stem}-preview.jpg')
                process.stdin.write(im.tobytes())
                if (frame_index-start_frame+1)%(fps*30)==0:
                    print(json.dumps({'stage':'render','frames_done':frame_index-start_frame+1,'frames_total':end_frame-start_frame}),flush=True)
            process.stdin.close()
            if process.wait()!=0:
                raise RuntimeError('FFmpeg rendering failed; inspect local renderer log')
        except BaseException:
            process.kill();process.wait();output.unlink(missing_ok=True)
            raise


def write_validation(lesson,root,total,backend,fps):
    actual=seconds(root/'video.mp4')
    if abs(actual-total)>.2:
        raise ValueError('Final video duration does not match dialogue timing')
    write_json(root/'lesson.json',lesson)
    write_json(root/'validation.json',dict(duration_seconds=actual,format='english-fluency',fps=fps,
        animated=True,animation='Python Pillow audio-reactive mouth shapes, breathing, sway and nods; not phoneme lip-sync',
        voices={'Alex':'am_michael' if backend=='kokoro' else 'en-US-GuyNeural',
                'Emma':'af_heart' if backend=='kokoro' else 'en-US-JennyNeural'},
        lesson_hash=digest(lesson),backend=backend,private=True,
        asset_hashes={name:file_hash(root/name) for name in ('video.mp4','thumbnail.jpg','captions.srt')}))
    return actual


def render(lesson, root, backend='kokoro', sample=False, fps=12):
    validate_lesson(lesson)
    if fps not in (12,24):
        raise ValueError('Animation frame rate must be 12 or 24')
    events,total=prepare_audio(lesson,root,backend)
    lower,upper=(60,90) if sample else (480,1200)
    if not lower<=total<=upper:
        raise ValueError(f'Measured narration {total:.1f}s outside {lower}–{upper}s; revise lesson before rendering/upload')
    thumbnail(lesson,root/'thumbnail.jpg')
    pending=root/'video.pending.mp4'
    render_frames(lesson,root,events,total,fps,0,math.ceil(total*fps),pending,with_audio=True)
    pending.replace(root/'video.mp4')
    preview=root/'video.pending-preview.jpg'
    if preview.exists(): preview.replace(root/'preview.jpg')
    return write_validation(lesson,root,total,backend,fps)


def daily(root, resume_upload=False):
    """Recover from saved lesson/audio; never regenerate or reinsert an ambiguous upload."""
    if not resume_upload and os.getenv('DAILY_ENABLED') != 'true':
        raise RuntimeError('Daily lesson production is disabled; set DAILY_ENABLED=true explicitly')
    from workflow import Store, required, upload_private
    from daily_publish import main as verify_cleanup
    run_id = required('SAVED_RUN_ID')
    if not re.fullmatch(r'daily-\d+', run_id):
        raise ValueError('Expected daily-<GitHub run ID>')
    store = Store(); key = f'runs/{run_id}/state.json'
    state = store.load(key, {})
    if state.get('phase') in ('uploaded', 'complete', 'verified_private'):
        if state.get('format') != 'english-fluency':
            raise RuntimeError('Legacy upload: use cleanup_run for the original workflow')
        root.mkdir(parents=True, exist_ok=True)
        from fluency_upload import ensure_captions
        ensure_captions(store, key, state, root)
        verify_cleanup()
        record_history(store, run_id, state['story'])
        return
    if state.get('phase') == 'upload_started':
        raise RuntimeError('Upload outcome ambiguous. Reconcile existing YouTube video before retrying; insertion refused')
    root.mkdir(parents=True, exist_ok=True)
    saved = store.load(f'runs/{run_id}/lesson.json', None)
    if resume_upload and saved is None:
        raise RuntimeError('No saved fluency lesson for this run')
    if saved is None:
        import datetime as dt
        from zoneinfo import ZoneInfo
        lesson = generate_lesson(dt.datetime.now(ZoneInfo('Asia/Dhaka')).date().isoformat(), store.load('fluency-history.json', []))
        store.save(f'runs/{run_id}/lesson.json', lesson)
    else:
        lesson = validate_lesson(saved)
    if resume_upload:
        for name in ('video.mp4', 'thumbnail.jpg', 'captions.srt', 'validation.json'):
            fid = store.find(f'runs/{run_id}/{name}')
            if not fid:
                raise RuntimeError(f'Missing completed asset {name}; refusing regeneration in upload-only recovery')
            store.download(fid, root/name)
        report = json.loads((root/'validation.json').read_text())
        if any(report.get('asset_hashes',{}).get(name) != file_hash(root/name)
               for name in ('video.mp4','thumbnail.jpg','captions.srt')):
            raise RuntimeError('Completed asset hash mismatch; refusing upload')
        if report.get('lesson_hash') != digest(lesson) or not report.get('animated'):
            raise RuntimeError('Saved render does not match lesson')
    else:
        render(lesson, root)
        for name in ('video.mp4', 'thumbnail.jpg', 'narration.wav', 'captions.srt', 'validation.json', 'timeline.json'):
            store.upload(root/name, f'runs/{run_id}/{name}')
    state = dict(phase='prepared', story=lesson, hash=digest(lesson), format='english-fluency')
    store.save(key, state)
    upload_private(root/'video.mp4', root/'thumbnail.jpg', lesson, state, store, key,
                   'Original English learning dialogue. Alex and Emma are illustrated fictional presenters. Synthetic narration; no background music.', category='27')
    from fluency_upload import ensure_captions
    ensure_captions(store, key, state, root)
    verify_cleanup()
    record_history(store, run_id, lesson)


def record_history(store, run_id, lesson):
    history = store.load('fluency-history.json', [])
    if not any(entry.get('run') == run_id for entry in history):
        history.append(dict(run=run_id, topic_id=lesson['topic_id'], title=lesson['title'], headline=lesson['headline'], hash=digest(lesson)))
        store.save('fluency-history.json', history)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sample', 'render', 'generate', 'daily', 'resume-upload'])
    parser.add_argument('--lesson', type=Path)
    parser.add_argument('--output', type=Path, default=Path('output/english-fluency'))
    parser.add_argument('--backend', choices=['kokoro', 'edge'], default='kokoro')
    parser.add_argument('--fps', type=int, choices=[12,24], default=12)
    args = parser.parse_args()
    if args.command in ('daily', 'resume-upload'):
        daily(args.output, args.command=='resume-upload'); return
    if args.command == 'generate':
        import datetime as dt
        args.output.mkdir(parents=True, exist_ok=True)
        write_json(args.output/'lesson.json', generate_lesson(dt.date.today().isoformat(), [])); return
    path = args.lesson or BASE/'examples'/'ordering-coffee.json'
    lesson = validate_lesson(json.loads(path.read_text()))
    actual = render(lesson, args.output, args.backend, args.command=='sample', args.fps)
    print(json.dumps({'duration_seconds':actual, 'output':str(args.output/'video.mp4'), 'animated':True}))


if __name__ == '__main__':
    main()

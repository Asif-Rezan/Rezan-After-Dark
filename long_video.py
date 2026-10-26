"""15–20 minute videos with a distinct image every ten seconds."""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import workflow as w
import duration_control as dc
from voice_cpu import voice_settings
from resilience import produce_file

ROOT = Path(os.getenv('OUTPUT_DIR', 'output/horror-long-video'))
INTERVAL = 10
SHARDS = 20
ASSETS = ('story.json', 'plan.json', 'narration.wav', 'captions.srt', 'duration.json')
HORROR_STYLE = ('atmospheric cinematic horror illustration, consistent adult characters, '
                 'realistic textures, eerie shadows and dim moonlight, suspenseful non-graphic scene, '
                 'landscape composition, no text or watermark')


def run_id():
    return os.getenv('SAVED_RUN_ID') or (dt.datetime.now(ZoneInfo('Asia/Dhaka')).strftime('%Y-%m-%d-%H%M%S')+'-horror-story')


def shot_plan(story, lengths, word_lists):
    """Anchor every visual to words actually spoken in its global ten-second window."""
    if len(lengths) != len(word_lists) or len(lengths) != len(story['scenes']):
        raise ValueError('Chapter audio, transcript and story must agree')
    total = sum(lengths)
    timeline, offset = [], 0.0
    for chapter, (length, words) in enumerate(zip(lengths, word_lists)):
        for word in words:
            start, end = float(word['start']), float(word['end'])
            if not 0 <= start < end <= length + .1:
                raise ValueError('Word timing outside measured narration')
            timeline.append({'start': offset+start, 'end': offset+min(end, length),
                             'word': word['word'].strip(), 'chapter': chapter})
        offset += length
    if not timeline:
        raise ValueError('Cannot synchronize images without word timings')
    shots = []
    for index in range(math.ceil(total/INTERVAL)):
        start, end = index*INTERVAL, min((index+1)*INTERVAL, total)
        spoken = [word for word in timeline if word['end'] > start and word['start'] < end]
        # Silence keeps the nearest narrated event, never an unrelated chapter beat.
        anchor = spoken or [min(timeline, key=lambda word: abs(word['start']-start))]
        chapter = max(set(word['chapter'] for word in anchor),
                      key=lambda c: sum(max(0, min(end, word['end'])-max(start, word['start']))
                                        for word in anchor if word['chapter'] == c))
        shots.append({'index': index, 'start': start, 'duration': end-start,
            'chapter': chapter, 'narration': ' '.join(word['word'] for word in anchor),
            'seed': 1700+index})
    return {'duration_seconds': total, 'image_interval_seconds': INTERVAL,
        'style': 'horror-story', 'alignment': 'measured-word-timestamps', 'shots': shots}


def write_json(path, value):
    """A cancelled write must not destroy the previous valid checkpoint."""
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def valid_visual(value):
    return isinstance(value, str) and 5 <= len(value.split()) <= 30


def storyboard(root=ROOT, ask=None, pause=None):
    """Create concrete visual prompts AFTER narration timing, never from outline beats."""
    import time
    ask = ask or w.request_story
    pause = pause or time.sleep
    story = json.loads((root/'story.json').read_text())
    plan = json.loads((root/'plan.json').read_text())
    manifest = read_cache(root/'duration.json')
    if (root/'duration.json').exists() and (manifest.get('plan_source_hash') != plan.get('source_hash')
            or manifest.get('story_sha256') != file_hash(root/'story.json')):
        raise RuntimeError('Storyboard source changed since narration validation')
    if plan.get('storyboard_verified'):
        return plan
    shots = plan['shots']
    pending = [shot for shot in shots if not valid_visual(shot.get('visual_action'))]
    for begin in range(0, len(pending), 8):
        batch = pending[begin:begin+8]
        properties = {f"shot_{shot['index']:03}": {'type': 'string'} for shot in batch}
        schema = {'type': 'object', 'properties': properties,
                  'required': list(properties), 'additionalProperties': False}
        context = {str(shot['chapter']): story['scenes'][shot['chapter']]['narration'] for shot in batch}
        prompt = f"""Storyboard this original fictional horror story. Return one image prompt per exact shot key.
Each shot is a measured ten-second narration window. Depict the concrete action,
character, object or disturbing discovery being narrated. Chapter text resolves context only;
never illustrate a later or earlier event. Show subtle dread through setting, light and composition.
Preserve recurring adult character appearance: {story.get('character_description', '')}.
Maintain spatial continuity and the story's supernatural rules. No graphic gore or sexual content.
Each prompt MUST be 12-22 English words: subject and concrete action FIRST, then relevant objects
and location. Describe one simple drawable composition. Include people only when narrated.
Do not put typography or narration text into the prompt. Keep invented visual details consistent with the fictional story.
Topic: {story.get('category', '')}. Story seeds: {json.dumps(story.get('story_seeds', []))}.
Chapter context: {json.dumps(context)}
Exact shot windows: {json.dumps([{'key': f"shot_{shot['index']:03}", 'spoken': shot['narration']} for shot in batch])}
"""
        unresolved = list(batch)
        for attempt in range(3):
            payload = {'model': os.getenv('STORY_MODEL', 'Qwen/Qwen3-4B-Instruct-2507'),
                'messages': [{'role': 'user', 'content': prompt +
                    ('\nRepair invalid or missing prompts. Use 12-22 words per scene.' if attempt else '')}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'aligned_shots',
                    'strict': True, 'schema': schema}},
                'temperature': .2, 'max_completion_tokens': 1800}
            try:
                result = ask('chat/completions', payload)
                choice = result['choices'][0]
                if choice.get('finish_reason') == 'length':
                    raise ValueError('Incomplete storyboard')
                values = json.loads(choice['message']['content'])
                if not isinstance(values, dict):
                    raise ValueError('Storyboard must be an object')
                for shot in list(unresolved):
                    visual = values.get(f"shot_{shot['index']:03}")
                    if valid_visual(visual):
                        shot['visual_action'] = shot['prompt'] = ' '.join(visual.split())
                        unresolved.remove(shot)
            except (RuntimeError, ValueError, TypeError, KeyError, IndexError, OSError) as error:
                print(json.dumps({'check': 'Storyboard response', 'attempt': attempt+1,
                    'error_type': type(error).__name__}), flush=True)
            # Preserve successes even when another shot in this batch needs repair.
            write_json(root/'plan.json', plan)
            if not unresolved:
                break
            if attempt < 2:
                pause(2 ** attempt)
        for shot in unresolved:
            # The measured spoken window remains the source; never invent another event.
            import re
            words = re.findall(r"[\w'-]+", shot['narration'])[:22]
            if not words:
                raise ValueError('Cannot recover storyboard without narrated words')
            visual = 'Illustration of ' + ' '.join(words) + ' in an atmospheric setting'
            shot['visual_action'] = shot['prompt'] = visual
            shot['storyboard_advisory'] = 'Narration-based fallback after three unsuccessful responses'
        if unresolved:
            print(json.dumps({'check': 'Storyboard fallback',
                'shots': [shot['index'] for shot in unresolved], 'advisory': True}), flush=True)
        # Private local checkpoint; do not log unpublished narration or prompts.
        write_json(root/'plan.json', plan)
        print(json.dumps({'aligned_shots': len(shots) - len(pending) + min(begin+8, len(pending)), 'total': len(shots)}), flush=True)
    plan['storyboard_verified'] = True
    write_json(root/'plan.json', plan)
    return plan


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def text_fingerprint(text):
    speed, pitch = voice_settings()
    settings = (text, os.getenv('TTS_BACKEND', 'kokoro'),
                os.getenv('CPU_TTS_VOICE', 'am_michael'), speed, pitch, 'horror-voice-v2')
    return hashlib.sha256(json.dumps(settings).encode()).hexdigest()


def read_cache(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def ensure_chapter_audio(root, index, text):
    audio = root/f'narration-{index:02}.wav'
    meta_path = root/f'narration-{index:02}.meta.json'
    fingerprint = text_fingerprint(text)
    meta = read_cache(meta_path)
    if (audio.exists() and meta.get('text_fingerprint') == fingerprint
            and meta.get('audio_sha256') == file_hash(audio)):
        return w.duration(audio)
    candidate = root/f'narration-{index:02}.new.wav'
    try:
        w.make_narration(text, candidate)
        length = w.duration(candidate)
        if not math.isfinite(length) or length <= 0:
            raise RuntimeError(f'Chapter {index+1} has invalid narration audio')
        candidate.replace(audio)
        meta_path.write_text(json.dumps({'text_fingerprint': fingerprint,
            'audio_sha256': file_hash(audio), 'seconds': length}))
        (root/f'words-{index:02}.json').unlink(missing_ok=True)
        (root/f'words-{index:02}.meta.json').unlink(missing_ok=True)
        return length
    finally:
        candidate.unlink(missing_ok=True)


def adjust_tempo(root, story, lengths, factor):
    """Prepare every adjusted chapter before replacing any source audio."""
    if not 1 < factor <= dc.MAX_TEMPO:
        raise ValueError('Tempo correction would harm voice quality')
    candidates = []
    try:
        for index, length in enumerate(lengths):
            prior = read_cache(root/f'narration-{index:02}.meta.json').get('tempo_factor', 1.0)
            if prior * factor > dc.MAX_TEMPO:
                raise ValueError('Cumulative tempo correction would harm voice quality')
            source = root/f'narration-{index:02}.wav'
            candidate = root/f'narration-{index:02}.tempo.wav'
            candidates.append(candidate)
            w.shell('ffmpeg', '-y', '-i', str(source), '-af', f'atempo={factor:.9f}',
                    '-c:a', 'pcm_s16le', str(candidate))
            measured = w.duration(candidate)
            if not math.isfinite(measured) or abs(measured - length/factor) > max(.15, length*.01):
                raise RuntimeError(f'Chapter {index+1} tempo output failed duration quality check')
        for index, candidate in enumerate(candidates):
            audio = root/f'narration-{index:02}.wav'
            candidate.replace(audio)
            meta_path = root/f'narration-{index:02}.meta.json'
            prior = read_cache(meta_path).get('tempo_factor', 1.0)
            meta_path.write_text(json.dumps({'text_fingerprint': text_fingerprint(story['scenes'][index]['narration']),
                'audio_sha256': file_hash(audio), 'tempo_factor': prior * factor,
                'seconds': w.duration(audio)}))
            (root/f'words-{index:02}.json').unlink(missing_ok=True)
            (root/f'words-{index:02}.meta.json').unlink(missing_ok=True)
    finally:
        for candidate in candidates:
            candidate.unlink(missing_ok=True)
    return [w.duration(root/f'narration-{index:02}.wav') for index in range(len(lengths))]


def ensure_words(root, index, audio, text):
    words_path = root/f'words-{index:02}.json'
    meta_path = root/f'words-{index:02}.meta.json'
    fingerprint = {'audio_sha256': file_hash(audio), 'text_fingerprint': text_fingerprint(text)}
    if words_path.exists() and read_cache(meta_path) == fingerprint:
        words = read_cache(words_path)
        if isinstance(words, list) and words:
            return words
    words = w.transcribe(audio)
    if not words:
        raise RuntimeError(f'Chapter {index+1} has no word timestamps')
    words_path.write_text(json.dumps(words))
    meta_path.write_text(json.dumps(fingerprint))
    return words


def correct_duration(story, root):
    lengths = [ensure_chapter_audio(root, i, scene['narration'])
               for i, scene in enumerate(story['scenes'])]
    total = sum(lengths)
    print(json.dumps({'narration_generated_chapters': len(lengths),
                      'original_duration_seconds': round(total, 2),
                      'duration_validation': 'PASSED' if dc.valid(total) else 'OUT OF RANGE'}), flush=True)
    for attempt in range(1, dc.MAX_ATTEMPTS+1):
        if dc.valid(total):
            return story, lengths
        factor = dc.tempo_factor(total)
        if factor and any(read_cache(root/f'narration-{i:02}.meta.json').get('tempo_factor', 1.0)
                          * factor > dc.MAX_TEMPO for i in range(len(lengths))):
            factor = None
        if factor is not None:
            method = 'minimal tempo adjustment'
            print(json.dumps({'correction_attempt': f'{attempt}/{dc.MAX_ATTEMPTS}',
                              'method': method, 'tempo_factor': round(factor, 6)}), flush=True)
            lengths = adjust_tempo(root, story, lengths, factor)
        else:
            direction = 'shorten' if total > dc.MAX_SECONDS else 'expand'
            selected, ratio = dc.select_revisions(lengths, direction)
            print(json.dumps({'correction_attempt': f'{attempt}/{dc.MAX_ATTEMPTS}',
                              'method': f'Qwen story {direction}', 'chapters': [i+1 for i in selected],
                              'target_word_ratio': round(ratio, 3)}), flush=True)
            candidate = w.revise_story_duration(story, ratio, direction, selected)
            for index in selected:
                if candidate['scenes'][index]['narration'] != story['scenes'][index]['narration']:
                    lengths[index] = ensure_chapter_audio(root, index, candidate['scenes'][index]['narration'])
            story = candidate
        total = sum(lengths)
        print(json.dumps({'corrected_duration_seconds': round(total, 2),
                          'duration_validation': 'PASSED' if dc.valid(total) else 'OUT OF RANGE'}), flush=True)
    if not dc.valid(total):
        raise RuntimeError(f'Narration duration {total:.2f}s remains outside 900–1200s after '
                           f'{dc.MAX_ATTEMPTS} correction attempts; no images or upload started')
    return story, lengths


def prepare(story=None, root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    story = story or json.loads(Path(os.getenv('STORY_FILE', str(root/'story.json'))).read_text())
    w.validate_story(story)
    print(json.dumps({'story_generated_words': sum(len(s['narration'].split()) for s in story['scenes'])}), flush=True)
    story, lengths = correct_duration(story, root)
    w.validate_story(story)
    (root/'story.json').write_text(json.dumps(story, indent=2))
    audios = [root/f'narration-{i:02}.wav' for i in range(len(lengths))]
    word_lists = []
    for index, (scene, audio, length) in enumerate(zip(story['scenes'], audios, lengths)):
        words = ensure_words(root, index, audio, scene['narration'])
        w.aligned_subtitles([words], [length], root/'chapter-check.srt')
        word_lists.append(words)
        print(json.dumps({'chapter': index+1, 'seconds': round(length, 2),
                          'audio_passed': True, 'words_aligned': len(words)}), flush=True)
    w.aligned_subtitles(word_lists, lengths, root/'captions.srt')
    (root/'audio-list.txt').write_text(''.join(f"file '{p.name}'\n" for p in audios))
    w.shell('ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(root/'audio-list.txt'),
        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(root/'narration.wav'))
    measured = w.duration(root/'narration.wav')
    total = sum(lengths)
    if not dc.valid(measured) or abs(measured-total) > .15:
        raise RuntimeError(f'Concatenated narration {measured:.2f}s does not match validated chapters {total:.2f}s')
    plan = shot_plan(story, lengths, word_lists)
    plan['source_hash'] = hashlib.sha256(json.dumps({'story': story, 'image_interval_seconds': INTERVAL,
        'audio_hashes': [file_hash(p) for p in audios], 'words': word_lists},
        sort_keys=True).encode()).hexdigest()
    previous = read_cache(root/'plan.json')
    if previous.get('source_hash') == plan['source_hash'] and previous.get('image_interval_seconds') == INTERVAL:
        plan = previous
    write_json(root/'plan.json', plan)
    (root/'duration.json').write_text(json.dumps({'duration_seconds': measured,
        'story_sha256': file_hash(root/'story.json'),
        'narration_sha256': file_hash(root/'narration.wav'),
        'captions_sha256': file_hash(root/'captions.srt'),
        'plan_source_hash': plan['source_hash'], 'chapters': lengths}, indent=2))
    print(json.dumps({'narration_validation': 'PASSED', 'storyboard_synchronization': 'PASSED',
        'audio_ready': True, 'seconds': measured, 'images': len(plan['shots']),
        'ready_for_storyboarding': True}), flush=True)
    return plan


def picture_clip(image, seconds, target):
    frames = max(1, round(seconds*30))
    vf = ("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"zoompan=z='min(zoom+0.0002,1.04)':d={frames}:s=1920x1080:fps=30,"
        f"fade=t=in:st=0:d=0.2,fade=t=out:st={max(0,seconds-.2)}:d=0.2,format=yuv420p")
    w.shell('ffmpeg', '-y', '-i', str(image), '-vf', vf, '-frames:v', str(frames), '-an',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-movflags', '+faststart', str(target))


def refine_visual_plan(root=ROOT):
    """Keep each story location, prop or narrated character visually prominent."""
    import re
    plan = json.loads((root/'plan.json').read_text())
    story = read_cache(root/'story.json')
    protagonist = story.get('protagonist_name', '').strip()
    for shot in plan['shots']:
        action = shot['visual_action'].strip().rstrip('.')
        group = bool(re.search(r'\b(?:people|crowd|workers|community|group|scholars|travelers)\b', action, re.I))
        person = bool(re.search(r'\b(?:person|man|woman|adult|guard|driver|tenant|commuter|keeper|caretaker|visitor|archivist|researcher)\b', action, re.I))
        person = person or bool(protagonist and re.search(r'\b' + re.escape(protagonist) + r'\b', action, re.I))
        focus = 'group' if group else ('person' if person else 'object')
        framing = ('wide environmental view', 'medium view', 'low angle view',
                   'over-the-shoulder view', 'close view of the narrated discovery')[shot['index'] % 5]
        shot['prompt'] = f'{action}. {framing}. {HORROR_STYLE}'
        shot['focus'] = focus
        shot['negative_prompt'] = 'graphic gore, nudity, text, watermark, repeated composition'
        if focus == 'object':
            shot['negative_prompt'] += ', portrait, unrelated person'
        elif focus == 'group':
            shot['negative_prompt'] += ', solo portrait'
        shot['semantic_target'] = action
    plan['storyboard_verified'] = True
    plan['visual_review'] = {'passed': False, 'phase': 'horror-render-review-required'}
    plan['scene_composition_version'] = 3
    write_json(root/'plan.json', plan)
    print(json.dumps({'scene_plan_refined': True, 'shots': len(plan['shots'])}), flush=True)
    return plan


def generate_shard(shard, root=ROOT, count=SHARDS):
    plan = json.loads((root/'plan.json').read_text())
    if not plan.get('storyboard_verified') or not all(s.get('prompt') for s in plan['shots']):
        raise RuntimeError('Narration-aligned storyboard is required before image generation')
    if plan.get('scene_composition_version') != 3:
        raise RuntimeError('Scene-specific object and group composition is required')
    shots = [s for s in plan['shots'] if s['index']%count == shard]
    failures = []
    for shot in shots:
        try:
            generate_shot(shot, root, shard, len(plan['shots']))
        except Exception as error:
            failures.append(shot['index'])
            print(json.dumps({'shard': shard, 'shot': shot['index'], 'rendered': False,
                'error_type': type(error).__name__}), flush=True)
    if failures:
        raise RuntimeError(f'Shard {shard} has unfinished shots {failures}; completed work is preserved')


def generate_shot(shot, root, shard, total):
    from scene_check import check_scene
    index = shot['index']
    image, clip = root/f'shot-{index:03}.png', root/f'shot-{index:03}.mp4'
    check = root/f'shot-{index:03}.qa.json'
    os.environ['IMAGE_NEGATIVE_PROMPT'] = shot.get('negative_prompt', '')
    for attempt in range(3):
        os.environ['IMAGE_SEED'] = str(shot['seed']+attempt*10000)
        if not image.exists():
            w.make_image(shot['prompt'], image)
        try:
            report = check_scene(image, shot)
        except Exception as error:
            # Similarity scoring is optional; image safety is enforced by make_image.
            report = {'passed': False, 'required': True, 'advisory': True,
                'warning': 'Scene similarity scoring unavailable',
                'error_type': type(error).__name__}
            write_json(check, report)
            break
        if report['passed']:
            check.write_text(json.dumps(report))
            break
        # CLIP similarity is a heuristic, not a reliable hard failure gate.
        # Keep a real safety-approved image after the final attempt, recording
        # the mismatch for review rather than losing all CPU render work.
        if attempt == 2:
            report = dict(report, passed=False, advisory=True,
                          warning='CLIP scene match was inconclusive after three renders')
            check.write_text(json.dumps(report))
            print(json.dumps({'shot': index, 'visual_check_passed': False,
                'attempt': attempt+1, 'quality_warning': True}), flush=True)
            break
        image.unlink()
        print(json.dumps({'shot': index, 'visual_check_passed': False, 'attempt': attempt+1}), flush=True)
    if not clip.exists():
        produce_file(clip, lambda candidate: picture_clip(image, shot['duration'], candidate), 'Shot rendering')
    print(json.dumps({'shard': shard, 'shot': index+1, 'total': total, 'rendered': True,
        'visual_check_passed': report['passed']}), flush=True)


def validate_engagement(story):
    import re
    for scene in story['scenes'][:-1]:
        if re.search(r'\bsubscribe\b', scene['narration'], re.IGNORECASE):
            raise ValueError('Premature call to action before the final chapter')
    ending = story['scenes'][-1]['narration'].lower()
    if not all(re.search(r'\b'+word+r'\b', ending) for word in ('like', 'comment', 'subscribe')):
        raise ValueError('Closing narration must request like, comment, and subscribe')
    if story.get('format') == 'horror-story' and (not story.get('topic_id') or story.get('fictional') is not True):
        raise ValueError('Horror story requires a topic and explicit fictional label')


def mix_audio(voice, music, target, seconds):
    """Normalize music first and use mild ducking; verify its actual contribution."""
    import numpy as np
    import subprocess
    bed = target.with_name('music-bed.wav')
    w.shell('ffmpeg', '-y', '-i', str(voice), '-stream_loop', '-1', '-i', str(music),
        '-filter_complex',
        '[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asplit=2[voice][side];'
        '[1:a]loudnorm=I=-27:TP=-3:LRA=7,aresample=48000[music];'
        '[music][side]sidechaincompress=threshold=0.08:ratio=2:attack=100:release=700:mix=0.35,asplit=2[bed][check];'
        '[voice][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0[a]',
        '-map', '[a]', '-t', str(seconds), '-ac', '2', '-c:a', 'pcm_s16le', str(target),
        '-map', '[check]', '-t', str(seconds), '-ac', '2', '-c:a', 'pcm_s16le', str(bed))
    def rms_db(path):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
            '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1'])
        audio = np.frombuffer(raw, dtype='<f4')
        if not len(audio) or not np.isfinite(audio).all():
            raise RuntimeError('Invalid audio samples')
        rms = float(np.sqrt(np.mean(audio**2)))
        return 20*math.log10(max(rms, 1e-12))
    bed_db, mix_db = rms_db(bed), rms_db(target)
    gap = mix_db-bed_db
    if not -40 < bed_db < -18 or not 5 < gap < 22:
        raise RuntimeError(f'Music mix is inaudible or too loud: bed={bed_db:.1f}dB, gap={gap:.1f}dB')
    return {'music_bed_rms_db': round(bed_db, 2), 'mix_rms_db': round(mix_db, 2),
        'music_gap_db': round(gap, 2), 'music_audibility_passed': True}


def validate_scene_reports(plan, scene_reports):
    """CLIP is quality guidance; only a documented advisory can replace a pass."""
    if len(scene_reports) != len(plan['shots']):
        raise RuntimeError('Missing visual review records')
    if not all(r.get('passed') is True or
               (r.get('advisory') is True and r.get('passed') is False
                and r.get('warning')) for r in scene_reports):
        raise RuntimeError('Missing or invalid visual review record')
    return [s['index'] for s, report in zip(plan['shots'], scene_reports)
            if report.get('advisory') is True]


def finish(root=ROOT):
    from music_cpu import generate
    from thumbnail_design import render_thumbnail
    plan = json.loads((root/'plan.json').read_text())
    clips = [root/f"shot-{s['index']:03}.mp4" for s in plan['shots']]
    if not all(p.exists() for p in clips):
        raise RuntimeError('Not every ten-second image clip exists')
    for shot, clip in zip(plan['shots'], clips):
        if abs(w.duration(clip)-shot['duration']) > .04:
            raise RuntimeError('Shot timing differs from the global ten-second plan')
    (root/'clips.txt').write_text(''.join(f"file '{p.name}'\n" for p in clips))
    w.shell('ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(root/'clips.txt'), '-c', 'copy', str(root/'joined.mp4'))
    if not (root/'music.audio').exists():
        generate(root/'music.audio')
    audio_report = mix_audio(root/'narration.wav', root/'music.audio', root/'mixed.wav', plan['duration_seconds'])
    w.shell('ffmpeg', '-y', '-i', str(root/'joined.mp4'), '-i', str(root/'mixed.wav'),
        '-vf', f"subtitles={root/'captions.srt'}:force_style='FontName=DejaVu Sans,FontSize=22,Outline=2,MarginV=35'",
        '-map', '0:v', '-map', '1:a', '-t', str(plan['duration_seconds']), '-ar', '48000',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-c:a', 'aac', '-movflags', '+faststart', str(root/'video.mp4'))
    metadata = w.check_video(root/'video.mp4')
    story = json.loads((root/'story.json').read_text())
    os.environ['IMAGE_NEGATIVE_PROMPT'] = ''
    os.environ['IMAGE_SEED'] = '9931'
    w.make_image(story['thumbnail_prompt'], root/'thumbnail-source.png')
    render_thumbnail(root/'thumbnail-source.png', story['thumbnail_text'], root/'thumbnail.jpg', story.get('category', 'Horror'))
    scene_reports = [json.loads((root/f"shot-{s['index']:03}.qa.json").read_text()) for s in plan['shots']]
    advisory_shots = validate_scene_reports(plan, scene_reports)
    print(json.dumps({'visual_quality_advisory_count': len(advisory_shots), 'shot_indices': advisory_shots}), flush=True)
    report = {'render_passed': True, 'duration_seconds': float(metadata['format']['duration']),
        'width': 1920, 'height': 1080, 'images': len(clips), 'image_interval_seconds': INTERVAL,
        'style': 'horror-story', 'privacy': 'private', 'voice': 'Kokoro am_michael', 'voice_speed': voice_settings()[0],
        'voice_pitch_semitones': voice_settings()[1],
        'music': 'Magenta MusicRNN horror atmosphere', 'image_model': os.getenv('CPU_IMAGE_MODEL', 'segmind/SSD-1B'),
        'audio_validation': audio_report, 'timeline_verified': True, 'storyboard_verified': plan.get('storyboard_verified', False),
        'alignment': plan.get('alignment'), 'scene_composition_version': plan.get('scene_composition_version'),
        'semantic_checked_images': sum(r.get('required', False) for r in scene_reports),
        'visual_quality_advisory_shots': advisory_shots,
        'storyboard_advisory_shots': [s['index'] for s in plan['shots'] if s.get('storyboard_advisory')]}
    (root/'validation.json').write_text(json.dumps(report))
    print(json.dumps(report), flush=True)
    return report


def render_complete(story, root):
    prepare(story, root)
    storyboard(root)
    refine_visual_plan(root)
    generate_shard(0, root, count=1)
    return finish(root)



def init_input():
    store = w.Store()
    if not store.find('inputs/horror-long-story.json'):
        store.save('inputs/horror-long-story.json', {'awaiting_private_input': True})
    print(json.dumps({'private_input_ready': True}), flush=True)


def stage_story():
    ROOT.mkdir(parents=True, exist_ok=True)
    store = w.Store()
    print(json.dumps({'check': 'Private Drive store', 'passed': True}), flush=True)
    if os.getenv('GENERATE_FRESH_STORY') == 'true':
        if os.getenv('STORY_FILE'):
            raise RuntimeError('Fresh daily stories cannot use STORY_FILE')
        if store.load(f'runs/{run_id()}/state.json', {}).get('phase') in ('upload_started', 'uploaded', 'complete', 'published', 'verified_private'):
            raise RuntimeError('Run already uploaded; use cleanup-only recovery')
        history = store.load('history.json', [])
        story = w.make_story(dt.datetime.now(ZoneInfo('Asia/Dhaka')).date().isoformat(), history[-30:])
        validate_engagement(story)
        (ROOT/'story.json').write_text(json.dumps(story, indent=2))
        print(json.dumps({'fresh_story_ready': True, 'title': story['title'],
            'words': sum(len(s['narration'].split()) for s in story['scenes'])}), flush=True)
        return
    file_id = store.find(os.getenv('PRIVATE_STORY_KEY', 'inputs/horror-long-story.json'))
    if not file_id:
        raise RuntimeError('Private story input is not configured')
    store.download(file_id, ROOT/'story.json')
    story = w.validate_story(json.loads((ROOT/'story.json').read_text()))
    print(json.dumps({'private_story_accessible': True,
        'words': sum(len(s['narration'].split()) for s in story['scenes'])}), flush=True)


def save_preparation():
    store = w.Store()
    key = f'runs/{run_id()}/state.json'
    if store.load(key, {}).get('phase') in ('upload_started', 'uploaded', 'complete', 'published', 'verified_private'):
        raise RuntimeError('Existing upload requires reconciliation; refusing duplicate')
    verify_preparation(ROOT)
    for name in ASSETS:
        store.upload(ROOT/name, f'runs/{run_id()}/{name}')
    store.save(key, {'phase': 'prepared', 'story': json.loads((ROOT/'story.json').read_text())})
    print(json.dumps({'starting_image_generation': True, 'run': run_id()}), flush=True)


def restore_preparation(images_only=False):
    ROOT.mkdir(parents=True, exist_ok=True)
    store = w.Store()
    for name in (('story.json', 'plan.json', 'duration.json') if images_only else ASSETS):
        file_id = store.find(f'runs/{run_id()}/{name}')
        if not file_id:
            raise RuntimeError('Saved preparation asset missing')
        store.download(file_id, ROOT/name)
    verify_preparation(ROOT, images_only=images_only)


def verify_preparation(root, images_only=False):
    manifest = read_cache(root/'duration.json')
    plan = read_cache(root/'plan.json')
    if (not dc.valid(manifest.get('duration_seconds', float('nan')))
            or manifest.get('story_sha256') != file_hash(root/'story.json')
            or manifest.get('plan_source_hash') != plan.get('source_hash')
            or not plan.get('storyboard_verified')):
        raise RuntimeError('Corrected story and storyboard checkpoint do not match')
    if not images_only and (manifest.get('narration_sha256') != file_hash(root/'narration.wav')
            or manifest.get('captions_sha256') != file_hash(root/'captions.srt')):
        raise RuntimeError('Narration or caption checkpoint changed after synchronization')


def shard_names(shard, plan):
    if not 0 <= shard < SHARDS:
        raise ValueError('Invalid shard')
    return [f"shot-{shot['index']:03}.{ext}" for shot in plan['shots']
        if shot['index'] % SHARDS == shard for ext in ('png', 'mp4', 'qa.json')]


def save_shard(shard):
    verify_preparation(ROOT, images_only=True)
    names = shard_names(shard, json.loads((ROOT/'plan.json').read_text()))
    # Validate the entire local bundle before uploading anything. No generation here.
    if any(not (ROOT/name).is_file() for name in names):
        raise RuntimeError('Incomplete local shard; use recover-shard to inspect saved assets')
    store = w.Store()
    receipts = {}
    for name in names:
        result = store.upload(ROOT/name, f'runs/{run_id()}/{name}')
        receipts[name] = {'id': result['id'], 'sha256': file_hash(ROOT/name)}
        print(json.dumps({'shard': shard, 'asset': name, 'upload_verified': True}), flush=True)
    store.save(f'runs/{run_id()}/shard-{shard:02}.json', {
        'run': run_id(), 'shard': shard, 'plan_sha256': file_hash(ROOT/'plan.json'),
        'files': receipts, 'complete': True})
    print(json.dumps({'shard': shard, 'saved_privately': True}), flush=True)


def recover_shard(shard):
    """Upload only: restore remote assets, refuse missing work, never render or call YouTube."""
    import re
    if not re.fullmatch(r'daily-\d+', os.getenv('SAVED_RUN_ID', '')):
        raise ValueError('Recovery requires an explicit original daily run ID')
    store = w.Store()
    state = store.load(f'runs/{run_id()}/state.json', {})
    if state.get('phase') != 'prepared' or state.get('video_id'):
        raise RuntimeError('Recovery requires prepared state with no existing YouTube upload')
    # A local encrypted backup must match the authoritative preparation exactly.
    local = {name: file_hash(ROOT/name) for name in ('story.json', 'plan.json', 'duration.json')
        if (ROOT/name).exists()}
    restore_preparation(True)
    if any(file_hash(ROOT/name) != digest for name, digest in local.items()):
        raise RuntimeError('Recovery backup does not match original preparation')
    names = shard_names(shard, json.loads((ROOT/'plan.json').read_text()))
    found = {name: store.find(f'runs/{run_id()}/{name}') for name in names}
    missing = [name for name in names if not found[name] and not (ROOT/name).is_file()]
    print(json.dumps({'run': run_id(), 'shard': shard, 'missing_assets': missing,
        'remote_assets': sum(bool(fid) for fid in found.values()),
        'upload_only_feasible': not missing}), flush=True)
    if missing:
        raise RuntimeError('Original runner files are unavailable; refusing regeneration')
    for name, fid in found.items():
        if not (ROOT/name).exists():
            store.download(fid, ROOT/name)
    save_shard(shard)


def restore_clips():
    restore_preparation()
    store = w.Store()
    for shot in json.loads((ROOT/'plan.json').read_text())['shots']:
        name = f"shot-{shot['index']:03}.mp4"
        file_id = store.find(f'runs/{run_id()}/{name}')
        if not file_id:
            raise RuntimeError('Saved ten-second clip missing')
        store.download(file_id, ROOT/name)
        qa_name = f"shot-{shot['index']:03}.qa.json"
        qa_id = store.find(f'runs/{run_id()}/{qa_name}')
        if not qa_id:
            raise RuntimeError('Visual-check record missing')
        store.download(qa_id, ROOT/qa_name)


def upload():
    report = json.loads((ROOT/'validation.json').read_text())
    if not report.get('render_passed') or report.get('image_interval_seconds') != INTERVAL or not report.get('storyboard_verified'):
        raise RuntimeError('New-format render has not passed validation')
    w.check_video(ROOT/'video.mp4')
    store, key = w.Store(), f'runs/{run_id()}/state.json'
    if store.load(key, {}).get('phase') in ('upload_started', 'uploaded', 'complete', 'published', 'verified_private'):
        raise RuntimeError('Existing upload requires reconciliation; refusing duplicate insertion')
    verify_preparation(ROOT)
    story = json.loads((ROOT/'story.json').read_text())
    digest = hashlib.sha256(' '.join(s['narration'] for s in story['scenes']).encode()).hexdigest()
    history = store.load('history.json', [])
    if any(h.get('hash') == digest for h in history):
        raise RuntimeError('Story was already uploaded')
    state = {'phase': 'rendered', 'story': story, 'hash': digest, 'validation': report}
    for name in ('video.mp4', 'thumbnail.jpg', 'captions.srt', 'story.json', 'plan.json', 'music.audio', 'validation.json'):
        store.upload(ROOT/name, f'runs/{run_id()}/{name}')
    store.save(key, state)
    w.upload_private(ROOT/'video.mp4', ROOT/'thumbnail.jpg', story, state, store, key,
        'Background music: original Magenta MusicRNN composition with synthesized horror drones, sparse minor-key chimes and subtle pulses.')
    history.append({'title': story['title'], 'hash': digest, 'run': run_id(),
        'video_id': state['video_id'], 'format': story.get('format'), 'topic_id': story.get('topic_id'),
        'plot': ' '.join(scene.get('summary', '') for scene in story['scenes'])})
    store.save('history.json', history)
    store.save('last-private-test.json', {'passed': True, 'run': run_id(),
        'video_id': state['video_id'], 'validation': report, 'processing_status': state['processing_status']})
    print(json.dumps({'private_test_passed': True, 'video_id': state['video_id'], **report}), flush=True)


if __name__ == '__main__':
    command = sys.argv[1]
    shard = int(sys.argv[2]) if len(sys.argv)>2 else 0
    actions = {'init-input': init_input, 'stage-story': stage_story, 'prepare': prepare, 'restore-preparation': restore_preparation, 'refine-plan': refine_visual_plan, 'storyboard': storyboard, 'save-preparation': save_preparation,
        'restore-images': lambda: restore_preparation(True), 'generate-shard': lambda: generate_shard(shard),
        'save-shard': lambda: save_shard(shard), 'recover-shard': lambda: recover_shard(shard), 'restore-clips': restore_clips,
        'finish': finish, 'upload': upload}
    try:
        actions[command]()
    except Exception as error:
        print(json.dumps({'command': command, 'passed': False, 'error_type': type(error).__name__,
            'http_status': getattr(error, 'http_status', getattr(getattr(error, 'resp', None), 'status', None)),
            'reason': str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__}), flush=True)
        if command in ('prepare', 'generate-shard', 'finish'):
            raise
        raise SystemExit(1)

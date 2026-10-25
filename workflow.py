"""Cloud rendering and private upload helpers; daily_publish verifies and publishes daily runs."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
import time
from zoneinfo import ZoneInfo


def required(name):
    value = os.getenv(name, '')
    if not value:
        raise RuntimeError(f'Missing configuration: {name}')
    return value


def shell(*args):
    return subprocess.check_output(args, stderr=subprocess.PIPE, text=True)


def probe(path):
    return json.loads(shell('ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)))


def duration(path):
    return float(probe(path)['format']['duration'])


def timestamp(seconds):
    millis = round(seconds * 1000)
    h, rest = divmod(millis, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def validate_story(data):
    if not isinstance(data, dict) or not 4 <= len(data.get('scenes', [])) <= 32:
        raise ValueError('Story must contain 4–32 narration chapters')
    if not isinstance(data.get('title'), str) or not 1 <= len(data['title']) <= 100:
        raise ValueError('Invalid title')
    if not isinstance(data.get('description'), str) or len(data['description']) > 4500:
        raise ValueError('Invalid description')
    tags = data.get('tags', [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags) or len(','.join(tags)) > 400:
        raise ValueError('Invalid tags')
    words = 0
    for scene in data['scenes']:
        for field in ('narration', 'image_prompt'):
            if not isinstance(scene.get(field), str) or not scene[field].strip():
                raise ValueError(f'Missing scene {field}')
        words += len(scene['narration'].split())
    if not 1000 <= words <= 3600:
        raise ValueError(f'Expected 1000–3600 narrated words, including duration revisions; received {words}')
    if not isinstance(data.get('thumbnail_text'), str) or len(data['thumbnail_text']) > 45:
        raise ValueError('Invalid thumbnail text')
    if not isinstance(data.get('thumbnail_prompt'), str):
        raise ValueError('Missing thumbnail prompt')
    return data


def request_story(endpoint, payload):
    from story_cpu import request_story as generate
    return generate(endpoint, payload)


def story_outline_schema():
    def object_schema(properties):
        return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}
    text_schema = {'type': 'string'}
    # Final shot prompts are generated from timed narration, so don't generate
    # discarded four-image chapter variants or duplicate YouTube metadata here.
    scene_schema = object_schema({'summary': text_schema, 'image_prompt': text_schema})
    chapter_keys = [f'chapter_{i:02}' for i in range(1, 21)]
    return object_schema({**{k: text_schema for k in
        ('title', 'character_description', 'protagonist_name')},
        'scenes': object_schema({k: scene_schema for k in chapter_keys})})


def narration_text_schema(lower, upper):
    """Constrain English words in the decoder, including final sentence punctuation."""
    word = r"[A-Za-z0-9][A-Za-z0-9,.;:!?'\-]*"
    final_word = r"[A-Za-z0-9][A-Za-z0-9'\-]*[.!?]"
    pattern = '^' + word + '( ' + word + '){' + str(lower - 2) + ',' + str(upper - 2) + '} ' + final_word + '$'
    return {'type': 'string', 'pattern': pattern}


def narration_schema(keys, target):
    lower, upper = max(85, target - 10), target + 20
    properties = {key: narration_text_schema(lower, upper) for key in keys}
    schema = {'type': 'object', 'properties': {'narrations': {
        'type': 'object', 'properties': properties, 'required': keys, 'additionalProperties': False}},
        'required': ['narrations'], 'additionalProperties': False}
    return schema


def narration_batch(prompt, keys, target, ask):
    """Constrain, then independently measure words before accepting narration."""
    schema = narration_schema(keys, target)
    lower, upper = max(85, target - 10), target + 20
    prompt += ('\nUse plain English narration with ASCII punctuation. Separate words with one space. '
               'Use indirect speech without quotation marks or paragraph breaks. Finish with a complete sentence. '
               f'The decoder requires {lower}-{upper} words in EVERY chapter; develop the event fully.')
    feedback = ''
    for attempt in range(3):
        group = ask(prompt + feedback, 4200, schema)
        values = group.get('narrations', {})
        if set(values) != set(keys) or not all(isinstance(values[key], str) for key in keys):
            raise ValueError('Narration batch must contain every assigned chapter as a string')
        counts = {key: len(values[key].split()) for key in keys}
        if all(lower <= count <= upper for count in counts.values()):
            return [values[key] for key in keys]
        print(json.dumps({'check': 'Qwen narration word targets', 'attempt': attempt + 1,
            'passed': False, 'chapter_words': counts, 'minimum': lower, 'maximum': upper}), flush=True)
        feedback = (f'\nThe draft failed measured word counts: {json.dumps(counts)}. '
            f'Every chapter MUST have {lower}-{upper} spoken words, targeting {target}. '
            'Expand short chapters with concrete action, sensory details, and the protagonist\'s reactions '
            'within that chapter\'s assigned event. Use 8-10 complete sentences per chapter. '
            'Shorten overlong chapters without losing the event. Return ALL assigned chapters, '
            'with no headings, word-count labels, filler, or repeated paragraphs. '
            'Previous draft to revise: ' + json.dumps(group))
    raise RuntimeError('Qwen narration missed measured chapter word targets after three attempts')


def make_story(day, previous):
    if os.getenv('STORY_FILE'):
        # Explicit original test story only; daily jobs must generate fresh stories.
        return validate_story(json.loads(Path(os.environ['STORY_FILE']).read_text()))
    model = os.getenv('STORY_MODEL', 'Qwen/Qwen3-4B-Instruct-2507')
    chapter_keys = [f'chapter_{i:02}' for i in range(1, 21)]
    outline_schema = story_outline_schema()
    def ask(prompt, limit, schema):
        result = request_story('chat/completions', {'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'story_payload',
                'strict': True, 'schema': schema}},
            'temperature': 0.8, 'max_completion_tokens': limit})
        choice = result['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise RuntimeError('Story response was truncated; refusing incomplete script')
        return json.loads(choice['message']['content'])
    topics = json.loads((Path(__file__).parent/'horror_topics.json').read_text())
    recent_ids = {item.get('topic_id') for item in previous[-10:] if isinstance(item, dict)}
    available = [topic for topic in topics if topic['id'] not in recent_ids] or topics
    topic = available[dt.date.fromisoformat(day).toordinal() % len(available)]
    theme = (f"Original fictional horror story: {topic['category']}: {topic['title_angle']}. "
        f"Story seeds: {json.dumps(topic['facts'])}. "
        "Invent new adult characters, a specific setting, consistent supernatural rules and a satisfying final twist. "
        "Use atmosphere, suspense and unsettling discoveries, without graphic gore or sexual violence. "
        "Never present fiction as true events or copy existing stories, franchises or real people.")
    recent_context = [{'title': str(item.get('title', ''))[:100],
                       'plot': str(item.get('plot', ''))[:400]}
                      for item in previous[-10:] if isinstance(item, dict)]
    outline = ask(f'''Plan an original English horror story for Rezan After Dark for {day}.
    Story direction: {theme}
    Avoid these recent titles and outlines: {json.dumps(recent_context)}.
    Open in immediate danger or with a disturbing discovery. Escalate through clues,
    failed explanations, a consequential choice, a confrontation and an earned chilling twist.
    Every chapter advances the plot. Maintain character appearance, location and timeline continuity.
    Return JSON: title (max 100 characters), protagonist_name, character_description (max 40 words),
    scenes (chapter_01 through chapter_20). Each chapter: summary (15-25 words) and
    one image_prompt (8-14 words). Atmospheric cinematic horror imagery, non-graphic.
    Provide ONLY this concise plan, no narration or YouTube metadata yet.
    Exact narration-matched visuals and publishing metadata are generated later.''', 3000, outline_schema)
    outline['scenes'] = [outline['scenes'][k] for k in chapter_keys]
    outline['format'] = 'horror-story'
    outline['topic_id'] = topic['id']
    outline['category'] = topic['category']
    outline['fictional'] = True
    outline['story_seeds'] = topic['facts']
    if len(outline.get('scenes', [])) != 20:
        raise ValueError('Outline requires exactly twenty chapters')
    summaries = [scene['summary'] for scene in outline['scenes']]
    chapter_target = int(os.getenv('STORY_CHAPTER_WORD_TARGET', '110'))
    if not 95 <= chapter_target <= 125:
        raise ValueError('STORY_CHAPTER_WORD_TARGET must be between 95 and 125')
    prior = ''
    for begin in (0, 5, 10, 15):
        keys = chapter_keys[begin:begin+5]
        prompt = f'''Write chapters {begin+1} through {begin+5} of this original fictional horror story.
        Content direction: {theme}
        Title: {outline['title']}. Story seeds: {json.dumps(topic['facts'])}. Character appearance: {outline['character_description']}.
        Assigned chapter events ONLY: {json.dumps([{'chapter': i+1, 'event': summaries[i]} for i in range(begin, begin+5)])}.
        Previous chapter ending: {prior[-700:]}.
        Return JSON with narrations: an object with the exact chapter keys {keys}, each value a narration string.
        Each string MUST contain approximately {chapter_target} English spoken words (within 5 words).
        Use 8-10 complete sentences per chapter, developing its action and sensory details fully.
        Across all twenty chapters, aim for 2,100–2,300 narrated words including the separately added final CTA.
        Natural immersive horror narration with sensory details, tension and believable adult characters.
        Use measured sentences and natural punctuation for slow, deliberate delivery; no spoken stage directions.
        Begin chapter 1 with a frightening discovery or immediate danger, no welcome or setup preamble.
        Develop the original fictional plot through discoveries, choices and consequences.
        End with an earned chilling twist that pays off earlier clues. Do not include any viewer call to action in any chapter;
        the application adds a final like, comment, and subscribe request separately.
        Narrate ONLY the five assigned events, one event per chapter in the listed order. Do not jump ahead.\n        No ending, farewell, viewer question, or call to action before chapter 20.
        Do not add chapter headings or directions to spoken text. No repeated introduction.'''
        parts = narration_batch(prompt, keys, chapter_target, ask)
        for scene, narration in zip(outline['scenes'][begin:begin+5], parts):
            scene['narration'] = narration
        prior = parts[-1]
    outline['scenes'][-1]['narration'] += (' Which part of this story frightened you most? '
        'Leave a comment, like this video, and subscribe to Rezan After Dark for more horror stories.')
    from video_packaging import request_package
    request_package(outline, topic, ask)
    from long_video import validate_engagement
    validate_engagement(outline)
    return validate_story(outline)


def assert_distinct_story(story, history):
    """Reject highly similar recent titles or plot summaries before rendering."""
    def words(value):
        return set(re.findall(r"[a-z]{4,}", value.lower())) - {
            'the', 'that', 'with', 'from', 'when', 'this', 'into', 'their', 'history'}
    title = words(story['title'])
    plot = words(' '.join(scene.get('summary', '') for scene in story['scenes']))
    for item in history[-30:]:
        if not isinstance(item, dict):
            continue
        old_title = words(item.get('title', ''))
        old_plot = words(item.get('plot', ''))
        if title and old_title and len(title & old_title) / len(title | old_title) >= .7:
            raise RuntimeError('Recent title is too similar; review the story before rendering')
        if plot and old_plot and len(plot & old_plot) / len(plot | old_plot) >= .65:
            raise RuntimeError('Recent plot is too similar; review the story before rendering')


def revise_story_duration(story, ratio, direction, indices=None):
    """Rewrite only chapters that need length changes, preserving the exact final CTA."""
    import copy
    if direction not in ('shorten', 'expand') or not 0.4 <= ratio <= 1.8:
        raise ValueError('Invalid duration revision request')
    if len(story['scenes']) != 20:
        raise ValueError('Duration revision requires the twenty-chapter daily story')
    from long_video import validate_engagement
    validate_engagement(story)
    ending = story['scenes'][-1]['narration']
    marker = ' Which part of this story frightened you most? '
    if marker not in ending:
        raise ValueError('Closing CTA marker missing; refusing duration revision')
    cta = marker + ending.split(marker, 1)[1]
    revised = copy.deepcopy(story)
    original = [s['narration'] for s in story['scenes']]
    original[-1] = original[-1].split(marker, 1)[0]
    keys = [f'chapter_{i:02}' for i in range(1, 21)]
    indices = list(range(20)) if indices is None else sorted(set(indices))
    if not indices or any(i not in range(20) for i in indices):
        raise ValueError('Invalid chapter selection for duration revision')
    model = os.getenv('STORY_MODEL', 'Qwen/Qwen3-4B-Instruct-2507')
    for batch_number, begin in enumerate(range(0, len(indices), 5)):
        selected = indices[begin:begin+5]
        batch = [keys[i] for i in selected]
        targets = {keys[i]: max(45, min(180, round(len(original[i].split()) * ratio)))
                   for i in selected}
        properties = {key: narration_text_schema(
            targets[key] - max(3, round(targets[key] * .05)),
            targets[key] + max(3, round(targets[key] * .05))) for key in batch}
        schema = {'type': 'object', 'properties': properties,
                  'required': batch, 'additionalProperties': False}
        prompt = f'''Revise these chapters of an original fictional horror story to {direction} the
spoken narration. Preserve character identities, supernatural rules, chronology, clues,
chapter order and final twist. Add atmosphere or action without changing the plot.
Never add a viewer call to action; the application preserves the original CTA separately.
Title: {story['title']}. Story seeds: {json.dumps(story.get('story_seeds', []))}.
Adjacent chapter context: {json.dumps({keys[i]: {'before': original[i-1][-350:] if i else '',
    'after': original[i+1][:350] if i < 19 else ''} for i in selected})}
Chapter text: {json.dumps({keys[i]: original[i] for i in selected})}
Approximate spoken-word targets: {json.dumps(targets)}.
Return JSON with exactly these chapter keys, each a complete revised narration string.
Use ASCII punctuation, single spaces between words, indirect speech and no paragraph breaks.
Chapter 1 retains the frightening hook. Chapter 20 retains the chilling final twist.'''
        result = request_story('chat/completions', {'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'duration_revision', 'strict': True, 'schema': schema}},
            'temperature': .35, 'max_completion_tokens': 4400})
        choice = result['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise RuntimeError('Duration revision was truncated')
        values = json.loads(choice['message']['content'])
        if set(values) != set(batch):
            raise ValueError('Duration revision omitted a chapter')
        for index, key in zip(selected, batch):
            narration = values[key].strip()
            actual = len(narration.split())
            if not narration or not max(35, round(targets[key]*.7)) <= actual <= round(targets[key]*1.3):
                raise ValueError(f'Duration revision chapter {index+1} missed its word target')
            revised['scenes'][index]['narration'] = narration
    revised['scenes'][-1]['narration'] += cta
    validate_engagement(revised)
    return validate_story(revised)


def make_image(prompt, path):
    prompt = ('atmospheric cinematic horror illustration, realistic material texture, '
        'eerie shadows, dim moonlight, suspenseful non-graphic scene, landscape composition, ' + prompt)
    if os.getenv('IMAGE_BACKEND', 'cpu') == 'cpu':
        from image_cpu import generate
        generate(prompt + ', no text, no watermark', path)
        return
    if os.getenv('IMAGE_BACKEND') != 'hf':
        raise ValueError('IMAGE_BACKEND must be cpu or hf')
    from huggingface_hub import InferenceClient
    image = InferenceClient(api_key=required('HF_TOKEN')).text_to_image(
        prompt=prompt + ', cinematic lighting, landscape composition, no text or watermarks',
        model=required('IMAGE_MODEL'), width=1024, height=576)
    image.save(path)


def speech_chunks(text, limit=200):
    chunks, current = [], ''
    for word in text.split():
        if len(word) > limit:
            raise ValueError('Narration contains a word longer than the TTS input limit')
        candidate = (current + ' ' + word).strip()
        if len(candidate) > limit:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def make_narration(text, path):
    if os.getenv('TTS_BACKEND', 'kokoro') != 'kokoro':
        raise RuntimeError('TTS_BACKEND must be kokoro; hosted narration is disabled')
    from voice_cpu import generate
    generate(text, path)


def transcribe(audio):
    if os.getenv('CAPTION_BACKEND', 'cpu') != 'cpu':
        raise RuntimeError('CAPTION_BACKEND must be cpu; hosted transcription is disabled')
    from voice_cpu import transcribe_audio
    return transcribe_audio(audio)


def aligned_subtitles(word_lists, lengths, path):
    cues, elapsed, index = [], 0.0, 1
    for words, length in zip(word_lists, lengths):
        for start in range(0, len(words), 8):
            group = words[start:start+8]
            begin, end = float(group[0]['start']), float(group[-1]['end'])
            if not 0 <= begin < end <= length + 0.1:
                raise ValueError('Caption timestamps exceed scene audio')
            text = '\n'.join(textwrap.wrap(' '.join(w['word'].strip() for w in group), 48))
            cues.append(f'{index}\n{timestamp(elapsed+begin)} --> {timestamp(elapsed+min(end,length))}\n{text}\n')
            index += 1
        elapsed += length
    path.write_text('\n'.join(cues), encoding='utf-8')


def render_scene(image, audio, output):
    length = duration(audio)
    frames = round(length * 30)
    vf = ("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
          f"zoompan=z='min(zoom+0.00015,1.06)':d={frames}:s=1920x1080:fps=30,"
          f"fade=t=in:st=0:d=0.35,fade=t=out:st={max(0,length-0.35)}:d=0.35,format=yuv420p")
    shell('ffmpeg', '-y', '-i', str(image), '-i', str(audio), '-vf', vf, '-t', str(length),
          '-c:v', 'libx264', '-preset', 'fast', '-crf', '21', '-c:a', 'aac', '-ar', '48000',
          '-ac', '2', '-movflags', '+faststart', str(output))
    return length


def subtitles(scenes, lengths, path):
    # Phrase timings are estimates within precisely measured scene audio, not forced alignment.
    cues, elapsed, index = [], 0.0, 1
    for scene, length in zip(scenes, lengths):
        words = scene['narration'].split()
        for start in range(0, len(words), 9):
            end = min(start + 9, len(words))
            text = '\n'.join(textwrap.wrap(' '.join(words[start:end]), 48))
            cues.append(f'{index}\n{timestamp(elapsed+length*start/len(words))} --> '
                        f'{timestamp(elapsed+length*end/len(words))}\n{text}\n')
            index += 1
        elapsed += length
    path.write_text('\n'.join(cues), encoding='utf-8')


def check_video(path, min_duration=900, max_duration=1200):
    metadata = probe(path)
    video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == (1920, 1080), 'Wrong output dimensions'
    assert min_duration <= float(metadata['format']['duration']) <= max_duration, 'Duration outside required range'
    assert any(s['codec_type'] == 'audio' for s in metadata['streams']), 'Missing audio'
    shell('ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-')
    return metadata


def drive_transient(error):
    """Retry transport failures and temporary API responses, never permission/quota errors."""
    import socket
    import ssl
    import http.client
    status = getattr(getattr(error, 'resp', None), 'status', None)
    return isinstance(error, (TimeoutError, ConnectionError, socket.timeout,
        ssl.SSLError, http.client.IncompleteRead, http.client.RemoteDisconnected)) or status in (408, 429, 500, 502, 503, 504)


def drive_retry(call):
    for attempt in range(5):
        try:
            return call()
        except Exception as error:
            if not drive_transient(error) or attempt == 4:
                raise
            print(json.dumps({'check': 'Drive request', 'retry': attempt + 1,
                'error_type': type(error).__name__}), flush=True)
            time.sleep(min(2 ** (attempt + 1), 16))


class Store:
    """Private Google Drive storage; never purchases additional quota."""
    def __init__(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        self.client = build('drive', 'v3', credentials=Credentials.from_authorized_user_info(
            json.loads(required('DRIVE_TOKEN_JSON'))), cache_discovery=False)
        self.folder = os.getenv('DRIVE_FOLDER_ID')
        if not self.folder:
            raise RuntimeError('DRIVE_FOLDER_ID is required for this separate channel')
        info = drive_retry(lambda: self.client.files().get(fileId=self.folder, fields='mimeType,trashed,appProperties').execute())
        if (info.get('trashed') or info['mimeType'] != 'application/vnd.google-apps.folder'
                or info.get('appProperties', {}).get('rezan_after_dark_root') != 'v1'):
            raise RuntimeError('DRIVE_FOLDER_ID must identify the dedicated Rezan After Dark folder')
        self.check_private(self.folder)

    def check_private(self, file_id):
        permissions = drive_retry(lambda: self.client.permissions().list(fileId=file_id,
            fields='permissions(type,role)', pageSize=100).execute()).get('permissions', [])
        if any(p.get('role') != 'owner' for p in permissions):
            raise RuntimeError('Workflow storage must be private to its owner')

    def find(self, key):
        # Query using only an internal SHA-256 key, never interpolated user input.
        digest = hashlib.sha256(key.encode()).hexdigest()
        q = (f"'{self.folder}' in parents and trashed = false and "
             f"appProperties has {{ key='workflow_key' and value='{digest}' }}")
        result = drive_retry(lambda: self.client.files().list(q=q, fields='nextPageToken,files(id)', pageSize=100).execute())
        items = result['files']
        if result.get('nextPageToken'):
            raise RuntimeError('Duplicate checkpoint files require reconciliation')
        if len(items) > 1:
            raise RuntimeError('Duplicate checkpoint files require reconciliation')
        if items:
            self.check_private(items[0]['id'])
            return items[0]['id']
        return None

    def load(self, key, default):
        file_id = self.find(key)
        if not file_id:
            return default
        return json.loads(drive_retry(lambda: self.client.files().get_media(fileId=file_id).execute()))

    def check_quota(self, size):
        quota = drive_retry(lambda: self.client.about().get(fields='storageQuota').execute())['storageQuota']
        if 'limit' not in quota:
            raise RuntimeError('Cannot verify free storage allowance; stop before upload')
        limit = min(int(quota['limit']), 15 * 1024**3)
        if int(quota.get('usage', 0)) + size + 100 * 1024**2 > limit:
            raise RuntimeError('Free storage allowance nearly full; no paid upgrade will be requested')

    def metadata(self, key, file_id):
        metadata = {'id': file_id, 'name': key.replace('/', '__'), 'parents': [self.folder],
                    'appProperties': {'workflow_key': hashlib.sha256(key.encode()).hexdigest()}}
        if os.getenv('RUN_SCOPED_CLEANUP') == 'true' and key.startswith('runs/'):
            parts = key.split('/')
            from daily_publish import disposable
            if len(parts) == 3 and disposable(parts[2]):
                metadata['appProperties'].update(automation_run=parts[1], disposable='v1')
        return metadata

    def verified(self, file_id, key, size, checksum):
        try:
            remote = drive_retry(lambda: self.client.files().get(fileId=file_id,
                fields='id,parents,trashed,appProperties,size,md5Checksum').execute())
        except Exception as error:
            if getattr(getattr(error, 'resp', None), 'status', None) == 404:
                return False
            raise
        if (remote.get('trashed') or self.folder not in remote.get('parents', []) or
                remote.get('appProperties', {}).get('workflow_key') != hashlib.sha256(key.encode()).hexdigest()):
            raise RuntimeError('Drive upload identity mismatch; refusing overwrite')
        self.check_private(file_id)
        return str(remote.get('size')) == str(size) and remote.get('md5Checksum') == checksum

    def put(self, key, media, size):
        """Bounded retries on one file ID and upload session; verify before returning."""
        checksum = hashlib.md5()
        for offset in range(0, size, 8 * 1024 * 1024):
            checksum.update(media.getbytes(offset, min(8 * 1024 * 1024, size - offset)))
        checksum = checksum.hexdigest()
        self.check_private(self.folder)
        file_id = self.find(key)
        creating = file_id is None
        journal = Path(os.getenv('OUTPUT_DIR', 'output')) / '.drive-upload-ids' / (
            hashlib.sha256((self.folder + ':' + key).encode()).hexdigest() + '.json')
        reserved = json.loads(journal.read_text())['id'] if journal.exists() else None
        if file_id and reserved and file_id != reserved:
            raise RuntimeError('Drive file ID differs from reserved upload identity')
        file_id = file_id or reserved
        if file_id and self.verified(file_id, key, size, checksum):
            return {'id': file_id}
        self.check_quota(size)
        if creating and not file_id:
            file_id = drive_retry(lambda: self.client.files().generateIds(count=1, space='drive').execute())['ids'][0]
        journal.parent.mkdir(parents=True, exist_ok=True)
        temporary = journal.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id': file_id}))
        temporary.replace(journal)
        metadata = self.metadata(key, file_id)

        def new_request():
            if creating:
                return self.client.files().create(body=metadata, media_body=media, fields='id')
            return self.client.files().update(fileId=file_id, media_body=media, fields='id')

        request, failures = new_request(), 0
        while True:
            try:
                if media.resumable():
                    _, result = request.next_chunk(num_retries=0)
                    if result is None:
                        continue
                else:
                    request.execute(num_retries=0)
                if not self.verified(file_id, key, size, checksum):
                    raise RuntimeError('Drive upload checksum or size verification failed')
                return {'id': file_id}
            except Exception as error:
                status = getattr(getattr(error, 'resp', None), 'status', None)
                if not drive_transient(error) and status not in (404, 410, 409):
                    raise
                # A response can time out after Drive has committed the complete file.
                # Read errors are not evidence that the file is absent.
                if self.verified(file_id, key, size, checksum):
                    return {'id': file_id}
                if status == 409:
                    raise RuntimeError('Drive creation conflict; refusing overwrite') from error
                failures += 1
                if failures >= 5:
                    raise
                print(json.dumps({'check': 'Drive upload', 'retry': failures,
                    'error_type': type(error).__name__}), flush=True)
                time.sleep(min(2 ** failures, 16))
                if status in (404, 410):
                    # Expired resumable session: reuse the reserved ID, never a new file.
                    request = new_request()

    def save(self, key, value):
        import io
        from googleapiclient.http import MediaIoBaseUpload
        payload = json.dumps(value).encode()
        return self.put(key, MediaIoBaseUpload(io.BytesIO(payload),
            mimetype='application/json', resumable=False), len(payload))

    def upload(self, path, key):
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
        try:
            return self.put(key, media, path.stat().st_size)
        finally:
            media.stream().close()

    def download(self, file_id, path):
        from googleapiclient.http import MediaIoBaseDownload
        self.check_private(file_id)
        info = drive_retry(lambda: self.client.files().get(fileId=file_id,
            fields='size,md5Checksum').execute())
        temporary = path.with_name(path.name + '.download-part')
        try:
            with temporary.open('wb') as output:
                request = MediaIoBaseDownload(output, self.client.files().get_media(fileId=file_id),
                    chunksize=8 * 1024 * 1024)
                done = False
                while not done:
                    _, done = drive_retry(lambda: request.next_chunk(num_retries=0))
            digest = hashlib.md5()
            with temporary.open('rb') as downloaded:
                for chunk in iter(lambda: downloaded.read(8 * 1024 * 1024), b''):
                    digest.update(chunk)
            if (str(temporary.stat().st_size) != str(info.get('size')) or
                    digest.hexdigest() != info.get('md5Checksum')):
                raise RuntimeError('Drive download checksum or size verification failed')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def load_music(store, root):
    # CPU music generation uses no API credits or paid provider.
    if os.getenv('MUSIC_BACKEND', 'magenta') == 'magenta':
        from music_cpu import generate
        generate(root/'music.audio')
        return 'Background music: original Magenta MusicRNN composition with synthesized horror drones, sparse minor-key chimes and subtle pulses.'
    # Only free sources. vidIQ-generated assets are not accepted.
    manifest = store.load('music-manifest.json', None)
    if not manifest or manifest.get('source') not in ('YouTube Audio Library', 'AI-generated (free local model)'):
        raise RuntimeError('Missing verified background music manifest in Drive')
    if manifest.get('attribution_required') and not manifest.get('attribution', '').strip():
        raise RuntimeError('Audio Library track requires attribution')
    target = root/'music.audio'
    store.download(manifest['file_id'], target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != manifest['sha256']:
        raise RuntimeError('Music file differs from the licensed track in the manifest')
    duration(target)  # Reject unreadable files.
    return manifest.get('attribution', '')


def upload_private(video, thumbnail, story, state, store, key, music_credit=''):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    client = build('youtube', 'v3', credentials=Credentials.from_authorized_user_info(
        json.loads(required('YOUTUBE_TOKEN_JSON'))), cache_discovery=False)
    channels = client.channels().list(part='id', mine=True).execute()['items']
    if required('EXPECTED_CHANNEL_ID') not in [c['id'] for c in channels]:
        raise RuntimeError('OAuth account is not the expected YouTube channel')
    # Persist before insert. An interrupted/ambiguous upload requires manual reconciliation.
    # Never silently retry videos.insert and risk duplicate videos.
    state['upload_description'] = story['description'] + '\n\n' + music_credit
    state['thumbnail_uploaded'] = False
    state['phase'] = 'upload_started'
    store.save(key, state)
    request = client.videos().insert(part='snippet,status', notifySubscribers=False,
        body={'snippet': {'title': story['title'], 'description': state['upload_description'], 'tags': story['tags'], 'categoryId': '24',
             'defaultLanguage': 'en'}, 'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False}},
        media_body=MediaFileUpload(str(video), chunksize=8*1024*1024, resumable=True))
    result = None
    while result is None:
        _, result = request.next_chunk(num_retries=5)
    state.update(phase='uploaded', video_id=result['id'])
    store.save(key, state)
    client.thumbnails().set(videoId=result['id'], media_body=MediaFileUpload(str(thumbnail))).execute()
    state['thumbnail_uploaded'] = True
    store.save(key, state)
    deadline = time.monotonic() + 1200
    while True:
        items = client.videos().list(part='status,processingDetails', id=result['id']).execute().get('items', [])
        if not items:
            raise RuntimeError('Uploaded video is not accessible for verification')
        remote = items[0]
        assert remote['status']['privacyStatus'] == 'private', 'Upload privacy validation failed'
        processing = remote.get('processingDetails', {}).get('processingStatus')
        upload_status = remote['status'].get('uploadStatus')
        if processing in ('failed', 'terminated') or upload_status in ('failed', 'rejected', 'deleted'):
            raise RuntimeError('YouTube rejected or failed to process the private video')
        if processing == 'succeeded' and upload_status == 'processed':
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('YouTube processing is still pending; private test is not passed')
        print(json.dumps({'check': 'YouTube processing', 'status': processing}), flush=True)
        time.sleep(20)
    state['processing_status'] = 'succeeded'
    state['phase'] = 'complete'
    store.save(key, state)


def preflight():
    needed = ['DRIVE_TOKEN_JSON']
    if not os.getenv('STORY_FILE'):
        import importlib.util
        if importlib.util.find_spec('llama_cpp') is None:
            needed.append('llama_cpp (install requirements-story.txt)')
    if os.getenv('IMAGE_BACKEND', 'cpu') == 'hf':
        needed += ['HF_TOKEN', 'IMAGE_MODEL']
    else:
        import importlib.util
        for module in ('torch', 'diffusers', 'transformers'):
            if importlib.util.find_spec(module) is None:
                needed.append(module + ' (CPU dependency)')
    import importlib.util
    if os.getenv('TTS_BACKEND', 'kokoro') == 'kokoro' and importlib.util.find_spec('kokoro') is None:
        needed.append('kokoro (CPU dependency)')
    if os.getenv('CAPTION_BACKEND', 'cpu') == 'cpu' and importlib.util.find_spec('faster_whisper') is None:
        needed.append('faster_whisper (CPU dependency)')
    if os.getenv('MUSIC_BACKEND', 'magenta') == 'magenta' and not shutil.which('node'):
        needed.append('node (CPU music dependency)')
    if os.getenv('UPLOAD_ENABLED') == 'true':
        needed += ['YOUTUBE_TOKEN_JSON', 'EXPECTED_CHANNEL_ID']
    missing = [name for name in needed if not os.getenv(name)]
    missing += [name for name in ('ffmpeg', 'ffprobe') if not shutil.which(name)]
    if missing:
        raise RuntimeError('Missing: ' + ', '.join(missing))


def run(daily=False):
    if daily and os.getenv('STORY_FILE'):
        raise RuntimeError('Daily automation cannot reuse a fixed test story')
    if daily and os.getenv('DAILY_ENABLED') != 'true':
        raise RuntimeError('Daily automation is disabled until the complete private test passes')
    preflight()
    day = dt.datetime.now(ZoneInfo('Asia/Dhaka')).date().isoformat()
    run_id = day if daily else day + '-test'
    root = Path('output') / run_id
    root.mkdir(parents=True, exist_ok=True)
    store, key = Store(), f'runs/{run_id}/state.json'
    test_record = store.load('last-private-test.json', {}) if daily else {}
    if daily and (not test_record.get('passed') or not test_record.get('remote_verification', {}).get('private_test_passed') or test_record.get('validation', {}).get('image_interval_seconds') != 10 or not test_record.get('validation', {}).get('storyboard_verified') or test_record.get('validation', {}).get('style') != 'horror-story'):
        raise RuntimeError('No fully verified private HD upload test is recorded')
    state = store.load(key, {})
    if state.get('phase') in ('upload_started', 'uploaded', 'complete', 'published', 'verified_private'):
        raise RuntimeError('Run already uploaded or needs upload reconciliation; inspect state before continuing')
    music_credit = load_music(store, root)
    history = store.load('history.json', [])
    story = state.get('story') or make_story(day, history[-30:])
    assert_distinct_story(story, history)
    digest = hashlib.sha256(' '.join(s['narration'] for s in story['scenes']).encode()).hexdigest()
    if digest in [h.get('hash') for h in history]:
        raise RuntimeError('Exact duplicate story rejected')
    state.update(story=story, phase='generating', hash=digest)
    store.save(key, state)
    (root/'story.json').write_text(json.dumps(story, indent=2))
    from long_video import render_complete
    report = render_complete(story, root)
    video, thumb = root/'video.mp4', root/'thumbnail.jpg'
    for name in ('video.mp4', 'thumbnail.jpg', 'captions.srt', 'story.json', 'validation.json', 'plan.json'):
        store.upload(root/name, f'runs/{run_id}/{name}')
    state['validation'] = report
    state['phase'] = 'rendered'
    store.save(key, state)
    if os.getenv('UPLOAD_ENABLED') == 'true':
        upload_private(video, thumb, story, state, store, key, music_credit)
    if state['phase'] == 'complete':
        history.append({'title': story['title'], 'hash': digest, 'run': run_id,
            'format': story.get('format'), 'topic_id': story.get('topic_id'),
            'plot': ' '.join(scene.get('summary', '') for scene in story['scenes'])})
        store.save('history.json', history)
        if not daily:
            store.save('last-private-test.json', {'passed': True, 'run': run_id, 'video_id': state['video_id'], 'validation': state['validation']})
    print(json.dumps({'run': run_id, 'phase': state['phase'], 'video_id': state.get('video_id')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['preflight', 'test', 'daily'])
    command = parser.parse_args().command
    if command == 'preflight':
        preflight()
    else:
        run(daily=command == 'daily')

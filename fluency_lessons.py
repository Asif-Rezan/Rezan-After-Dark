"""Original English dialogue lessons, with validated speaker turns and practice pauses."""
import datetime as dt
import hashlib
from itertools import groupby
import json
import re

SPEAKERS = ('Alex', 'Emma')
PREFIX = 'Learn English While You Sleep'
TOPICS = [
    ('coffee', 'Ordering Coffee', ['ORDER', 'COFFEE', 'IN ENGLISH']),
    ('introductions', 'Meeting Someone New', ['MEET', 'NEW PEOPLE', 'IN ENGLISH']),
    ('routine', 'Talking About Your Day', ['YOUR', 'DAILY LIFE', 'IN ENGLISH']),
    ('shopping', 'Shopping and Asking Prices', ['SHOP', 'WITH EASE', 'IN ENGLISH']),
    ('weekend', 'Making Weekend Plans', ['WEEKEND', 'PLANS', 'IN ENGLISH']),
    ('directions', 'Asking for Directions', ['ASK FOR', 'DIRECTIONS', 'IN ENGLISH']),
    ('restaurant', 'Ordering at a Restaurant', ['ORDER', 'YOUR FOOD', 'IN ENGLISH']),
    ('work', 'Small Talk at Work', ['SPEAK', 'AT WORK', 'IN ENGLISH']),
    ('travel', 'Checking into a Hotel', ['HOTEL', 'CHECK-IN', 'IN ENGLISH']),
    ('phone', 'Making a Phone Call', ['PHONE', 'CALLS', 'IN ENGLISH']),
    ('appointment', 'Booking an Appointment', ['BOOK AN', 'APPOINTMENT', 'IN ENGLISH']),
    ('friends', 'Catching Up with a Friend', ['TALK TO', 'FRIENDS', 'IN ENGLISH']),
]


def validate_lesson(lesson):
    if lesson.get('format') != 'english-fluency' or lesson.get('level') not in ('A2', 'B1'):
        raise ValueError('An A2 or B1 English fluency lesson is required')
    for field, limit in [('title', 100), ('topic', 65), ('description', 4000)]:
        text = lesson.get(field)
        if not isinstance(text, str) or not text.strip() or len(text) > limit or '\n' in text:
            raise ValueError(f'Invalid lesson {field}')
    if not lesson['title'].startswith(PREFIX + ' | ') or not lesson['description'].startswith(PREFIX + ' | '):
        raise ValueError('Title and description must begin with the required sleep-series prefix')
    heads = lesson.get('headline')
    if (not isinstance(heads, list) or len(heads) != 3
            or any(not isinstance(h, str) or not re.fullmatch(r"[A-Z0-9 '-]{1,18}", h) for h in heads)):
        raise ValueError('Thumbnail needs three short uppercase headline lines')
    tags = lesson.get('tags')
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags) or len(','.join(tags)) > 400:
        raise ValueError('Invalid lesson tags')
    if not tags or tags[0] != PREFIX:
        raise ValueError('First tag must be the sleep-series prefix')
    turns = lesson.get('turns')
    if not isinstance(turns, list) or not 8 <= len(turns) <= 260:
        raise ValueError('Expected 8–260 lesson turns')
    heard = set()
    for turn in turns:
        speaker, text = turn.get('speaker'), turn.get('text')
        if speaker not in SPEAKERS or not isinstance(text, str) or not text.strip() or len(text) > 180:
            raise ValueError('Invalid speaker or dialogue text')
        if len(text.split()) > 28 or any(ord(c) < 32 for c in text):
            raise ValueError('Keep each turn short and free of control characters')
        if turn.get('section') not in ('LISTEN', 'REPEAT', 'YOUR TURN', 'FINISH'):
            raise ValueError('Invalid lesson section')
        pause = turn.get('pause', .35)
        if isinstance(pause, bool) or not isinstance(pause, (float, int)) or not 0 <= pause <= 10:
            raise ValueError('Practice pause must be 0–10 seconds')
        if turn['section'] == 'YOUR TURN' and pause < 2:
            raise ValueError('Role play must leave time for the learner to answer')
        heard.add(speaker)
    if heard != set(SPEAKERS):
        raise ValueError('Both male and female speakers are required')
    return lesson


def dialogue_schema():
    # Fixed keys assign speakers in code; constrained English word counts are
    # enforced by the same production grammar already used for narration.
    from workflow import narration_text_schema
    properties = {f'line_{i:02}': narration_text_schema(6, 18) for i in range(1, 17)}
    return {'type': 'object', 'properties': {'dialogue': {
        'type': 'object', 'properties': properties, 'required': list(properties),
        'additionalProperties': False}}, 'required': ['dialogue'], 'additionalProperties': False}


def normalized_words(text):
    return ' '.join(re.findall(r"\w+", text.casefold()))


def dialogue_hash(turns):
    lines = [normalized_words(turn['text']) for turn in turns]
    return hashlib.sha256(json.dumps(lines).encode()).hexdigest()


def lesson_dialogue_hashes(lesson):
    hashes = []
    for section, turns in groupby(lesson['turns'], key=lambda turn: turn['section']):
        if section == 'LISTEN':
            dialogue = list(turns)
            if dialogue[0]['text'].startswith("Let's practice "):
                dialogue = dialogue[1:]
            if dialogue:
                hashes.append(dialogue_hash(dialogue))
    return hashes


def generate_lesson(day, previous, ask=None):
    """Three different situations within one topic; never pad duration with filler."""
    if ask is None:
        from story_cpu import request_story
        def ask(prompt, schema):
            result = request_story('chat/completions', {
                'messages': [{'role': 'user', 'content': prompt}], 'max_completion_tokens': 2400,
                'response_format': {'json_schema': {'schema': schema}}, 'temperature': .7})
            return json.loads(result['choices'][0]['message']['content'])
    recent = {item.get('topic_id') for item in previous[-8:]}
    available = [topic for topic in TOPICS if topic[0] not in recent] or TOPICS
    topic_id, topic, headline = available[dt.date.fromisoformat(day).toordinal() % len(available)]
    package_schema = {'type':'object','properties':{'angle':{'type':'string','minLength':8,'maxLength':48},
        'headline':{'type':'array','minItems':3,'maxItems':3,'items':{'type':'string','pattern':"^[A-Z0-9 '-]{1,18}$"}}},
        'required':['angle','headline'],'additionalProperties':False}
    # Keep same-topic history even after it leaves the short topic rotation window.
    packaging_history = [dict(title=item.get('title',''), headline=item.get('headline',[]))
                         for item in previous if item.get('topic_id') == topic_id][-12:]
    packaging_history += [dict(title=item.get('title',''), headline=item.get('headline',[])) for item in previous[-8:]]
    seen_headlines = {normalized_words(' '.join(item.get('headline', []))) for item in previous}
    seen_titles = {normalized_words(item.get('title', '')) for item in previous}
    seen_dialogues = {digest for item in previous for digest in item.get('dialogue_hashes', [])}
    last_variant = max((item.get('thumbnail_variant', -1) for item in previous), default=-1)
    for attempt in range(3):
        package = ask(f'Plan a fresh A2 adult English lesson about {topic}. Return angle: a specific everyday situation, max 48 characters, and headline: exactly three short uppercase thumbnail lines, each at most 18 characters. Make the headline clearly about this situation. Avoid these earlier titles and headlines: {json.dumps(packaging_history)}. Attempt {attempt + 1}: the complete headline and title must be different from earlier videos. No fluency guarantees, no claims about subconscious learning. JSON only.', package_schema)
        angle = package['angle'].strip()
        headline = package['headline']
        if not 8 <= len(angle) <= 48 or any(ord(c)<32 for c in angle):
            raise ValueError('Invalid lesson angle')
        title = f'{PREFIX} | {angle} | A2 Practice'
        if normalized_words(' '.join(headline)) not in seen_headlines and normalized_words(title) not in seen_titles:
            break
        packaging_history.append(dict(title=title, headline=headline))
    else:
        raise ValueError('Duplicate title or thumbnail headline after three attempts')
    topic = angle
    turns = [dict(speaker='Emma', text=f"Let's practice {topic.lower()}. Listen first, then speak with us.",
                  section='LISTEN', pause=.6)]
    summaries = []
    for index in range(3):
        prompt = f'''Write an original A2 English conversation for adult learners: {topic}.
Two adult speakers only: Alex (male), Emma (female). Situation {index + 1} of 3.
Earlier dialogue openings to avoid repeating: {json.dumps(summaries)}.
Create a different concrete everyday situation within this topic. Alternate Alex and Emma.
Return exactly 16 short natural turns, preferably 8–14 words each (allowed 6–18).
Use dialogue keys line_01 through line_16. Odd lines are Emma, even lines Alex.
Each value is ONLY spoken English text, plain ASCII punctuation, ending with . ! or ?.
Do not put speaker names or quote marks into the values. Correct grammar, natural polite
English, clear referents, coherent responses. No narrator, headings, stage directions,
pronunciation spellings, teaching claims, links, or invented facts about real people.
No fluency guarantees. Return only the requested JSON dialogue.'''
        feedback = ''
        for attempt in range(3):
            data = ask(prompt + feedback, dialogue_schema())
            lines = data.get('dialogue', {})
            keys = [f'line_{i:02}' for i in range(1, 17)]
            valid = isinstance(lines, dict) and set(lines) == set(keys)
            if valid:
                valid = all(isinstance(lines[key], str) and 6 <= len(lines[key].split()) <= 18 for key in keys)
            if valid:
                dialogue = [dict(speaker='Emma' if i % 2 == 0 else 'Alex', text=lines[key])
                            for i, key in enumerate(keys)]
                fingerprint = dialogue_hash(dialogue)
                if fingerprint not in seen_dialogues and any('?' in line['text'] for line in dialogue):
                    seen_dialogues.add(fingerprint)
                    break
            feedback = f' Repair attempt {attempt + 1}: use all 16 line keys, 6–18 words each, and include natural questions. Write a new conversation with different lines.'
        else:
            raise ValueError('Generated dialogue failed validation after three attempts')
        summaries.append(dialogue[0]['text'])
        turns += [dict(**line, section='LISTEN', pause=.4) for line in dialogue]
        turns.append(dict(speaker='Emma', text="Now listen and repeat each sentence. Use the pause to speak out loud.",
                          section='REPEAT', pause=.6))
        turns += [dict(**line, section='REPEAT', pause=min(8., max(3., len(line['text'].split()) * .45)))
                  for line in dialogue]
        turns.append(dict(speaker='Emma', text="Your turn. Answer these questions in your own words.",
                          section='YOUR TURN', pause=2.))
        questions = [line for line in dialogue if '?' in line['text']][:3]
        if not questions:
            raise ValueError('Conversation must include questions for role play')
        turns += [dict(**line, section='YOUR TURN', pause=5.) for line in questions]
    turns.append(dict(speaker='Emma', text='Well done! Try these phrases in your own conversation today.',
                      section='FINISH', pause=1.))
    return validate_lesson(dict(format='english-fluency', level='A2', topic_id=topic_id, topic=topic,
        title=f'{PREFIX} | {topic} | A2 Practice', headline=headline, thumbnail_variant=last_variant + 1,
        description=f'{PREFIX} | {topic}. Practice {topic.lower()} with Alex and Emma. Listen to three original conversations, repeat each sentence, and answer aloud during role play. A2 English listening and speaking practice with on-screen subtitles.',
        tags=[PREFIX, 'English conversation', 'English speaking practice', 'shadowing practice', 'A2 English', topic], turns=turns))

"""Story-aware YouTube packaging for original fictional horror."""
import json
import re
import time
from thumbnail_design import hook_layout

CLICKBAIT = re.compile(r'\b(?:shocking|unbelievable|exposed|proof|finally revealed|they hid|you won.t believe)\b', re.I)
VAGUE_HOOK = re.compile(r'^(?:WHAT HAPPENED|THE HIDDEN TRUTH|WHAT IS THIS|WHAT\'S INSIDE)\?$', re.I)
QUESTION_OPENERS = {'HOW', 'WHY', 'WHAT', 'WHO', 'WHERE'}
# Enforce the mechanical question format during local Qwen decoding, not just
# after spending several minutes generating each complete package.
HOOK_SCHEMA = {
    'type': 'string',
    'maxLength': 30,
    'pattern': r"^(HOW|WHY|WHAT|WHO|WHERE)( [A-Z][A-Z0-9'-]{0,11}){1,4}\?$",
}


def _plain(value):
    return re.sub(r'\s+', ' ', value).strip()


def artwork_candidate(prompt, topic):
    """Use the curated composition when generated artwork misses mechanical limits.

    Never truncate a scene or append an anchor to unrelated generated content.
    The replacement still passes every visual validation below.
    """
    anchor = topic['thumbnail_anchor']
    if 25 <= len(prompt) <= 220 and anchor.casefold() in prompt.casefold():
        return prompt
    curated = _plain(topic['thumbnail_visual_story'])
    return curated if anchor.casefold() in curated.casefold() else f'{anchor}. {curated}'


def description_parts(payload, topic):
    """Repair mechanical description failures using only the curated topic facts."""
    keyword = topic['search_phrase']
    intro = _plain(payload.get('description_intro', ''))
    details = _plain(payload.get('description_details', ''))
    # Sensational claims must be rejected, even if another constraint also fails.
    if CLICKBAIT.search(intro) or CLICKBAIT.search(details):
        raise ValueError('Description contains prohibited clickbait')
    if (not 80 <= len(intro) <= 400 or keyword.casefold() not in
            re.split(r'(?<=[.!?])\s', intro, maxsplit=1)[0].casefold()):
        intro = (f'{keyword}: an original fictional horror story of mounting dread, '
                 'unsettling discoveries and a chilling final twist from Rezan After Dark.')
    if not 80 <= len(details) <= 800:
        facts = [_plain(fact) for fact in topic.get('facts', []) if _plain(fact)]
        if not facts:
            raise ValueError('Description fallback requires curated story seeds')
        details = ' '.join(facts) + ' This original fictional tale follows escalating suspense toward a chilling final revelation.'
    if (not 80 <= len(intro) <= 400 or not 80 <= len(details) <= 800
            or CLICKBAIT.search(intro) or CLICKBAIT.search(details)):
        raise ValueError('Curated description exceeds metadata constraints')
    if not intro.endswith(('.', '?', '!')):
        intro += '.'
    return intro, details


def choose_package(story, topic, payload):
    """Use a concise, accurate title and matching thumbnail hook; retain alternatives for review."""
    keyword = topic['search_phrase']
    candidates = [payload.get(key, {}) for key in ('option_a', 'option_b', 'option_c')]
    valid = []
    rejected = []
    for name, option in zip(('option_a', 'option_b', 'option_c'), candidates):
        title = _plain(option.get('title', ''))
        text = _plain(option.get('thumbnail_text', ''))
        prompt = artwork_candidate(_plain(option.get('thumbnail_prompt', '')), topic)
        if not 35 <= len(title) <= 75 or keyword.casefold() not in title[:52].casefold():
            rejected.append(f'{name}: title must be 35-75 characters and contain the exact subject within its first 52 characters')
            continue
        if CLICKBAIT.search(title) or CLICKBAIT.search(text):
            rejected.append(f'{name}: title or thumbnail question uses prohibited clickbait')
            continue
        hook_words = text.rstrip('?').split()
        if (not text.endswith('?') or not 2 <= len(hook_words) <= 5 or len(text) > 30
                or hook_words[0].upper() not in QUESTION_OPENERS or VAGUE_HOOK.fullmatch(text)):
            rejected.append(f'{name}: thumbnail needs a specific 2-5 word question, max 30 characters, ending ?, starting HOW/WHY/WHAT/WHO/WHERE')
            continue
        try:
            hook_layout(text)
        except ValueError:
            rejected.append(f'{name}: thumbnail question cannot fit at readable size')
            continue
        if not 25 <= len(prompt) <= 220 or topic['thumbnail_anchor'].casefold() not in prompt.casefold():
            rejected.append(f'{name}: artwork prompt must be 25-220 characters and contain the exact anchor phrase')
            continue
        if any(re.search(r'\b'+re.escape(term)+r'\b', prompt, re.I)
               for term in topic.get('thumbnail_forbidden_terms', [])):
            rejected.append(f'{name}: artwork prompt contains a forbidden visual element, even if phrased as a negative instruction')
            continue
        if text.casefold() in title.casefold():
            rejected.append(f'{name}: thumbnail question repeats the title')
            continue
        score = 5 if title.casefold().startswith(keyword.casefold()) else 0
        score += 2 if 45 <= len(title) <= 65 else 0
        score += 1 if '?' in title else 0
        score += 2 if len(hook_words) <= 4 else 0
        score += 1 if any(word in text.casefold() for word in topic['thumbnail_anchor'].casefold().split()) else 0
        visual_story = topic['thumbnail_visual_story'].strip().rstrip('.')
        # Put the fictional visual story first so diffusion's token limit preserves it.
        # One concise composition, not several competing scene descriptions.
        # Keep the anchor and its unresolved visual relationship ahead of style.
        artwork_prompt = visual_story if prompt.rstrip('.') == visual_story else f'{visual_story}. {prompt}'
        artwork_prompt = ' '.join(artwork_prompt.split()[:45])
        artwork_prompt += '. Cinematic horror illustration, eerie side light, no text.'
        valid.append((score, {'title': title, 'thumbnail_text': text, 'thumbnail_prompt': artwork_prompt}))
    if not valid:
        raise ValueError('No accurate, readable YouTube title and thumbnail pair: ' + '; '.join(rejected))
    valid.sort(key=lambda pair: pair[0], reverse=True)
    best = valid[0][1]
    intro, details = description_parts(payload, topic)
    relevant_text = ' '.join([keyword, topic['category']] + topic.get('facts', []))
    relevant_stems = {word[:5] for word in re.findall(r'[a-z]{5,}', relevant_text.casefold())
                      if word not in {'history', 'ancient', 'historical', 'documentary', 'archaeology'}}
    base_tags = [keyword, topic['category'], 'horror stories', 'Rezan After Dark']
    if 'archaeolog' in topic['category'].casefold() or 'ancient structure' in topic['category'].casefold():
        base_tags.append('archaeology')
    tags = []
    for tag in base_tags + payload.get('tags', []):
        tag = _plain(tag)
        if tag not in base_tags and not any(word[:5] in relevant_stems for word in
                                            re.findall(r'[a-z]{5,}', tag.casefold())):
            continue
        if tag and tag.casefold() not in [item.casefold() for item in tags] and len(tag) <= 40:
            tags.append(tag)
    tags = tags[:12]
    if len(','.join(tags)) > 400:
        raise ValueError('Video tags exceed YouTube metadata budget')
    description = (f'{intro}\n\n{details}\n\n'
                   'An original fictional horror story from Rezan After Dark. '
                   'All characters and events are fictional. Story, narration and illustrations use AI assistance.')
    if len(description) > 4500:
        raise ValueError('Video description exceeds upload budget')
    story.update(best, description=description, tags=tags, search_phrase=keyword,
                 packaging_alternatives=[item[1] for item in valid[1:]])
    return story


def request_package(story, topic, ask, pause=None):
    pause = pause or time.sleep
    text = {'type': 'string'}
    option = {'type': 'object', 'properties': {
              'title': text, 'thumbnail_text': HOOK_SCHEMA, 'thumbnail_prompt': text},
              'required': ['title', 'thumbnail_text', 'thumbnail_prompt'], 'additionalProperties': False}
    schema = {'type': 'object', 'properties': {
        'option_a': option, 'option_b': option, 'option_c': option,
        'description_intro': text, 'description_details': text,
        'tags': {'type': 'array', 'items': text}},
        'required': ['option_a', 'option_b', 'option_c', 'description_intro', 'description_details', 'tags'],
        'additionalProperties': False}
    outline = [scene.get('summary', '') for scene in story['scenes']]
    prompt = f'''Create YouTube packaging for an English original fictional horror story.
Search subject phrase: {topic['search_phrase']}
Category: {topic['category']}
Fictional story seeds: {json.dumps(topic['facts'])}
Episode outline: {json.dumps(outline)}
Existing title: {story['title']}
Return three DISTINCT title-thumbnail pairs (option_a, option_b, option_c).
Each title: 35-75 characters, put the exact search subject phrase in the first 52 characters,
accurately promise the suspense and central mystery of this story, use normal capitalization.
Avoid generic "mystery revealed" wording, sensational certainty, false true-story claims and misleading clickbait.
Thumbnail text: a SPECIFIC unresolved question with a curiosity gap, 2-5 short words,
maximum 30 characters, ending in ?. Use UPPERCASE words with single spaces.
Start with HOW, WHY, WHAT, WHO or WHERE. Each following word is at most 12 characters.
The question must be addressed in this episode. Avoid vague
questions such as WHAT HAPPENED? or spoilers of the final twist.
Complement the title without repeating its phrasing. It must be legible on a phone.
Thumbnail's fictional visual anchor: {topic['thumbnail_anchor']}.
The image must tell this fictional visual story: {topic['thumbnail_visual_story']}.
Do not mention or depict these elements in thumbnail artwork: {json.dumps(topic.get('thumbnail_forbidden_terms', []))}.
Omit forbidden words entirely, including negative phrases such as "no gore"; the renderer handles exclusions.
Thumbnail prompt: 25-220 characters, include the exact visual-anchor phrase, and add
a specific scene composition that builds on the visual story. Make the ominous presence,
entrance, path or object relationship the source of curiosity even with no words visible.
Place the intriguing detail on the RIGHT and leave dark space on the LEFT for the question.
Use dramatic but plausible light and depth. No embedded letters, labels, arrows, logos,
graphic gore or sexual imagery.
Description intro: 80-400 characters, put the exact subject phrase naturally in the FIRST sentence
and introduce the suspense without spoiling the ending. Description details: 80-800 characters, unique to this episode,
describe the premise and escalating stakes. Never claim that fictional events are true.
Tags: 4-8 concise specific search variants, including sensible spelling variants if useful;
no unrelated trend tags, hashtags, or keyword stuffing. Return only the requested JSON.'''
    feedback = ''
    for attempt in range(3):
        if attempt:
            pause(60)  # Respect the free provider's request/token budget.
        result = ask(prompt + feedback, 2600, schema)
        try:
            return choose_package(story, topic, result)
        except ValueError as error:
            print(json.dumps({'check': 'Horror packaging', 'attempt': attempt+1,
                              'passed': False, 'reason': str(error)}), flush=True)
            if attempt == 2:
                raise
            feedback = ('\nYour previous response failed validation: ' + str(error) +
                        '\nPrevious metadata: ' + json.dumps(result) +
                        '\nCorrect these issues. Return the complete requested JSON; '
                        'keep metadata faithful to the fictional plot and do not reveal the ending.')

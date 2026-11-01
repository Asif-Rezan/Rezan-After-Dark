"""Readable 16:9 horror story thumbnail layout."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from itertools import combinations


def _font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                 '/System/Library/Fonts/Supplemental/Arial Bold.ttf'):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def hook_layout(text):
    """Find a readable one to three line layout before artwork is generated."""
    words = text.upper().split()
    if not 2 <= len(words) <= 5 or len(text) > 30 or not text.endswith('?'):
        raise ValueError('Thumbnail hook must be a short horror question')
    measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    for size in range(96, 55, -4):
        font = _font(size)
        for count in (1, 2, 3):
            for breaks in combinations(range(1, len(words)), count - 1):
                cuts = (0,) + breaks + (len(words),)
                lines = [' '.join(words[cuts[i]:cuts[i + 1]]) for i in range(count)]
                if all(measure.textbbox((0, 0), line, font=font, stroke_width=3)[2] <= 500
                       for line in lines):
                    return lines, size
    raise ValueError('Thumbnail hook cannot fit at readable size')


LAYOUTS = (
    'Extreme close-up of the key object; a small contextual clue beyond it, headline at the top',
    'Diagonal face-to-object tension, opposing scales, headline across the upper third',
    'Over-the-shoulder discovery viewpoint, one striking focal object, headline above the shoulder',
    'Two contrasting states separated diagonally, one shared factual anchor, headline in the center',
    'Low-angle hero object with a tiny human silhouette for scale, headline at the top left',
    'Centered symbolic confrontation, two opposing forces around one anchor, headline at the bottom left',
)
PALETTES = (
    'sunshine yellow and vivid cobalt blue with coral accents',
    'bright turquoise and hot coral with golden highlights',
    'electric blue and warm amber with crisp white highlights',
    'vibrant emerald and tangerine with bright cream highlights',
    'rich violet and lemon yellow with warm peach highlights',
    'vivid scarlet and cyan with luminous golden highlights',
)


def complete_thumbnail_prompt(visual, text, identity):
    """Give the image model the complete artwork and exact headline together."""
    import hashlib
    import re
    hook_layout(text)
    digest=hashlib.sha256(identity.encode()).digest()
    # Remove instructions from saved packages that conflict with native typography.
    visual=re.sub(r'\b(?:no text|no written letters|no letters|no embedded letters)\b', '', visual, flags=re.I)
    visual=re.sub(r'(?:open |empty )?dark space on the (?:left|right)', '', visual, flags=re.I)
    visual=re.sub(r'\b(?:on the |at |at the |on )?(?:right|left)(?: side| foreground)?\b', '', visual, flags=re.I)
    visual=' '.join(visual.split()).strip(' ,.')
    return (f'Bright colorful YouTube thumbnail, {PALETTES[digest[1]%len(PALETTES)]}. '
        f'Exact headline "{text.upper()}" in large bold readable letters. '
        f'{LAYOUTS[digest[0]%len(LAYOUTS)]}. Subject: {visual}. '
        'Perfectly spelled typography integrated into the image. '
        'Create one unresolved visual question: show the meaningful clue or contrast, leave the explanation '
        'for the video. Match this fictional horror episode; tease its central mystery without revealing the ending. No gore. '
        'Bright exposure, saturated clean colors, luminous subject, strong separation, simple background, '
        'one dominant focal point, clear at phone size, cinematic horror key art, eerie atmosphere with a brightly lit focal subject. '
        'Landscape 16:9, headline fully inside 8 percent safe margins, avoid bottom-right timestamp area. '
        'Only the exact headline; no other writing, logos, badges or watermark.')


def render_thumbnail(source, text, target, category='Horror'):
    """Export model-completed artwork; never draw text, labels or a dark overlay."""
    hook_layout(text)
    with Image.open(source) as original:
        image=ImageOps.fit(original.convert('RGB'),(1280,720),method=Image.Resampling.LANCZOS)
    output=Path(target)
    output.parent.mkdir(parents=True,exist_ok=True)
    for quality in (92,85,75):
        image.save(output,quality=quality,optimize=True)
        if output.stat().st_size < 2*1024*1024:
            return output
    raise RuntimeError('Thumbnail exceeds YouTube size limit')


def generate_thumbnail(story, root, identity):
    """Separate thumbnail art direction from the text-free video scene pipeline."""
    import hashlib
    import json
    import os
    from image_cpu import generate
    from resilience import produce_file, retry_local
    root=Path(root)
    prompt=complete_thumbnail_prompt(story['thumbnail_prompt'],story['thumbnail_text'],identity)
    seed=int(hashlib.sha256((identity+prompt).encode()).hexdigest()[:8],16)
    source=root/'thumbnail-source.png'
    if os.getenv('IMAGE_BACKEND','cpu')=='cpu':
        produce_file(source,lambda candidate:generate(prompt,candidate,allow_text=True,seed=seed),
                     'Thumbnail image generation')
    elif os.getenv('IMAGE_BACKEND')=='hf':
        from huggingface_hub import InferenceClient
        from workflow import required
        image=retry_local(lambda:InferenceClient(api_key=required('HF_TOKEN')).text_to_image(
            prompt=prompt,model=required('IMAGE_MODEL'),width=1024,height=576),
            'Hosted thumbnail generation')
        produce_file(source,image.save,'Thumbnail image save')
    else:
        raise ValueError('IMAGE_BACKEND must be cpu or hf')
    target=render_thumbnail(source,story['thumbnail_text'],root/'thumbnail.jpg')
    (root/'thumbnail-design.json').write_text(json.dumps(dict(
        version='horror-native-text-v1',seed=seed,prompt=prompt,text_mode='model-generated',
        typography_review='Model-generated spelling requires visual review'),indent=2))
    return target

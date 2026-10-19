"""Readable 16:9 horror story thumbnail layout."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
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


def render_thumbnail(source, text, target, category='Horror'):
    lines, size = hook_layout(text)
    with Image.open(source) as original:
        image = ImageOps.fit(original.convert('RGB'), (1280, 720), method=Image.Resampling.LANCZOS)
    # Build a photographic focal point from the generated artwork itself: one lit
    # ominous detail on the right, with its surroundings falling into shadow.
    image = ImageEnhance.Contrast(image).enhance(1.12)
    dimmed = ImageEnhance.Brightness(image).enhance(0.69)
    focus = Image.new('L', image.size, 0)
    focus_draw = ImageDraw.Draw(focus)
    focus_draw.ellipse((610, 80, 1290, 650), fill=210)
    focus = focus.filter(ImageFilter.GaussianBlur(105))
    image = Image.composite(image, dimmed, focus)
    # Darken only the text side; preserve the main horror subject on the right.
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    stripe = ImageDraw.Draw(overlay)
    for x in range(0, 650, 4):
        opacity = round(225 * max(0, 1 - (x / 650) ** 1.9))
        stripe.rectangle((x, 0, x+3, 720), fill=(4, 13, 20, opacity))
    image = Image.alpha_composite(image.convert('RGBA'), overlay)
    draw = ImageDraw.Draw(image)
    cream, gold = (250, 247, 235, 255), (235, 69, 69, 255)
    label = 'REZAN AFTER DARK'
    label_font = _font(23)
    label_width = draw.textbbox((0, 0), label, font=label_font)[2]
    draw.rounded_rectangle((60, 72, 95+label_width, 124), radius=9, fill=(190, 38, 45, 255))
    draw.text((78, 83), label, font=label_font, fill=(13, 24, 29, 255))
    # The same layout was checked when the package was selected.
    font = _font(size)
    y = 310 if len(lines) == 1 else (260 if len(lines) == 2 else 210)
    for index, line in enumerate(lines):
        draw.text((61, y + index*(size+18)), line, font=font,
                  fill=cream if index == 0 else gold, stroke_width=3,
                  stroke_fill=(4, 13, 20, 255))
    draw.rectangle((64, 652, 390, 660), fill=gold)
    output = Path(target)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert('RGB').save(output, quality=89, optimize=True)
    if output.stat().st_size >= 2*1024*1024:
        image.convert('RGB').save(output, quality=78, optimize=True)
    if output.stat().st_size >= 2*1024*1024:
        raise RuntimeError('Thumbnail exceeds YouTube size limit')
    return output

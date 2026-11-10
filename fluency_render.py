"""Deterministic white lesson layout and stylish yellow thumbnail compositor."""
from pathlib import Path
import math
import hashlib
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / 'assets' / 'fluency'
INK, MUTED, TEAL = '#142329', '#68747C', '#287A72'


def font(size, display=False):
    face = ImageFont.truetype(str(ASSETS / ('Anton-Regular.ttf' if display else 'NotoSans.ttf')), size)
    if not display:
        face.set_variation_by_axes([600, 100])
    return face


def wrap(draw, text, face, width):
    lines, current = [], ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if draw.textlength(candidate, font=face) > width:
            if not current:
                raise ValueError('Word cannot fit subtitle region')
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + [current]


def fit_text(draw, text, width, max_lines=2, start=36, minimum=23):
    for size in range(start, minimum-1, -1):
        face = font(size)
        try:
            lines = wrap(draw, text, face, width)
        except ValueError:
            continue
        if len(lines) <= max_lines:
            return face, lines
    raise ValueError('Subtitle exceeds safe region; split this turn')


def background():
    im = Image.new('RGB', (1280, 720), 'white')
    art = Image.open(ASSETS / 'conversation-characters.png').convert('RGB')
    im.paste(art.resize((1066, 600), Image.Resampling.LANCZOS), (107, 120))
    return im


def center(draw, text, y, face, color=INK):
    draw.text((640, y), text, font=face, fill=color, anchor='mt')


def lesson_frame(bg, lesson, turn, fraction, practice=False):
    im = bg.copy()
    d = ImageDraw.Draw(im)
    d.rectangle((366, 180, 914, 409), fill='white')
    # Header stays above the characters; no opaque boxes over their heads or hands.
    center(d, 'ENGLISH CONVERSATION', 24, font(15), MUTED)
    title_font, title_lines = fit_text(d, lesson['topic'], 1000, 1, 35, 20)
    center(d, title_lines[0], 53, title_font)
    center(d, f"{lesson['level']}  /  LISTEN • REPEAT • SPEAK", 104, font(14), MUTED)
    d.rounded_rectangle((545, 191, 735, 228), radius=18, fill='#EEF5F4')
    center(d, 'SPEAK NOW' if practice else turn['section'], 201, font(15), TEAL)
    face, rows = fit_text(d, turn['text'], 548)
    center(d, 'YOUR TURN' if practice else turn['speaker'].upper(), 269, font(15), TEAL)
    for index, row in enumerate(rows):
        center(d, row, 309 + index * 49, face)
    if practice or turn['section'] == 'REPEAT':
        center(d, 'Say it out loud' if practice else 'Listen, then repeat', 430, font(18), MUTED)
    for name, x in [('Alex', 170), ('Emma', 1110)]:
        active = name == turn['speaker'] and not practice
        d.rounded_rectangle((x-60, 646, x+60, 682), radius=18, fill=TEAL if active else '#EEF2F2')
        d.text((x, 653), name, font=font(17), fill='white' if active else MUTED, anchor='mt')
    d.rectangle((450, 695, 830, 698), fill='#E6ECEB')
    d.rectangle((450, 695, 450 + int(380 * min(1, max(0, fraction))), 698), fill=TEAL)
    return im


def thumbnail(lesson, output, variant_key=None):
    """Variable headline using bundled condensed display font, outlines and extrusion.

    Character is a genuine alpha cutout; never crop hands to a rectangular column.
    """
    im = Image.new('RGBA', (1280, 720), '#FFFF00')
    female = Image.open(ASSETS / 'emma-thumbnail.png').convert('RGBA')
    bounds = female.getchannel('A').point(lambda value: 255 if value > 128 else 0).getbbox()
    if bounds is None:
        raise ValueError('Empty thumbnail character asset')
    female = female.crop(bounds)
    variant = int(hashlib.sha256(str(variant_key or lesson).encode()).hexdigest()[:8],16)
    female.thumbnail((490, 654), Image.Resampling.LANCZOS)
    im.alpha_composite(female, (1280 - female.width - 28, 720 - female.height - 16))
    # Three cohesive angled lines, black face, white outline, hard black extrusion.
    stack = Image.new('RGBA', (780, 485))
    d = ImageDraw.Draw(stack)
    y = 16
    for line in lesson['headline']:
        size = 150
        while d.textbbox((0, 0), line, font=font(size, True))[2] > 680 and size > 38:
            size -= 1
        face = font(size, True)
        box = d.textbbox((0, 0), line, font=face)
        h = box[3] - box[1]
        for shift in range(10, 0, -1):
            d.text((22+shift, y-box[1]+shift), line, font=face, fill='black', stroke_width=7, stroke_fill='black')
        d.text((22, y-box[1]), line, font=face, fill='black', stroke_width=6, stroke_fill='white')
        y += max(130, h + 23)
    stack = stack.rotate(2 + variant % 3, Image.Resampling.BICUBIC, expand=True)
    im.alpha_composite(stack, (22, 40))
    d = ImageDraw.Draw(im)
    d.polygon([(55, 553), (732, 525), (717, 590), (42, 618)], fill='black')
    d.text((73, 556), 'LISTEN. REPEAT. SPEAK.', font=font(44, True), fill='#FFFF00')
    d.text((53, 657), f"REAL-LIFE CONVERSATION • {lesson['level']}", font=font(22, True), fill='black')
    im.convert('RGB').save(output)


class CharacterAnimator:
    """Python/Pillow puppet animation: breathing, sway and audio-reactive nods.

    Reuses the approved art, preserving complete hands. No generated per-frame
    artwork and no lip-sync claim. The two white-background halves have separate
    pivots and never move the subtitles.
    """
    def __init__(self):
        art = Image.open(ASSETS / 'conversation-characters.png').convert('RGB').resize((1066, 600), Image.Resampling.LANCZOS)
        self.puppets = [art.crop((0, 0, 533, 600)).convert('RGBA'), art.crop((533, 0, 1066, 600)).convert('RGBA')]

    def frame(self, seconds, speaker=None, energy=0.):
        im = Image.new('RGB', (1280, 720), 'white')
        for i, puppet in enumerate(self.puppets):
            phase = seconds * 1.7 + i * 1.5
            active = speaker == ('Alex', 'Emma')[i]
            amplitude = min(1., max(0., energy)) if active else 0.
            angle = .6 * math.sin(phase * .65) + amplitude * .9 * math.sin(seconds * 7)
            bob = 2. * math.sin(phase) + amplitude * 3.2 * math.sin(seconds * 8)
            # Small rotations around the bottom-center give a restrained torso sway.
            layer = puppet.rotate(angle, Image.Resampling.BICUBIC, center=(266, 585), fillcolor='white')
            im.paste(layer.convert('RGB'), (107 + i * 533, 120 + round(bob)))
        return im

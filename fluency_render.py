"""White lesson and thumbnail layouts for the illustrated English series."""
from pathlib import Path
import math
import hashlib
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

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


# Outer limbs and every mouth keyframe have white padding in the artwork.
# Independent placement leaves additional clearance for the full motion envelope.
PUPPET_POSITIONS = ((52, 166), (695, 166))


def background():
    return CharacterAnimator().frame(0.)


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
    for name, x in [('Alex', 280), ('Emma', 1010)]:
        active = name == turn['speaker'] and not practice
        d.rounded_rectangle((x-60, 646, x+60, 682), radius=18, fill=TEAL if active else '#EEF2F2')
        d.text((x, 653), name, font=font(17), fill='white' if active else MUTED, anchor='mt')
    d.rectangle((450, 695, 830, 698), fill='#E6ECEB')
    d.rectangle((450, 695, 450 + int(380 * min(1, max(0, fraction))), 698), fill=TEAL)
    return im


def thumbnail(lesson, output, variant_key=None):
    """Place the two illustrated speakers around a centered, topic-specific hook."""
    im = Image.new('RGB', (1280, 720), 'white')
    variant = lesson.get('thumbnail_variant')
    if variant is None:
        variant = int(hashlib.sha256(str(variant_key or lesson).encode()).hexdigest()[:8], 16)
    variant %= 9
    pose_indices = (variant % 3, (variant + variant // 3) % 3)
    for side, (poses, pose_index) in enumerate(zip(CharacterAnimator().poses, pose_indices)):
        puppet = poses[pose_index].convert('RGB')
        white = Image.new('RGB', puppet.size, 'white')
        bounds = ImageChops.difference(puppet, white).convert('L').point(
            lambda value: 255 if value > 22 else 0).getbbox()
        if bounds is None:
            raise ValueError('Empty thumbnail character asset')
        puppet = puppet.crop(bounds)
        scale = min(420 / puppet.width, 745 / puppet.height)
        puppet = puppet.resize((round(puppet.width * scale), round(puppet.height * scale)),
                               Image.Resampling.LANCZOS)
        x = 6 if side == 0 else im.width - 6 - puppet.width
        im.paste(puppet, (x, max(12, im.height - puppet.height)))

    draw = ImageDraw.Draw(im)
    for index, line in enumerate(lesson['headline']):
        face = font(108, True)
        while draw.textlength(line, font=face) > 410:
            face = font(face.size - 1, True)
        draw.text((640, 236 + index * 116), line, font=face,
                  fill='#176B61' if index == 1 else INK, anchor='mm')
    im.save(output)


def mouth_pose(puppet, keyframe, box):
    """Place a real mouth keyframe within a feathered local face region."""
    patch = keyframe.crop(box).convert('RGB')
    width, height = patch.size
    mask = Image.new('L',patch.size)
    ImageDraw.Draw(mask).ellipse((3,3,width-3,height-3),fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    result = puppet.copy()
    result.paste(patch,box[:2],mask)
    return result


class CharacterAnimator:
    """Audio-reactive 2D mouth, breathing, sway and nods built with Python/Pillow.

    Mouth openness follows smoothed audio energy, not individual phonemes. The
    fixed illustrated characters remain recognizable and their hands stay intact.
    """
    def __init__(self):
        art = Image.open(ASSETS / 'conversation-characters.png').convert('RGB').resize((1066, 600), Image.Resampling.LANCZOS)
        puppets = [art.crop((0, 0, 533, 600)).convert('RGBA'), art.crop((533, 0, 1066, 600)).convert('RGBA')]
        resting = Image.open(ASSETS / 'conversation-closed-mouths.png').convert('RGB').resize((1066,600),Image.Resampling.LANCZOS)
        closed = [resting.crop((0,0,533,600)),resting.crop((533,0,1066,600))]
        speaking = Image.open(ASSETS / 'conversation-speaking-mouths.png').convert('RGB').resize((1066,600),Image.Resampling.LANCZOS)
        middle = [speaking.crop((0,0,533,600)),speaking.crop((533,0,1066,600))]
        # Mouth boxes are calibrated to the approved artwork, in puppet pixels.
        geometry = [(185,137,250,185), (291,157,355,205)]
        self.poses = [[mouth_pose(puppet,pose,box) for pose in (rest,half,puppet)]
                      for puppet,rest,half,box in zip(puppets,closed,middle,geometry)]

    def frame(self, seconds, speaker=None, energy=0.):
        im = Image.new('RGB', (1280, 720), 'white')
        for i, poses in enumerate(self.poses):
            phase = seconds * 1.7 + i * 1.5
            active = speaker == ('Alex', 'Emma')[i]
            amplitude = min(1., max(0., energy)) if active else 0.
            articulation = amplitude * (.72 + .28*math.sin(seconds*23))
            puppet = poses[round(articulation*2)]
            angle = .6 * math.sin(phase * .65) + amplitude * .9 * math.sin(seconds * 7)
            bob = 2. * math.sin(phase) + amplitude * 3.2 * math.sin(seconds * 8)
            layer = puppet.rotate(angle, Image.Resampling.BICUBIC, center=(266, 585), fillcolor='white')
            x, y = PUPPET_POSITIONS[i]
            im.paste(layer.convert('RGB'), (x, y + round(bob)))
        return im

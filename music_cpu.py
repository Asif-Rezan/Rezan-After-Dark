"""Render MusicRNN notes as a restrained horror bed, with drones and sparse chimes."""
import json
import math
from pathlib import Path
import subprocess
import wave
import numpy as np

MUSIC_CREDIT = 'Background music: original Magenta MusicRNN composition with synthesized horror drones, sparse minor-key chimes and subtle pulses.'


def render_sequence(sequence, path):
    rate, step = 24000, 60 / 52 / 4
    seconds = float(sequence['totalQuantizedSteps']) * step + 5
    if not 5 < seconds <= 600 or not sequence.get('notes'):
        raise ValueError('Music sequence must contain notes and a bounded duration')
    audio = np.zeros((math.ceil(seconds * rate), 2), dtype=np.float32)
    timeline = np.arange(len(audio), dtype=np.float32) / rate
    # Low A/E foundation, slight detuning and slow swells; no sudden stingers.
    swell = .65 + .35 * np.sin(2 * np.pi * .035 * timeline) ** 2
    for frequency, gain in ((55, .045), (55.18, .025), (82.41, .018), (110, .012)):
        drone = np.sin(2 * np.pi * frequency * timeline) * swell * gain
        audio[:, 0] += drone
        audio[:, 1] += np.sin(2 * np.pi * frequency * timeline + .18) * swell * gain
    pulse = np.exp(-((timeline % 2.8) / .24) ** 2)
    audio += (np.sin(2 * np.pi * 65.41 * timeline) * pulse * .022)[:, None]
    # Revoice model notes into A natural minor, leaving space between events.
    scale = (0, 2, 3, 5, 7, 8, 10)
    for index, note in enumerate(sequence['notes']):
        if index % 4:
            continue
        start = int(int(note['quantizedStartStep']) * step * rate)
        if not 0 <= start < len(audio):
            raise ValueError('Music note outside timeline')
        pitch = int(note['pitch'])
        relative = (pitch - 57) % 12
        degree = min(scale, key=lambda value: abs(value - relative))
        pitch = 57 + degree + (12 if pitch >= 72 else 0)
        count = min(6 * rate, len(audio) - start)
        t = np.arange(count, dtype=np.float32) / rate
        hz = 440 * 2 ** ((pitch - 69) / 12)
        envelope = np.minimum(t / .12, 1) * np.exp(-t / 1.8)
        tone = (np.sin(2 * np.pi * hz * t) + .12 * np.sin(2 * np.pi * hz * 2.01 * t))
        signal = tone * envelope * .035
        pan = .35 + .3 * (index % 3) / 2
        audio[start:start+count, 0] += signal * (1-pan)
        audio[start:start+count, 1] += signal * pan
    for delay, gain in ((.43, .18), (.81, .09)):
        shift = int(delay * rate)
        audio[shift:] += audio[:-shift, ::-1].copy() * gain
    fade = min(5 * rate, len(audio)//2)
    audio[:fade] *= np.linspace(0, 1, fade)[:, None]
    audio[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = float(np.max(np.abs(audio)))
    if not np.isfinite(audio).all() or peak < .001:
        raise RuntimeError('Generated music is silent or invalid')
    audio *= .55 / peak
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((audio * 32767).astype('<i2').tobytes())
    return seconds


def generate(path):
    notes_path = path.with_suffix('.notes.json')
    subprocess.run(['node', str(Path(__file__).with_name('music_rnn.cjs')), str(notes_path)], check=True)
    seconds = render_sequence(json.loads(notes_path.read_text()), path)
    print(json.dumps({'check': 'Generated horror music audio', 'passed': True,
                      'duration_seconds': round(seconds, 2)}), flush=True)


if __name__ == '__main__':
    import sys
    generate(Path(sys.argv[1]))

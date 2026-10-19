"""Render actual MusicRNN-generated notes as a quiet original ambient piano bed."""
import json
import math
from pathlib import Path
import subprocess
import wave
import numpy as np


def generate(path):
    notes_path = path.with_suffix('.notes.json')
    subprocess.run(['node', str(Path(__file__).with_name('music_rnn.cjs')), str(notes_path)], check=True)
    sequence = json.loads(notes_path.read_text())
    rate, step = 24000, 60 / 64 / 4
    seconds = float(sequence['totalQuantizedSteps']) * step + 5
    audio = np.zeros((math.ceil(seconds * rate), 2), dtype=np.float32)
    for index, note in enumerate(sequence['notes']):
        start = int(int(note['quantizedStartStep']) * step * rate)
        length = max(0.16, (int(note['quantizedEndStep']) - int(note['quantizedStartStep'])) * step)
        count = min(int((length + 2) * rate), len(audio) - start)
        t = np.arange(count, dtype=np.float32) / rate
        hz = 440 * 2 ** ((int(note['pitch']) - 69) / 12)
        # Additive electric-piano timbre, no copyrighted samples or soundfonts.
        envelope = np.minimum(t / 0.012, 1) * np.exp(-t / 1.3)
        envelope *= np.exp(-np.maximum(t - length, 0) * 3)
        tone = sum(weight * np.sin(2 * np.pi * hz * harmonic * t)
                   for harmonic, weight in ((1, .8), (2, .15), (3, .035), (4, .015)))
        signal = tone * envelope * .16
        pan = .42 + .16 * (index % 3) / 2
        audio[start:start+count, 0] += signal * (1-pan)
        audio[start:start+count, 1] += signal * pan
    # Soft stereo delay; short fades make looping unobtrusive beneath narration.
    for seconds_delay, gain in ((.31, .22), (.57, .12), (.89, .065)):
        shift = int(seconds_delay * rate)
        audio[shift:] += audio[:-shift, ::-1].copy() * gain
    fade = min(3 * rate, len(audio)//2)
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
    print(json.dumps({'check': 'Generated music audio', 'passed': True, 'duration_seconds': round(seconds, 2)}), flush=True)


if __name__ == '__main__':
    import sys
    generate(Path(sys.argv[1]))

"""Exercise real Kokoro model/tokenizer loading and the production pitch filter."""
import json
from importlib.metadata import version
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf
from voice_cpu import generate


def main():
    with tempfile.TemporaryDirectory(prefix='voice-readiness-') as directory:
        # Two calls also check that the cached pipeline remains usable.
        for index, text in enumerate((
                "The hallway was empty. Then a whisper came from behind the locked door.",
                "I stopped breathing, but the footsteps kept coming closer.")):
            path = Path(directory)/f'check-{index}.wav'
            generate(text, path)
            samples, rate = sf.read(path)
            seconds = len(samples)/rate
            if (rate != 24000 or not 1 <= seconds <= 60
                    or not np.isfinite(samples).all()
                    or np.max(np.abs(samples)) < 0.001):
                raise RuntimeError('Kokoro readiness audio is empty, silent, or invalid')
            print(json.dumps({'check': 'Kokoro narration audio', 'passed': True,
                              'sample_rate': rate, 'seconds': round(seconds, 2)}), flush=True)
    print(json.dumps({'check': 'Narration dependency versions',
                      'versions': {name: version(name) for name in
                          ('kokoro', 'misaki', 'spacy', 'thinc', 'preshed', 'numpy')}}), flush=True)


if __name__ == '__main__':
    main()

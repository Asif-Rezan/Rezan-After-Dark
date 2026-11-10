"""Exercise both production Kokoro voices, with neutral pitch and measured audio."""
import json
import math
from pathlib import Path
import tempfile
import wave
import numpy as np
from voice_cpu import generate


def main():
    with tempfile.TemporaryDirectory(prefix='fluency-voices-') as directory:
        signals=[]
        for name,voice in [('Alex','am_michael'),('Emma','af_heart')]:
            path=Path(directory)/f'{name}.wav'
            generate('Could I have a coffee with oat milk, please?',path,voice=voice,speed=.9,pitch=0.)
            with wave.open(str(path)) as source:
                rate=source.getframerate()
                signal=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype(float)/32768
            length=len(signal)/rate
            if rate!=24000 or not 1<length<15 or not np.isfinite(signal).all() or np.sqrt(np.mean(signal**2))<.001:
                raise RuntimeError(f'{name} voice validation failed')
            signals.append(signal)
            print(json.dumps({'speaker':name,'voice':voice,'seconds':round(length,2),'passed':True}))
        if len(signals[0])==len(signals[1]) and np.array_equal(*signals):
            raise RuntimeError('Two speakers returned identical speech')

if __name__=='__main__': main()

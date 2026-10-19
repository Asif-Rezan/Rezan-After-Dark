import tempfile
import unittest
import wave
from pathlib import Path
import numpy as np
from music_cpu import render_sequence

class HorrorMusicTests(unittest.TestCase):
    def test_horror_bed_is_finite_faded_stereo_and_has_low_drone(self):
        sequence={'totalQuantizedSteps':64,'notes':[
            {'pitch':60+i,'quantizedStartStep':i*4,'quantizedEndStep':i*4+2} for i in range(16)]}
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'music.wav'
            seconds=render_sequence(sequence,target)
            with wave.open(str(target)) as source:
                self.assertEqual((source.getnchannels(),source.getframerate()),(2,24000))
                audio=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').reshape(-1,2)/32768
            self.assertAlmostEqual(len(audio)/24000,seconds,places=3)
            self.assertTrue(np.isfinite(audio).all())
            self.assertLess(np.max(np.abs(audio)),.56)
            self.assertLess(np.max(np.abs(audio[[0,-1]])),.001)
            section=audio[5*24000:9*24000,0]
            power=np.abs(np.fft.rfft(section))**2
            frequency=np.fft.rfftfreq(len(section),1/24000)
            self.assertGreater(power[frequency<150].sum()/power.sum(),.5)

    def test_empty_sequence_is_rejected(self):
        with self.assertRaises(ValueError):
            render_sequence({'totalQuantizedSteps':64,'notes':[]},Path('unused.wav'))

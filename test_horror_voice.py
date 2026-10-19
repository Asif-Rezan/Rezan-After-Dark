import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import numpy as np
from voice_cpu import deepen_audio, voice_settings
from long_video import text_fingerprint
import workflow as w

class HorrorVoiceTests(unittest.TestCase):
    def test_pitch_is_lowered_without_changing_slow_audio_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            source,target=Path(directory)/'source.wav',Path(directory)/'deep.wav'
            w.shell('ffmpeg','-y','-f','lavfi','-i','sine=frequency=220:sample_rate=24000:duration=4',str(source))
            deepen_audio(source,target,-2)
            self.assertLess(abs(w.duration(target)-4),.08)
            with wave.open(str(target)) as stream:
                signal=np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2')
                rate=stream.getframerate()
            frequency=np.fft.rfftfreq(len(signal),1/rate)[np.argmax(abs(np.fft.rfft(signal)))]
            self.assertAlmostEqual(frequency,220*2**(-2/12),delta=2)

    def test_voice_changes_invalidate_audio_cache(self):
        with patch.dict(os.environ,{'CPU_TTS_SPEED':'.82','CPU_TTS_PITCH_SEMITONES':'-2'}):
            original=text_fingerprint('A door opens.')
            self.assertEqual(voice_settings(),(.82,-2))
        with patch.dict(os.environ,{'CPU_TTS_SPEED':'.9','CPU_TTS_PITCH_SEMITONES':'-2'}):
            self.assertNotEqual(original,text_fingerprint('A door opens.'))
        with patch.dict(os.environ,{'CPU_TTS_SPEED':'.82','CPU_TTS_PITCH_SEMITONES':'0'}):
            self.assertNotEqual(original,text_fingerprint('A door opens.'))
        with patch.dict(os.environ,{'CPU_TTS_SPEED':'nan'}):
            with self.assertRaises(ValueError): voice_settings()

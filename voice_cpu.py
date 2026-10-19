"""Free Kokoro English narration and Faster-Whisper word alignment on cloud CPUs."""
import os
import math
from pathlib import Path
import tempfile
import subprocess
import numpy as np

_pipeline = None
_whisper = None


def voice_settings():
    speed = float(os.getenv('CPU_TTS_SPEED', '0.82'))
    pitch = float(os.getenv('CPU_TTS_PITCH_SEMITONES', '-2'))
    if not math.isfinite(speed) or not .7 <= speed <= 1.0:
        raise ValueError('CPU_TTS_SPEED must be between 0.7 and 1.0')
    if not math.isfinite(pitch) or not -4 <= pitch <= 0:
        raise ValueError('CPU_TTS_PITCH_SEMITONES must be between -4 and 0')
    return speed, pitch


def deepen_audio(source, target, semitones):
    factor = 2 ** (semitones / 12)
    # Lower pitch while preserving the slow narration's measured duration.
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source),
        '-af', f'asetrate=24000*{factor:.12f},aresample=24000,atempo={1/factor:.12f}',
        '-ar', '24000', '-c:a', 'pcm_s16le', str(target)], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def generate(text, path):
    global _pipeline
    import soundfile as sf
    import torch
    torch.set_num_threads(2)
    if _pipeline is None:
        from kokoro import KPipeline
        _pipeline = KPipeline(lang_code='a', device='cpu', repo_id='hexgrad/Kokoro-82M')
    speed, pitch = voice_settings()
    parts = [audio.numpy() for _, _, audio in _pipeline(text,
        voice=os.getenv('CPU_TTS_VOICE', 'am_michael'), speed=speed, split_pattern=r'\n+')]
    if not parts:
        raise RuntimeError('Kokoro returned no narration')
    with tempfile.TemporaryDirectory(prefix='horror-voice-') as directory:
        raw = Path(directory)/'raw.wav'
        sf.write(str(raw), np.concatenate(parts), 24000)
        deepen_audio(raw, path, pitch)


def transcribe_audio(path):
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel('base.en', device='cpu', compute_type='int8', cpu_threads=2)
    import subprocess
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
        '-f', 'f32le', '-ac', '1', '-ar', '16000', 'pipe:1'],
        capture_output=True, check=True)
    audio = np.frombuffer(decoded.stdout, dtype='<f4').copy()
    segments, info = _whisper.transcribe(audio, language='en', word_timestamps=True,
        beam_size=3, vad_filter=False)
    words = [dict(word=w.word, start=w.start, end=w.end) for segment in segments for w in segment.words or []
        if w.end > w.start]
    if not words:
        raise RuntimeError('CPU transcription returned no aligned words')
    return words

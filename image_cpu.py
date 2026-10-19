"""Download public model weights and generate images on a cloud CPU runner.

This does not call Hugging Face Inference Providers and consumes no inference
API credits. The default model revision is pinned for reproducibility.
"""
import argparse
import os
from pathlib import Path
import time
import re


class ImageSafetyBlocked(RuntimeError):
    """The model's safety checker rejected a generated image."""


def safer_prompt(prompt):
    """Keep the scene's setting and props while removing graphic/sexual details."""
    replacements = [
        (r'\b(?:bloodied|bloody|gory|gore|bleeding|bloodstains?)\b', 'dramatic'),
        (r'\b(?:corpse|dead body|mutilated body)\b', 'unoccupied place'),
        (r'\b(?:nude|naked|undressed|lingerie|underwear)\b', 'fully clothed'),
        (r'\b(?:strangling|stabbing|shooting|beating|torturing|assaulting)\b', 'confronting'),
        (r'\b(?:knife|gun|pistol|weapon)\b', 'personal belongings'),
        (r'\b(?:sexual assault|rape|abuse)\b', 'tense disagreement'),
        (r'\b(?:wound|injury|bruises?)\b', 'signs of distress'),
    ]
    for pattern, replacement in replacements:
        prompt = re.sub(pattern, replacement, prompt, flags=re.I)
    # Preserve the scene's visual anchor before the encoders' token limit.
    return (prompt + ' Safe non-graphic horror illustration; fully clothed people, '
            'no violence or exposed bodies.')

def recovery_prompt(prompt):
    """Reframe the same setting and props as an unoccupied evidence scene."""
    scene = re.sub(r'\b(?:people|person|man|woman|men|women|crowd|workers|soldiers|ruler|child|children)\b',
                   'eerie setting', prompt, flags=re.I)
    return safer_prompt('Unoccupied eerie location and story props, atmospheric horror view. '
                        + scene + ' Focus on architecture, tools and landscape; no people depicted.')


# Previous image model, retained for easy restoration:
# MODEL = 'Lykon/dreamshaper-8'
# REVISION = 'a7e52b98680b1ba8ff7bce97c7f9f2e2e5337917'
MODEL = 'segmind/SSD-1B'
REVISION = '60987f37e94cd59c36b1cba832b9f97b57395a10'
_pipeline = None
def generate(prompt, path):
    global _pipeline
    import torch
    from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler
    torch.set_num_threads(min(os.cpu_count() or 2, 4))
    model = os.getenv('CPU_IMAGE_MODEL') or MODEL
    revision = os.getenv('CPU_MODEL_REVISION') or (REVISION if model == MODEL else 'main')
    if _pipeline is None:
        print('Loading public image-model weights on CPU', flush=True)
        # Pinned public SSD-1B safetensors on CPU. Keep the model's safety checker.
        _pipeline = DiffusionPipeline.from_pretrained(model, revision=revision,
            torch_dtype=torch.float32, use_safetensors=True, trust_remote_code=False)
        _pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
            _pipeline.scheduler.config, algorithm_type='dpmsolver++', use_karras_sigmas=True)
        _pipeline.to('cpu')
        _pipeline.enable_attention_slicing()
        _pipeline.enable_vae_slicing()
    width = int(os.getenv('CPU_IMAGE_WIDTH', '1024'))
    height = int(os.getenv('CPU_IMAGE_HEIGHT', '576'))
    steps = int(os.getenv('CPU_IMAGE_STEPS', '16'))
    if width % 8 or height % 8 or not 256 <= width <= 1536 or not 256 <= height <= 1536:
        raise ValueError('CPU image dimensions must be multiples of eight in 256–1536')
    if not 1 <= steps <= 50:
        raise ValueError('CPU generation requires 1–50 steps')
    pipeline = _pipeline
    start = time.monotonic()
    seed = int(os.getenv('IMAGE_SEED', '42'))
    image = None
    initial_prompt = safer_prompt(prompt)
    rewritten = recovery_prompt(prompt)
    for attempt in range(10):
        # Five seeds per composition; keep input sanitization and output checks.
        candidate = initial_prompt if attempt < 5 else rewritten
        if attempt == 5:
            print('Five safety rejections; switching to an environmental reconstruction.', flush=True)
        result = pipeline(prompt=candidate, negative_prompt='anime, cartoon, watercolor, flat colors, line art, cel shading, blurry, low detail, poor quality, 3D render, CGI, extra arms, extra legs, deformed hands, duplicate person, text, letters, watermark, logo, ' + os.getenv('IMAGE_NEGATIVE_PROMPT', ''), width=width, height=height,
            num_inference_steps=steps, guidance_scale=9.0,
            generator=torch.Generator(device='cpu').manual_seed(seed + attempt * 10007))
        if any(getattr(result, 'nsfw_content_detected', None) or []):
            print(f'Image safety check rejected attempt {attempt+1}/10.', flush=True)
            continue
        image = result.images[0]
        break
    if image is None:
        raise ImageSafetyBlocked('Image blocked after five initial and five rewritten-prompt attempts; preserve run for review')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    print(f'CPU image generated: {image.width}x{image.height}; {time.monotonic()-start:.1f}s', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.prompt, args.output)

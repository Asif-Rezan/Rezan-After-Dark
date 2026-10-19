"""Free local CLIP comparison of object/group scenes against generic portrait frames."""
_model = None
_processor = None


def check_scene(image, shot):
    global _model, _processor
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    required = shot.get('focus') in ('object', 'group', 'person')
    if not required:
        return {'passed': True, 'required': False, 'method': 'narration-plan-and-sample-review'}
    if _model is None:
        _model = CLIPModel.from_pretrained('openai/clip-vit-base-patch32', trust_remote_code=False).eval()
        _processor = CLIPProcessor.from_pretrained('openai/clip-vit-base-patch32', trust_remote_code=False)
    target = shot['semantic_target']
    alternatives = ['A generic studio portrait unrelated to the horror scene.',
                    'An unrelated modern person standing in an empty room.',
                    'An empty landscape without the described story action.']
    with Image.open(image) as picture:
        inputs = _processor(text=[target]+alternatives, images=picture.convert('RGB'),
                            return_tensors='pt', padding=True, truncation=True)
    with torch.inference_mode():
        scores = _model(**inputs).logits_per_image[0].tolist()
    passed = scores[0] > max(scores[1:])
    return {'passed': passed, 'required': True, 'method': 'CLIP-scene-versus-portrait',
            'scene_score': scores[0], 'alternative_score': max(scores[1:])}

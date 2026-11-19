"""Ranking with CLIP (openai/clip-vit-base-patch32, MIT, about 0.6 GB).

text_scores   how well each candidate matches the prompt  -> pick the best of N takes
image_scores  how alike each image is to a reference      -> pick the take that stays
              closest to the character (turnarounds, animation frames) and flag drift

CLIP runs on the CPU so it never competes with ComfyUI for VRAM. Candidates are scored
composited on mid grey, so transparency does not confuse it. CLIP judges content, not
craft: it cannot see a bad hand or a smeared edge, so the checks in sprite.py and a look
by a person are still needed.
"""
import os

import numpy as np

from .config import FORGE_ROOT, log

CLIP_MODEL = "openai/clip-vit-base-patch32"
_state = {}


def available():
    try:
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _load():
    if "model" not in _state:
        from transformers import CLIPModel, CLIPProcessor
        log(f"loading {CLIP_MODEL} for scoring ...")
        cache = os.path.join(FORGE_ROOT, "models", "hf_cache")
        _state["processor"] = CLIPProcessor.from_pretrained(CLIP_MODEL, cache_dir=cache)
        _state["model"] = CLIPModel.from_pretrained(CLIP_MODEL, cache_dir=cache).eval()
    return _state["processor"], _state["model"]


def _feat(out):
    out = out.pooler_output if hasattr(out, "pooler_output") else out
    return out / out.norm(dim=-1, keepdim=True)


def _images(rgbas):
    from PIL import Image

    from .sprite import on_background
    return [Image.fromarray((np.clip(on_background(r), 0, 1) * 255).astype(np.uint8), "RGB") for r in rgbas]


def text_scores(rgbas, text):
    import torch
    processor, model = _load()
    with torch.no_grad():
        t = processor(text=[text[:300]], return_tensors="pt", padding=True, truncation=True)
        i = processor(images=_images(rgbas), return_tensors="pt")
        te, ie = _feat(model.get_text_features(**t)), _feat(model.get_image_features(**i))
        return [round(float(s), 4) for s in (ie @ te.T).squeeze(-1)]


def image_scores(rgbas, reference):
    import torch
    processor, model = _load()
    with torch.no_grad():
        i = processor(images=_images(list(rgbas) + [reference]), return_tensors="pt")
        f = _feat(model.get_image_features(**i))
        return [round(float(s), 4) for s in (f[:-1] @ f[-1:].T).squeeze(-1)]

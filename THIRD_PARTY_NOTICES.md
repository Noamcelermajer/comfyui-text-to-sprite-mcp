# Third-party models and software

Sprite Forge's own code is MIT-licensed. It downloads and drives the following, each under
its own licence. Every model it installs allows commercial use of what you make with it;
check the licences yourself before shipping a commercial game.

| Component | Used by | Licence |
| :--- | :--- | :--- |
| FLUX.2 [klein] 4B (Black Forest Labs), fp8 | sprite, icon, pixel_sprite, character, turnaround, animation, tile, vfx | Apache 2.0 |
| Qwen3 4B text encoder (Alibaba Qwen; ComfyUI repackage by Comfy-Org) | FLUX.2 Klein 4B, Z-Image Turbo | Apache 2.0 |
| FLUX.2 VAE | FLUX.2 Klein 4B | Apache 2.0 |
| Pixel Art Sprite LoRA for FLUX.2-klein-4B (Limbicnation; trained on CC0 data per its card) | pixel_sprite, pixel_character, pixel sheets | Apache 2.0 |
| Z-Image Turbo (Tongyi-MAI), int8 | portrait, `max` presets | Apache 2.0 |
| Qwen-Image-Edit 2511 (Alibaba Qwen), int8; Qwen2.5-VL 7B encoder; Qwen-Image VAE | turnaround / animation `max` | Apache 2.0 |
| Qwen-Image-Edit-2511 Lightning 4-step LoRA (lightx2v) | turnaround / animation `max` | Apache 2.0 |
| BiRefNet (ZhengPeng7; ComfyUI repackage by Comfy-Org) | background removal | MIT |
| CLIP ViT-B/32 (OpenAI) | ranking takes and frames | MIT |
| ComfyUI | everything | GPL-3.0 (run as a separate program) |
| NumPy, SciPy, Pillow | image processing | BSD / HPND |

The pixel-art grid recovery in `spriteforge/pixel.py` is an independent implementation of
the approach used by Pixel Snapper (Hugo-Dz, MIT) and proper-pixel-art (MIT); no code was
copied. The named palettes are the published PICO-8, Game Boy, NES, Endesga 32 and
Sweetie 16 colour sets.

Deliberately **not** used, because their licences do not allow commercial use:
Qwen-Image 2.1 (Qwen Research License), FLUX.2 [klein] 9B (FLUX non-commercial licence),
BRIA RMBG-2.0 (CC BY-NC), the Pixelization GAN of Wu et al. (research only).

Stores: some marketplaces ban AI-generated assets and Steam and itch.io ask you to
disclose them. Check the rules of where you publish.

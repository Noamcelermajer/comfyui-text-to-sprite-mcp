# Sprite Forge - local text-to-2D game art for Claude, Codex and any MCP client

**Turn a sentence into game-ready 2D art on your own GPU.** Free, private and offline:
transparent sprites and icons, **true pixel art** (a real pixel grid and a locked palette, not
just a pixel "look"), characters with 4/8-direction turnarounds and animation sprite sheets,
seamless tiles, portraits and VFX frames - driven by your coding agent or the command line.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP server](https://img.shields.io/badge/MCP-server-blueviolet)](https://modelcontextprotocol.io)
[![Agent skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-orange)](skills/sprite-forge/SKILL.md)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-0.45%2B-informational)](https://github.com/comfyanonymous/ComfyUI)

> "make a 32-pixel knight for my platformer, then give him a walk cycle" &rarr; your agent
> creates the character, renders the walk frames from it, snaps every frame to one pixel grid
> and one palette, and hands back a sprite sheet with a JSON atlas your engine imports.

Part of a set: [Asset Forge](https://github.com/Noamcelermajer/comfyui-text-to-3d-mcp) (3D models)
and [Sound Forge](https://github.com/Noamcelermajer/comfyui-text-to-audio-mcp) (audio) use the same
profile / preset / skill design.

**Why Sprite Forge**

- **No credits, no cloud.** Everything runs on your own NVIDIA card.
- **Commercially clean by default.** Every model it installs is Apache 2.0 or MIT. Popular
  alternatives that are *not* (Qwen-Image 2.1, FLUX.2 Klein 9B, RMBG-2.0) are left out on purpose.
- **Game-ready, not just generated.** BiRefNet cut-out, halo removal (defringe), crop, canvas
  and padding, feet-on-the-ground alignment for characters, sprite sheets with
  TexturePacker/Phaser JSON atlases (Phaser, Godot via Sprite Atlas Importer, Unity importers).
- **Real pixel art.** Models only draw pixel-art *style* - blocks of uneven size, blurred
  edges, hundreds of colours. Sprite Forge recovers the grid the way the best open
  "unfakers" do (edge-profile cell detection with drift-following boundaries, dominant
  colour per cell, OKLab palette matching, orphan cleanup, hard alpha), with named palettes
  (PICO-8, Game Boy, NES, Endesga 32, Sweetie 16) or **one frozen palette for your whole game**.
- **Characters stay themselves.** A character is created once and saved; turnarounds and
  animations are reference edits of it, ranked by CLIP likeness to the reference.
- **Best of N.** Several takes per call, ranked by prompt match (CLIP) and by the checks
  (cut off, several objects, background left in, pixel-grid quality); the rest are kept.
- **Fast / standard / max presets** and **hardware tiers** (8 / 12 / 16 / 24 GB).
- **Swap models without code.** Each kind of asset is a small JSON profile around a ComfyUI graph.

## What it makes

| Profile | For | Model | Output |
| :--- | :--- | :--- | :--- |
| `sprite` | props, items, weapons, furniture, UI pieces | FLUX.2 Klein 4B | transparent PNG on a square canvas |
| `icon` | skill / item icons, badges, buttons | FLUX.2 Klein 4B | PNG + 256/128/64 px |
| `pixel_sprite` | pixel-art characters, items, enemies | Klein 4B + pixel-art LoRA | real 16-64 px pixel art, hard alpha, limited palette, x8 preview |
| `character` / `pixel_character` | a reusable character reference | Klein 4B (+ LoRA) | full-body transparent PNG |
| `turnaround` | 4 or 8 facing directions | Klein 4B edit (max: Qwen-Image-Edit 2511) | frames + sheet + atlas |
| `animation` | walk, run, idle, attack, jump, hurt, death, cast | Klein 4B edit (max: Qwen-Image-Edit 2511) | frames + sheet + atlas with an animation tag |
| `tile` | ground, walls, floors, water, foliage | Klein 4B (max: Z-Image Turbo) | seamless texture + 3x3 preview, optional pixel version |
| `portrait` | dialogue portraits, cards, key art | Z-Image Turbo | full-resolution image |
| `vfx` | explosions, smoke, magic bursts | Klein 4B + edits | frames + sheet + atlas, brightness as alpha |

Sheets can be pixel art too: pass `pixel_grid` (e.g. 32) and every frame is converted with
**one** pixel size and **one** palette, so the character never changes colour between frames.

## Status

Everything is built and the image processing is covered by offline tests
(`tests/test_offline.py`: cut-out, defringe, canvas, pixel-grid recovery - 100% of pixels of a
distorted 24x32 test sprite recovered, where a plain resize gets 67% - palettes, seamless
tiles, sheets). **GPU timings per profile are being measured now** and will be added here;
turnaround, animation and vfx are marked experimental until then.

## Requirements

- NVIDIA GPU with 8 GB of VRAM or more (16 GB tested), Windows or Linux
- ComfyUI 0.45 or newer (portable build is fine) and git; no custom node packs
- About 25 GB of free disk for the default models (18 GB download), 55 GB with `--all`

| VRAM | What you get |
| :--- | :--- |
| 8 GB | Everything at `standard`, at most 2 takes; turnaround/animation `max` stays on Klein (the 20B edit model does not fit). |
| 12 GB | Everything; the Qwen-Image-Edit `max` presets run with offloading, one take per frame. |
| 16 GB | Everything (reference machine, RTX 5080). |
| 24 GB+ | Everything, with room for more takes. |

## Install

The easiest way: paste [SETUP_PROMPT.md](SETUP_PROMPT.md) into Claude Code or Codex. By hand:

```
git clone https://github.com/Noamcelermajer/comfyui-text-to-sprite-mcp SpriteForge
cd SpriteForge
python forge.py setup        # models for this GPU (--dry-run to preview, --all for Qwen-Image-Edit)
python forge.py doctor       # every profile should be "ready": true
python forge.py connect      # registers the MCP server and the skill for Claude and Codex
```

Sprite Forge finds ComfyUI next to it or through `SPRITEFORGE_COMFY`, starts it when needed,
and hands over to ComfyUI's Python for the image libraries. Full unattended instructions
for agents: [AGENTS.md](AGENTS.md).

## Use

From an agent: just ask ("make a 32x32 pixel-art slime enemy with the PICO-8 palette"). The
skill tells it which profile, parameters and prompt style to use, and every result comes back
with a preview image the agent looks at. The MCP server has 7 tools: `list_profiles`,
`generate_sprite`, `create_character`, `get_job`, `check_image`, `models`, `forge_status`.

From the command line (every command prints one JSON object):

```
python forge.py sprite potion "a glowing blue mana potion in a round glass flask" --project ../MyGame
python forge.py sprite slime "a cute green slime with big eyes" -p pixel_sprite --set grid=24 --set palette=pico8
python forge.py character ranger "a young ranger with red hair, green hooded cloak, leather armour, a longbow"
python forge.py sprite ranger_dirs -p turnaround --character ranger --set directions=8
python forge.py sprite ranger_walk -p animation --character ranger --set action=walk --set facing=right
python forge.py sprite grass "short green grass with small flowers" -p tile --set pixelize=true
python forge.py sprite boom "fiery orange explosion with black smoke" -p vfx --set frames=6
python forge.py check some_sprite.png --kind sprite
```

Exit code 0 = ok, 2 = written but failed its check (see `problems`), 1 = error.

## Customising

- **Per game:** `python forge.py init <game folder>` writes a `spriteforge.json` with the
  output folder, characters folder, style and per-profile settings. Set
  `{"pixel_sprite": {"params": {"palette": "project"}}}` to give the whole game one palette
  (`palette.hex`, Lospec format - you can also drop in your own).
- **Per call:** `--set key=value` / `params` (`size`, `candidates`, `view`, `grid`, `palette`,
  `outline`, `directions`, `action`, `facing`, `pixel_grid`, ...). `forge.py profiles` explains
  every parameter.
- **New profile:** a JSON file in `profiles/user/` or the game's `spriteforge_profiles/`
  that `extends` an existing one.
- **New model:** add it to `models/catalog.json` and write a profile around a ComfyUI API
  graph in `graphs/`.

Settings are layered: profile, hardware tier, project, preset, style, call; the tier's
limits are applied last so a call cannot run an 8 GB card out of memory.

## Licences

Sprite Forge's code is MIT. Every model it installs allows commercial use of your outputs
(FLUX.2 Klein 4B, Z-Image Turbo, Qwen-Image-Edit 2511, the pixel-art LoRA: Apache 2.0;
BiRefNet, CLIP: MIT). Details in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Some stores
ban or require disclosure of AI-generated assets - check where you publish.

Not to be confused with other projects named "SpriteForge".

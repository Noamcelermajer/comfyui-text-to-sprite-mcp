---
name: sprite-forge
description: Generate 2D game art locally and for free - sprites, props, items, icons and UI elements with transparent backgrounds, true pixel art (real grid, limited palette), characters with 4/8-direction turnarounds and animation sprite sheets (walk, run, idle, attack...), animating an existing pixel sprite from a description, normal maps for 2D lighting, seamless tiles, portraits and VFX frames - as game-ready PNGs, GIFs and TexturePacker/Phaser JSON atlases. Use whenever the user asks to create, generate, regenerate, animate or fix a sprite, 2D asset, pixel art, icon, sprite sheet, character sheet, walk cycle, tile or texture, portrait, effect animation or normal map, or to check a PNG.
---

# Sprite Forge

Sprite Forge turns text into game-ready 2D assets on this machine's GPU (ComfyUI underneath;
it is started automatically). Nothing is sent to a paid service. Every result is cut out with
a clean edge, cropped, sized, checked, and saved with a manifest; several takes are made and
the best one is kept.

## How to call it

Prefer the **`sprite-forge` MCP tools** when they are available:
`list_profiles`, `generate_sprite`, `create_character`, `animate_pixel`, `make_normal_map`, `get_job`, `check_image`, `models`, `forge_status`.

Otherwise use the command line from the Sprite Forge folder; every command prints one JSON object:

```
python forge.py profiles
python forge.py sprite  <name> "<prompt>" -p sprite --project <game folder>
python forge.py sprite  <name> "<prompt>" -p pixel_sprite --set grid=32 --set palette=project --project <game folder>
python forge.py character <id> "<who, outfit, colours, props>" --project <game folder>
python forge.py sprite  <name> -p turnaround --character <id> --set directions=8 --project <game folder>
python forge.py sprite  <name> -p animation  --character <id> --set action=walk --set facing=right --project <game folder>
python forge.py animate <name> <sprite.png> "<the motion>" --pose "<frame 1>" --pose "<frame 2>" ...
python forge.py normal  <sprite.png>
python forge.py check   <file.png> --kind sprite
```

Exit code 0 = ok, 2 = finished but the result failed its check, 1 = error.

## Pick the profile

| Profile | For | Output |
| :--- | :--- | :--- |
| `sprite` | props, items, pickups, weapons, furniture, UI pieces | transparent PNG, cropped, centred on a square canvas |
| `icon` | skill / item icons, badges, buttons | square PNG plus 256/128/64 px versions |
| `pixel_sprite` | true pixel art: characters, items, enemies | real pixel grid (e.g. 32 px), hard alpha, limited or named palette, plus an x8 preview |
| `character` | a reusable character reference | full-body transparent PNG, feet on the ground line |
| `pixel_character` | the same, drawn in pixel-art style | reference for pixel-art sheets |
| `turnaround` | the character facing 4 or 8 directions | frames + `<name>_sheet.png` + `<name>.atlas.json` |
| `animation` | walk, run, idle, attack, jump, hurt, death, cast | frames + sheet + atlas with an animation tag |
| `tile` | ground, walls, floors, water, foliage | seamless square texture + a 3x3 tiled preview (optional pixel version) |
| `portrait` | dialogue portraits, cards, key art | full-resolution image |
| `vfx` | explosions, smoke, magic bursts, hit sparks | frames + sheet + atlas; brightness is transparency |
| `animate_pixel` (tool) | animate an EXISTING pixel sprite from a description | frames on the sprite's own grid and palette + sheet + atlas + GIF |

Every sheet also gets `<name>.gif` (loops) so the user can watch it.

Each has three presets: `fast` (one quick take), `standard` (the default), `max` (bigger
model or more takes; Qwen-Image-Edit for turnarounds/animation). Say which preset you used.

## Workflow

1. **Always pass the game project folder** (`project_dir` / `--project`). Its
   `spriteforge.json` supplies the style, output folder, characters folder and per-profile
   settings, so a game's art stays consistent. If it has none, offer to create one
   (`forge.py init <dir>`).
2. **Write the prompt the way the profile's `prompt_guide` says** (`list_profiles` returns
   it). Describe the thing, not the picture: no "a sprite of", no background, no camera words
   in the prompt - set the angle with `params.view` (`front view`, `side view`,
   `top-down view`, `isometric view`).
3. **Characters that appear more than once: create them first.** `create_character` (or
   the `character` profile with `character` set) saves a reference; turnarounds and
   animations are edits of it, so the character keeps one look. For pixel-art sheets use
   `profile: "pixel_character"` and pass `params.pixel_grid` (e.g. 32) to the sheet.
4. **Longer jobs are asynchronous.** If a tool returns a job id, call `get_job` with
   `wait_seconds: 50` until the status is not `queued` or `running`. One job at a time.
5. **Look at the preview.** Every result comes with a preview image (sprites on a
   checkerboard). Check it before calling the asset done: wrong subject, a cut-off part, a
   second object, a halo, a frame that is not the same character. Report the file, its size,
   any `problems`, and mention the other takes in `.spriteforge/<name>/`.

## Pixel art

- Image models only draw pixel-art *style*; Sprite Forge makes it real: it finds the
  model's pixel grid (or forces `grid`), takes the dominant colour of each cell, removes
  stray pixels, makes alpha fully on/off and limits the palette. Never claim a sprite is
  pixel art without this step.
- `grid`: the sprite's longest side in pixels (16, 24, 32, 48, 64). `"auto"` keeps the pixel
  size the model drew.
- `palette`: `pico8`, `gameboy`, `nes`, `endesga32`, `sweetie16`, a list of hex colours, or
  **`"project"`** - one palette frozen for the whole game (saved as `palette.hex` in the
  project by the first pixel asset, reused by every later one). Use `"project"` for a game.
- `outline: true` adds a 1-pixel dark outline; `canvas: 32` puts the sprite on a 32x32 grid.
- No dithering is applied (it flickers in animation).

## Animate an existing pixel sprite

Use `animate_pixel` when the user already has a pixel sprite (their own, or one made with
`pixel_sprite`) and describes a motion: "make the slime bounce", "animate the knight swinging
his sword". It keeps the sprite's exact pixel size and palette in every frame.

- `image`: the PNG (native size up to 256 px, or an enlarged copy with hard pixels).
- `description`: the motion. walk / run / idle / attack / jump / hurt / death / cast use
  built-in key poses.
- **Write `poses` yourself for anything else**: one short line per frame saying what changes,
  e.g. for a sword swing `["raising the sword high above his head", "the sword at the top,
  body leaning back", "swinging the sword down in front of him", "the sword low after the
  strike"]`. 3-6 frames read best. These are key poses, not in-betweens.
- `params.include_original: true` puts the untouched sprite in as frame 0; `params.fps`.
- Look at the GIF / preview: if a frame lost part of the sprite or changed it, re-run with a
  new `seed` or `preset: "max"` (more takes per frame).

## Normal maps (2D lighting)

For games with 2D lights (Godot `CanvasTexture` + `Light2D`, Unity 2D lights, Phaser
light2d), pass `params.normal_map: true` to sprite, icon, pixel_sprite, character,
turnaround, animation or animate_pixel, or call `make_normal_map` on any existing PNG.

- Writes `<name>_normal.png` (or `<name>_sheet_normal.png`, same layout as the sheet) and
  `<name>_lit_preview.png` (the sprite lit from left, top, right and bottom) - show the user
  the preview.
- Pixel art keeps its pixel grid with crisp, snapped normals.
- Default convention is OpenGL (green = up), which Godot and Unity expect; `normal_y: "down"`
  / `y: "down"` for DirectX. `normal_strength` 0.5 subtle - 2 strong.
- The relief is inferred from the drawing (outline, colour regions, highlights), not modelled:
  it reads well under moving lights but is not a real depth scan.

## When the result says `failed_check`

The file is written but should not be used as is. `problems` explains why.

| Problem | Fix |
| :--- | :--- |
| `cut off` | Re-run with a new `seed`; add "small, centred, with space around it" or raise `params.padding`. |
| `several objects` | Name one object only; re-run with a new seed. |
| `background not removed` / `empty` | Describe a solid object; keep `background` plain; re-run. |
| `may not look like the character` (sheets) | Re-run that sheet with a new seed or `preset: "max"`; keep the character prompt simple. |
| `may not have turned` (turnaround) | Re-run with `preset: "max"` (Qwen-Image-Edit follows view changes better). |
| `no clear pixel grid found` | The picture was not drawn as pixel art: set `grid` to a number, or re-run. |
| `visible seam` (tile) | Use an organic surface or raise `params.blend`; re-run. |
| `much smaller than the original` (animate_pixel) | That frame lost part of the sprite: re-run with a new seed or give a clearer pose. |
| `flat: almost a single colour` | The model drew a silhouette: re-run with a new seed; say "in full colour". |

Same `name` + same settings returns the existing file; change a setting, the seed, or pass
`force: true` to make a new one.

## Customising

- Per project: `spriteforge.json` (`style`, `style_preset`, `output_dir`, `characters_dir`,
  `profiles.<id>.params`, e.g. `{"pixel_sprite": {"params": {"grid": 32, "palette": "project"}}}`).
- Per call: `params`, e.g. `{"size": 256, "candidates": 6, "view": "side view", "directions": 4,
  "action": "attack", "facing": "down"}`. `list_profiles` lists every parameter with an explanation.
- New profile: a small JSON file in the project's `spriteforge_profiles/` that `extends` one.
- Models and hardware: the `models` tool reports what each profile and preset needs and
  what is missing. Downloads are large - ask the user before installing.
- Licences: every model Sprite Forge installs allows commercial use of what you make
  (FLUX.2 Klein 4B, Z-Image Turbo, Qwen-Image-Edit 2511 and the pixel LoRA: Apache 2.0;
  BiRefNet and CLIP: MIT). Some stores (e.g. Steam, itch.io) ask you to disclose AI-made assets.

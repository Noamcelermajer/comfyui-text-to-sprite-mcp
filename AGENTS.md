# Sprite Forge - instructions for AI agents

This file is for an AI agent (Claude, Codex, or any other) asked to install or use Sprite
Forge. Everything here can be done without asking a human, except where it says to ask.
Every `forge.py` command prints one JSON object on stdout; read it to decide the next step.

- **Installing?** Follow [Install](#install-unattended) from top to bottom.
- **Already installed and asked to make a sprite?** Read [`skills/sprite-forge/SKILL.md`](skills/sprite-forge/SKILL.md).
- **Changing profiles or adding a model?** Read the "Customising" section of [`README.md`](README.md).

## What gets installed

| Layer | What | Installed by |
| :--- | :--- | :--- |
| Base | ComfyUI 0.45 or newer with CUDA PyTorch; git | you, with the commands below (skipped when present) |
| Node packs | none - every node Sprite Forge uses is built into ComfyUI 0.45 | - |
| Models | about 18 GB by default; up to 49 GB with `--all` (adds Qwen-Image-Edit 2511 for the turnaround/animation `max` presets), listed in `models/catalog.json` | `forge.py setup` |
| Connector | MCP server entry and skill for Claude and Codex | `forge.py connect` |

Requirements: an NVIDIA GPU with at least 8 GB of VRAM (12 GB for the Qwen-Image-Edit
`max` presets), about 25 GB of free disk space (55 GB for `--all`), Windows or Linux.

## Install (unattended)

Run the steps in order. Each one is safe to repeat; anything already present is skipped.

### 1. Clone

Clone this repository and `cd` into it. Use any Python 3.10+ to run `forge.py`; it hands
over to ComfyUI's own Python, which has the image libraries it needs.

### 2. Run setup and act on its answer

```
python forge.py setup
```

| `status` | Exit code | Meaning and what to do |
| :--- | :--- | :--- |
| `ok` | 0 | Models are in place. Go to step 3. |
| `blocked` | 3 | Base software is missing, ComfyUI is too old, or the disk is too full. Run the commands in `manual_steps[].commands` (or free space), then run `setup` again. |
| `partial` | 3 | Something failed to download. Each failed item carries its own error. Run `setup` again (downloads resume). |

- **ComfyUI not found.** If ComfyUI is already installed somewhere, set the environment
  variable `SPRITEFORGE_COMFY` to its folder (the one containing `main.py`) and run `setup`
  again - do not install a second copy. Search the usual places first. Only if none
  exists, install it (commands in `manual_steps`).
- Downloads use several connections at once and resume after an interruption.
- Flags: `--dry-run` lists what would be installed and the download size; `--all` also
  installs Qwen-Image-Edit 2511 (31 GB with its encoder and LoRA) when the GPU has 12 GB or
  more; `--skip-models` skips the downloads.

### 3. Verify

```
python forge.py doctor
```

Every entry under `profiles` must show `"ready": true`, an empty `missing` list and (when
ComfyUI is running) an empty `missing_nodes` list. `missing` not empty: run `setup` again or
`python forge.py models --install <name> ...`. `missing_nodes` not empty: ComfyUI is older
than 0.45 - update it.

### 4. Connect the agent

```
python forge.py connect
```

Registers the MCP server and installs the skill for Claude and Codex at user level, backing
up each config file it edits. `--claude` or `--codex` for one, `--project <dir>` for a
single project, `--dry-run` to preview. A new agent session is needed before the tools appear.

Any other MCP client: a stdio server with command `python` and arguments
`["<this folder>/forge.py", "mcp"]`.

### 5. Prove it works

```
python forge.py sprite setup_test "a glowing blue mana potion in a round glass flask with a cork stopper"
```

Success is `"status": "ok"`, an empty `problems` list, and a `file` (a 512x512 transparent
PNG). Look at the `preview` image it names. The first run is slower while weights load.

### 6. Report

Tell the user what was already present, what was installed, how many GB were downloaded,
which config files `connect` edited, the test result and its file path (ask them to look at
it), and anything still missing.

## Rules while installing

- Never delete, replace or downgrade existing models, node packs or Python packages.
- Never change the installed PyTorch of an existing ComfyUI.
- Install Python packages only into ComfyUI's interpreter (`comfy_python` in `machine.json`).
- Do not edit `graphs/` to get past an error; settings belong in profiles.
- Do not work around the disk-space check by deleting the user's files.
- Model licences: every model Sprite Forge installs is Apache 2.0 or MIT (commercial use of
  outputs allowed). Tell the user this in the report, and that some stores ask for AI
  assets to be disclosed.

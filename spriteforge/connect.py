"""Register Sprite Forge with coding agents (MCP server + skill), and set up projects.

User scope   : Claude  -> ~/.claude.json (mcpServers) + ~/.claude/skills/sprite-forge
               Codex   -> ~/.codex/config.toml ([mcp_servers.sprite-forge]) + ~/.codex/skills/sprite-forge
Project scope: <project>/.mcp.json + <project>/.claude/skills/sprite-forge + a block in <project>/AGENTS.md
Every edited file is backed up next to itself (*.spriteforge.bak) the first time.
"""
import json
import os
import re
import shutil

from .config import FORGE_ROOT, load_machine

NAME = "sprite-forge"
MARK_A, MARK_B = "# >>> sprite-forge >>>", "# <<< sprite-forge <<<"
MD_A, MD_B = "<!-- sprite-forge:start -->", "<!-- sprite-forge:end -->"


def server_command():
    return load_machine()["comfy_python"], [os.path.join(FORGE_ROOT, "forge.py"), "mcp"]


def skill_text():
    py, args = server_command()
    text = open(os.path.join(FORGE_ROOT, "skills", NAME, "SKILL.md"), encoding="utf-8").read()
    return text.replace("python forge.py", f'"{py}" "{args[0]}"')  # exact paths for this machine


def _backup(path):
    if os.path.isfile(path) and not os.path.isfile(path + ".spriteforge.bak"):
        shutil.copyfile(path, path + ".spriteforge.bak")


def _write_skill(folder, remove, dry, log):
    dst = os.path.join(folder, NAME)
    if remove:
        if os.path.isdir(dst):
            log.append(f"remove {dst}")
            if not dry:
                shutil.rmtree(dst)
        return
    log.append(f"write {os.path.join(dst, 'SKILL.md')}")
    if not dry:
        os.makedirs(dst, exist_ok=True)
        open(os.path.join(dst, "SKILL.md"), "w", encoding="utf-8").write(skill_text())


def _json_mcp(path, remove, dry, log):
    py, args = server_command()
    data = json.load(open(path, encoding="utf-8-sig")) if os.path.isfile(path) else {}
    servers = data.setdefault("mcpServers", {})
    if remove:
        if NAME not in servers:
            return
        servers.pop(NAME)
        log.append(f"remove mcpServers.{NAME} from {path}")
    else:
        servers[NAME] = {"type": "stdio", "command": py, "args": args}
        log.append(f"set mcpServers.{NAME} in {path}")
    if not dry:
        _backup(path)
        json.dump(data, open(path, "w", encoding="utf-8"), indent=2)


def _marked_block(path, block, a, b, remove, dry, log):
    text = open(path, encoding="utf-8-sig").read() if os.path.isfile(path) else ""
    cleaned = re.sub(re.escape(a) + r".*?" + re.escape(b) + r"\n?", "", text, flags=re.S).rstrip("\n")
    new = cleaned + "\n" if remove else (cleaned + "\n\n" if cleaned else "") + f"{a}\n{block}\n{b}\n"
    if new == text or (remove and not text):
        return
    log.append(f"{'remove sprite-forge block from' if remove else 'write sprite-forge block to'} {path}")
    if not dry:
        _backup(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        open(path, "w", encoding="utf-8").write(new)


def connect(claude=False, codex=False, project=None, remove=False, dry_run=False):
    if not claude and not codex:
        claude = codex = True
    py, args = server_command()
    home, log = os.path.expanduser("~"), []

    if project:
        project = os.path.abspath(project)
        if claude:
            _json_mcp(os.path.join(project, ".mcp.json"), remove, dry_run, log)
            _write_skill(os.path.join(project, ".claude", "skills"), remove, dry_run, log)
        if codex:
            block = ("## Sprite Forge (local 2D game asset generation)\n\n" + skill_text().split("---", 2)[2].strip())
            _marked_block(os.path.join(project, "AGENTS.md"), block, MD_A, MD_B, remove, dry_run, log)
    else:
        if claude:
            _json_mcp(os.path.join(home, ".claude.json"), remove, dry_run, log)
            _write_skill(os.path.join(home, ".claude", "skills"), remove, dry_run, log)
        if codex:
            toml = (f"[mcp_servers.{NAME}]\ncommand = '{py}'\nargs = ['{args[0]}', 'mcp']\n"
                    "startup_timeout_sec = 30\ntool_timeout_sec = 120")
            _marked_block(os.path.join(home, ".codex", "config.toml"), toml, MARK_A, MARK_B, remove, dry_run, log)
            _write_skill(os.path.join(home, ".codex", "skills"), remove, dry_run, log)

    return {"status": "ok", "dry_run": dry_run, "scope": "project" if project else "user", "changes": log,
            "mcp_server": {"command": py, "args": args},
            "note": "Restart the agent (or open a new session) so it loads the server and the skill."}


def make_bundle(out_path=None):
    """Build a Claude Desktop extension (.mcpb): a one-file, double-click connector.

    The bundle is deliberately thin - it holds no code, only a manifest that starts
    this folder's forge.py - so profiles and settings keep a single home.
    """
    import zipfile

    from .mcp_server import TOOLS

    py, args = server_command()
    out_path = os.path.abspath(out_path or os.path.join(FORGE_ROOT, "dist", "sprite-forge.mcpb"))
    manifest = {
        "manifest_version": "0.2",
        "name": NAME,
        "display_name": "Sprite Forge",
        "version": "1.0.0",
        "description": "Generate 2D game assets on this computer's GPU: sprites, icons, pixel art, character turnarounds and animation sheets, tiles and VFX from text.",
        "long_description": "Sprite Forge drives a local ComfyUI install (FLUX.2 Klein 4B, Z-Image Turbo, Qwen-Image-Edit 2511, BiRefNet). "
                            "It turns a text prompt into a cut-out, cropped, checked PNG, real pixel art, or a sprite sheet with a "
                            "JSON atlas, keeps the best of several takes, and keeps characters consistent across directions and "
                            "animation frames. Profiles, hardware tiers and per-project settings are plain JSON files.",
        "author": {"name": "Sprite Forge (local)"},
        "server": {
            "type": "python",
            "entry_point": "server.py",
            "mcp_config": {"command": "${user_config.python}", "args": ["${__dirname}/server.py", "${user_config.forge}"]},
        },
        "tools": [{"name": n, "description": t[1]} for n, t in TOOLS.items()],
        "user_config": {
            "python": {"type": "file", "title": "Python (ComfyUI's python.exe)", "required": True, "default": py,
                       "description": "The Python that ComfyUI runs with; it has the image libraries Sprite Forge needs."},
            "forge": {"type": "file", "title": "forge.py", "required": True, "default": args[0],
                      "description": "forge.py inside the Sprite Forge folder."},
        },
        "compatibility": {"platforms": ["win32", "darwin", "linux"]},
        "keywords": ["sprites", "pixel art", "game assets", "sprite sheet", "icons", "comfyui"],
    }
    readme = ("Sprite Forge connector for Claude Desktop.\n\nThis bundle contains no code. It starts the MCP server in the "
              f"Sprite Forge folder:\n  {py}\n  {args[0]} mcp\n\nRebuild it with:  forge.py bundle\n")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("README.txt", readme)
        z.writestr("server.py", "import runpy, sys\nforge = sys.argv[1]\nsys.argv = [forge, \"mcp\"]\nrunpy.run_path(forge, run_name=\"__main__\")\n")
    return {"status": "ok", "bundle": out_path,
            "install": "In Claude Desktop: Settings > Extensions > Advanced settings > Install Extension, pick this file "
                       "(or double-click it). Confirm the two paths, then enable it."}


PROJECT_TEMPLATE = {
    "_about": "Sprite Forge settings for this project. Everything is optional.",
    "style": "",
    "output_dir": "assets/sprites",
    "characters_dir": "characters",
    "style_preset": "",
    "profiles": {
        "sprite": {"params": {"size": 256}},
        "pixel_sprite": {"params": {"grid": 32, "palette": None}}
    }
}


def init_project(project_dir):
    project_dir = os.path.abspath(project_dir)
    path = os.path.join(project_dir, "spriteforge.json")
    if os.path.isfile(path):
        return {"status": "ok", "created": False, "file": path, "note": "already exists; left unchanged"}
    os.makedirs(project_dir, exist_ok=True)
    json.dump(PROJECT_TEMPLATE, open(path, "w", encoding="utf-8"), indent=2)
    return {"status": "ok", "created": True, "file": path,
            "next": "Set 'style' (e.g. 'dark medieval fantasy, hand-painted') or 'style_preset'; add custom profiles in spriteforge_profiles/ and styles in spriteforge_styles/."}

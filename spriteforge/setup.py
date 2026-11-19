"""Unattended install: the model files for this GPU (Sprite Forge uses only nodes built into ComfyUI 0.45).

Safe to repeat: anything present is skipped. Never touches the installed PyTorch -
packages are installed with the current torch versions pinned as constraints.
"""
import glob
import os
import re
import shutil
import subprocess
import tempfile

from . import models
from .config import load_machine, log

MIN_COMFY = (0, 45, 0)


def _run(cmd, timeout=1800):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-1500:]


def comfy_version(root):
    try:
        text = open(os.path.join(root, "comfyui_version.py"), encoding="utf-8").read()
        return tuple(int(x) for x in re.search(r'"(\d+)\.(\d+)\.(\d+)', text).groups())
    except Exception:
        return None


def _pack_present(custom_nodes, pack):
    for path in glob.glob(os.path.join(custom_nodes, "*", "*.py")):
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if all(name in text for name in pack["provides"]):
            return os.path.dirname(path)
    return None


def _constraints(py):
    """Pin the installed torch family so no requirement can swap it."""
    code = ("import importlib.metadata as m\nfor n in ('torch','torchaudio','torchvision'):\n"
            "    try: print(f'{n}=={m.version(n)}')\n    except Exception: pass")
    _code, out = _run([py, "-c", code], timeout=120)
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write("\n".join(l for l in out.splitlines() if "==" in l))
    f.close()
    return f.name


def install_packs(m, dry_run=False, pinned=False):
    custom = os.path.join(m["comfy_root"], "custom_nodes")
    results = []
    for pack in models.catalog()["node_packs"]:
        found = _pack_present(custom, pack)
        if found:
            results.append({"pack": pack["folder"], "status": "already_installed", "path": found})
            continue
        if dry_run:
            results.append({"pack": pack["folder"], "status": "would_install", "url": pack["url"]})
            continue
        if not shutil.which("git"):
            results.append({"pack": pack["folder"], "status": "error", "error": "git is not installed",
                            "manual": "install git (winget install Git.Git), then run setup again"})
            continue
        dst = os.path.join(custom, pack["folder"])
        code, out = _run(["git", "clone", "--depth", "50", pack["url"], dst])
        if code:
            results.append({"pack": pack["folder"], "status": "error", "step": "git clone", "output": out})
            continue
        if pinned and pack.get("tested_commit"):
            _run(["git", "-C", dst, "checkout", "-q", pack["tested_commit"]])
        req = os.path.join(dst, "requirements.txt")
        entry = {"pack": pack["folder"], "status": "installed", "path": dst}
        if os.path.isfile(req):
            cons = _constraints(m["comfy_python"])
            code, out = _run([m["comfy_python"], "-m", "pip", "install", "-r", req, "-c", cons])
            os.unlink(cons)
            if code:
                entry.update(status="installed_with_errors", pip_output=out,
                             manual=f'"{m["comfy_python"]}" -m pip install -r "{req}"')
        results.append(entry)
    return results


def setup(dry_run=False, skip_models=False, skip_nodes=False, pinned=False, everything=False):
    m = load_machine(refresh=True)
    out = {"status": "ok", "dry_run": dry_run, "machine": {"gpu": m["gpu"], "ram_gb": m["ram_gb"], "tier": m["tier"]}}
    if not m["comfy_root"]:
        out.update(status="blocked", reason="ComfyUI was not found", manual_steps=[{
            "what": "Install ComfyUI 0.45 or newer, or point Sprite Forge at an existing one",
            "commands": ["set SPRITEFORGE_COMFY=<folder containing ComfyUI's main.py>   (if it is already installed)",
                         "git clone https://github.com/comfyanonymous/ComfyUI",
                         "cd ComfyUI && python -m venv venv && venv/Scripts/python -m pip install torch torchvision "
                         "--index-url https://download.pytorch.org/whl/cu128 && venv/Scripts/python -m pip install -r requirements.txt"]}])
        return out
    ver = comfy_version(m["comfy_root"])
    out["comfyui_version"] = ".".join(map(str, ver)) if ver else "unknown"
    if ver and ver < MIN_COMFY:
        out.update(status="blocked", reason=f"ComfyUI {out['comfyui_version']} is too old: FLUX.2 Klein, background-removal "
                   "and Qwen-Image-Edit nodes need 0.45 or newer", manual_steps=[{"what": "update ComfyUI", "commands": [
                       f'git -C "{m["comfy_root"]}" pull', "or run the update script of your portable build"]}])
        return out

    if not skip_nodes:
        out["node_packs"] = install_packs(m, dry_run, pinned)

    if not skip_models:
        wanted = models.default_set(m["tier"], everything)
        cat = models.catalog()
        index = models._installed_index()
        todo = [n for n in wanted if (n in cat["files"] and n not in index) or (n in cat["repos"] and not models.repo_present(n))]
        size = round(sum((cat["files"].get(n) or cat["repos"].get(n))["size_gb"] for n in todo), 2)
        free = round(shutil.disk_usage(models.models_dir()).free / 1e9, 1)
        out["models"] = {"to_download": todo, "download_gb": size, "free_gb": free}
        if dry_run:
            pass
        elif size + 5 > free:
            out["status"] = "blocked"
            out["models"]["error"] = f"not enough disk space: {size} GB to download, {free} GB free (keeping 5 GB spare)"
        else:
            out["models"]["results"] = models.install(todo)

    bad = [p for p in out.get("node_packs", []) if p["status"] in ("error", "installed_with_errors")]
    bad += [r for r in out.get("models", {}).get("results", []) if r["status"] == "error"]
    if bad and out["status"] == "ok":
        out["status"] = "partial"
    if any(p["status"].startswith("installed") for p in out.get("node_packs", [])):
        out["restart_comfyui"] = "A node pack was installed: restart ComfyUI so it loads (Sprite Forge starts it again when needed)."
    log("setup finished")
    return out

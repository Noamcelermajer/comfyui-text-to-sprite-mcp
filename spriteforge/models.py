"""Which model files each profile needs, which are present, and how to fetch the rest.

A profile's needs are read from its graph and from every parameter value that names a
model file (so a preset that switches to a bigger model reports that model too), plus
the catalog's `repos` entries (folders downloaded whole, e.g. the CLIP scorer).
"""
import json
import os
import subprocess
import urllib.request

from . import profiles
from .config import FORGE_ROOT, load_machine, log

MODEL_EXTS = (".safetensors", ".gguf", ".pt", ".pth", ".ckpt", ".bin")


def catalog():
    return json.load(open(os.path.join(FORGE_ROOT, "models", "catalog.json"), encoding="utf-8-sig"))


def models_dir():
    return os.path.join(load_machine()["comfy_root"], "models")


def _installed_index():
    index = {}
    for root, _dirs, files in os.walk(models_dir()):
        for f in files:
            if f.lower().endswith(MODEL_EXTS):
                index.setdefault(f, os.path.join(root, f))
    return index


def _model_names(obj):
    if isinstance(obj, str):
        if obj.lower().endswith(MODEL_EXTS) and len(obj) < 200:
            yield os.path.basename(obj.replace("\\", "/"))
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _model_names(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _model_names(v)


def repo_present(repo_id, meta=None):
    meta = meta or catalog()["repos"][repo_id]
    if meta.get("local"):  # cached inside Sprite Forge (the CLIP scorer)
        cache = os.path.join(FORGE_ROOT, "models", "hf_cache", "models--" + repo_id.replace("/", "--"), "snapshots")
        return os.path.isdir(cache) and any(os.scandir(cache))
    d = os.path.join(models_dir(), *meta["folder"].split("/"))
    return os.path.isdir(d) and any(f.endswith((".safetensors", ".bin")) for f in os.listdir(d))


def needs(profile_id, preset="standard", project_dir=None):
    p = profiles.resolve(profile_id, project_dir, preset=preset)
    names = set()
    for rel in [x for x in (p.get("graph"), p.get("edit_graph")) if x]:
        g = json.load(open(profiles.graph_path({**p, "graph": rel}), encoding="utf-8-sig"))
        set_targets = set(p.get("set", {})) if rel == p.get("graph") else set(p.get("edit_set", {}))
        for nid, node in g.items():  # a graph default that a profile parameter replaces is not a need
            for key, val in node["inputs"].items():
                if f"{nid}.{key}" not in set_targets:
                    names |= set(_model_names(val))
    names |= set(_model_names(p["params"]))
    tag = f"{profile_id}:{preset}"
    repos = [r for r, meta in catalog()["repos"].items()
             if profile_id in meta.get("profiles", []) or tag in meta.get("profiles", [])]
    return sorted(names), repos


def profile_status(profile_id, project_dir=None, index=None):
    index = _installed_index() if index is None else index
    cat = catalog()
    p = profiles.resolve(profile_id, project_dir)
    out = {"profile": profile_id, "min_vram_gb": p.get("min_vram_gb", 0),
           "fits_this_gpu": load_machine()["gpu"]["vram_gb"] + 0.5 >= p.get("min_vram_gb", 0), "presets": {}}
    for preset in profiles.PRESETS:
        files, repos = needs(profile_id, preset, project_dir)
        missing = [{"file": f, **{k: cat["files"].get(f, {}).get(k) for k in ("folder", "size_gb", "role")}} for f in files if f not in index]
        missing += [{"repo": r, **{k: cat["repos"][r].get(k) for k in ("folder", "size_gb", "role")}} for r in repos if not repo_present(r)]
        out["presets"][preset] = {"ready": not missing, "missing": missing}
    out["ready"] = out["presets"]["standard"]["ready"]
    out["missing"] = out["presets"]["standard"]["missing"]
    return out


def report(project_dir=None):
    index = _installed_index()
    m = load_machine()
    cat = catalog()
    return {
        "gpu": m["gpu"], "ram_gb": m["ram_gb"], "tier": m["tier"],
        "guide_for_this_tier": cat["hardware_guide"].get(str(m["tier"]), {}),
        "profiles": [profile_status(p["id"], project_dir, index) for p in profiles.list_profiles(project_dir)],
        "optional_not_installed": [{"file": f, "size_gb": meta["size_gb"], "role": meta["role"]}
                                   for f, meta in cat["files"].items() if f not in index and not meta.get("default")],
    }


def _download(url, dst, label):
    from .download import download
    download(url, dst, label)


def _install_repo(repo_id, meta):
    if repo_present(repo_id, meta):
        return {"repo": repo_id, "status": "already_installed"}
    py = load_machine()["comfy_python"]
    if meta.get("local"):
        code = ("import sys; sys.path.insert(0, r'%s'); from spriteforge import score; score._load(); print('ok')" % FORGE_ROOT)
    else:
        target = os.path.join(models_dir(), *meta["folder"].split("/"))
        code = ("from huggingface_hub import snapshot_download; snapshot_download(repo_id=%r, local_dir=r'%s'); print('ok')"
                % (repo_id, target))
    log(f"downloading {repo_id} ({meta.get('size_gb')} GB) ...")
    r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=7200)
    if r.returncode == 0 and repo_present(repo_id, meta):
        return {"repo": repo_id, "status": "installed"}
    return {"repo": repo_id, "status": "error", "error": (r.stderr or r.stdout)[-500:],
            "manual": f"huggingface-cli download {repo_id} --local-dir <ComfyUI>/models/{meta['folder']}"}


def install(names):
    """Download catalog files (file names) or repos (owner/name) into place. One result per item."""
    cat = catalog()
    results = []
    for name in names:
        if name in cat["repos"]:
            results.append(_install_repo(name, cat["repos"][name]))
            continue
        meta = cat["files"].get(name)
        if not meta:
            results.append({"file": name, "status": "not_in_catalog"})
            continue
        dst = os.path.join(models_dir(), meta["folder"], name)
        if os.path.isfile(dst):
            results.append({"file": name, "status": "already_installed"})
            continue
        try:
            _download(meta["url"], dst, f"{name} ({meta['size_gb']} GB)")
            results.append({"file": name, "status": "installed", "path": dst})
        except Exception as e:
            results.append({"file": name, "status": "error", "error": str(e)})
    return results


def default_set(tier, everything=False):
    """What `setup` installs on this tier: the defaults, or every item that fits."""
    cat = catalog()
    pick = lambda meta: (everything or meta.get("default")) and tier >= meta.get("min_tier", 0)
    return ([f for f, m in cat["files"].items() if pick(m)] + [r for r, m in cat["repos"].items() if pick(m)])

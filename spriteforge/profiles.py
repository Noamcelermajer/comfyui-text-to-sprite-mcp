"""Profiles: data files that describe one way of making a 2D asset (a sprite, an icon, pixel art, a character sheet, a tile).

Settings are layered, later layers win:
  1. the profile file (and whatever it `extends`)
  2. defaults for this machine's VRAM tier                  ("tiers": {"24": {"params": {...}}})
  3. the project's spriteforge.json                           ("profiles": {"<id>": {"params": {...}}})
  4. the chosen preset - fast / standard / max              ("presets": {"fast": {...}})
     and the project's own block for that preset            ("profiles": {"<id>": {"presets": {"fast": {...}}}})
  4b. the style preset - retro_8bit / cartoon / ...          (styles/<id>.json "params")
     only parameters the profile has are applied; the rest are reported as ignored
  5. the arguments of the call
  6. limits for this machine's VRAM tier, applied last so nothing above can exceed
     what the card can run                                  ("tiers": {"8": {"limits": {...}}})
     numbers are capped (min), `true` is forced, anything else is replaced.

The preset comes after the project's general settings on purpose: a project that
sets `loudness` for a profile changes its standard result without flattening fast and max.

Profiles are looked up in:  SpriteForge/profiles, SpriteForge/profiles/user,
and <project>/spriteforge_profiles (a project profile with the same id wins).
"""
import glob
import json
import os

from .config import FORGE_ROOT, deep_merge, load_machine

PROJECT_FILE = "spriteforge.json"


def load_project(project_dir):
    if not project_dir:
        return {}
    path = os.path.join(project_dir, PROJECT_FILE)
    return json.load(open(path, encoding="utf-8-sig")) if os.path.isfile(path) else {}


def _raw_profiles(project_dir=None):
    dirs = [os.path.join(FORGE_ROOT, "profiles"), os.path.join(FORGE_ROOT, "profiles", "user")]
    if project_dir:
        dirs.append(os.path.join(project_dir, "spriteforge_profiles"))
    found = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.json"))):
            p = json.load(open(path, encoding="utf-8-sig"))
            p["_dir"] = os.path.dirname(path)
            found[p.get("id") or os.path.splitext(os.path.basename(path))[0]] = p
    return found


def _extended(pid, raw, seen=()):
    if pid not in raw:
        raise KeyError(f"unknown profile '{pid}'. Known: {', '.join(sorted(raw))}")
    if pid in seen:
        raise ValueError(f"profile '{pid}' extends itself")
    p = raw[pid]
    if p.get("extends"):
        base = _extended(p["extends"], raw, seen + (pid,))
        merged = deep_merge(base, {k: v for k, v in p.items() if k != "extends"})
        if "graph" not in p:  # an inherited graph path stays relative to the parent's folder
            merged["_dir"] = base["_dir"]
        return merged
    return dict(p)


PRESETS = ("fast", "standard", "max")


def _style_dirs(project_dir=None):
    dirs = [os.path.join(FORGE_ROOT, "styles"), os.path.join(FORGE_ROOT, "styles", "user")]
    if project_dir:
        dirs.append(os.path.join(project_dir, "spriteforge_styles"))
    return dirs


def load_style(style_id, project_dir=None):
    """A style preset: prompt wording plus sprite parameters. None = no style."""
    if not style_id:
        return None
    for d in reversed(_style_dirs(project_dir)):  # a project style with the same id wins
        path = os.path.join(d, f"{style_id}.json")
        if os.path.isfile(path):
            return json.load(open(path, encoding="utf-8-sig"))
    raise KeyError(f"unknown style '{style_id}'. Known: {', '.join(s['id'] for s in list_styles(project_dir))}")


def list_styles(project_dir=None):
    found = {}
    for d in _style_dirs(project_dir):
        for path in sorted(glob.glob(os.path.join(d, "*.json"))):
            st = json.load(open(path, encoding="utf-8-sig"))
            found[st.get("id") or os.path.splitext(os.path.basename(path))[0]] = st
    return [{"id": k, "title": v.get("title", ""), "description": v.get("description", ""),
             "status": v.get("status", "experimental"), "sets": sorted(v.get("params", {}))} for k, v in sorted(found.items())]


def resolve(pid, project_dir=None, params=None, preset=None, style=None):
    """Return the profile with every layer applied; `params` are the call's overrides."""
    raw = _raw_profiles(project_dir)
    p = _extended(pid, raw)
    p["id"] = pid
    tier_block = (p.get("tiers") or {}).get(str(load_machine()["tier"]), {})
    project = load_project(project_dir)
    proj_block = (project.get("profiles") or {}).get(pid, {})
    preset_block = {}
    if preset and preset != "standard":
        if preset not in PRESETS:
            raise ValueError(f"unknown preset '{preset}'. Presets: {', '.join(PRESETS)}")
        preset_block = (p.get("presets") or {}).get(preset) or {}
    p["preset"] = preset or "standard"
    if preset and preset != "standard" and not preset_block:
        p["preset_note"] = f"profile '{pid}' has no separate '{preset}' settings; its standard settings were used"
    proj_preset = ((proj_block.get("presets") or {}).get(p["preset"]) or {})
    tier_preset = ((tier_block.get("presets") or {}).get(p["preset"]) or {})  # e.g. 8 GB: max without the 20B edit model
    known = set(p.get("params", {}))
    for name, layer in (("tier", tier_block), ("project", proj_block), ("preset", preset_block),
                        ("tier preset", tier_preset), ("project preset", proj_preset)):
        bad = [k for k in layer.get("params", {}) if k not in known]
        if bad:
            raise ValueError(f"{name} settings for profile '{pid}' name unknown parameters {bad}. "
                             f"It accepts: {', '.join(sorted(known))}")
        p["params"] = {**p.get("params", {}), **layer.get("params", {})}
        for g_key, s_key, v_key in (("graph", "set", "vram_group"), ("edit_graph", "edit_set", "edit_vram_group")):
            if g_key in layer:  # a layer that switches model graph brings its own input map
                p[g_key], p[s_key] = layer[g_key], dict(layer.get(s_key, {}))
            else:
                p[s_key] = {**p.get(s_key, {}), **layer.get(s_key, {})}
            if v_key in layer:
                p[v_key] = layer[v_key]
        if "batch" in layer:
            p["batch"] = layer["batch"]
    project_style = project.get("style_preset")
    st = load_style(style if style is not None else project_style, project_dir)
    p["style_preset"] = st["id"] if st else None
    p["style_text"] = st.get("prompt", "") if st else ""
    p["style_ignored"] = []
    if st:
        for k, v in (st.get("params") or {}).items():
            if k in p["params"]:
                p["params"][k] = v
            else:
                p["style_ignored"].append(k)
    unknown = [k for k in (params or {}) if k not in p["params"]]
    if unknown:
        raise ValueError(f"profile '{pid}' has no parameter {unknown}. It accepts: {', '.join(sorted(p['params']))}")
    p["params"] = {**p["params"], **(params or {})}
    p["limited_by_vram"] = {}
    for k, cap in (tier_block.get("limits") or {}).items():
        cur = p["params"].get(k)
        if isinstance(cap, bool):
            new = True if cap else cur
        elif isinstance(cap, (int, float)) and isinstance(cur, (int, float)):
            new = min(cur, cap)
        else:
            new = cap
        if new != cur:
            p["limited_by_vram"][k] = {"asked": cur, "used": new}
            p["params"][k] = new
    p["style"] = project.get("style", "")
    return p


def graph_path(profile):
    g = profile["graph"]
    for base in (profile.get("_dir", ""), FORGE_ROOT):
        cand = os.path.normpath(os.path.join(base, g))
        if os.path.isfile(cand):
            return cand
    raise FileNotFoundError(f"graph '{g}' of profile '{profile['id']}' was not found")


def list_profiles(project_dir=None):
    raw = _raw_profiles(project_dir)
    tier = str(load_machine()["tier"])
    out = []
    for pid in sorted(raw):
        if raw[pid].get("abstract") or raw[pid].get("internal"):
            continue
        p = _extended(pid, raw)
        params = resolve(pid, project_dir)["params"]
        out.append({"id": pid, "kind": p.get("kind"), "title": p.get("title", ""), "description": p.get("description", ""),
                    "status": p.get("status", "experimental"), "min_vram_gb": p.get("min_vram_gb", 0),
                    "params": params, "requires": p.get("requires", []),
                    "prompt_guide": p.get("prompt_guide", ""),
                    "presets": {"standard": "the profile's own settings",
                                **{k: v.get("description", "") for k, v in (p.get("presets") or {}).items() if v}}
                    })
    return out

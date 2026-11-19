"""The engine: run a profile, keep the best take, and make it game-ready.

generate_sprite   text -> a checked, cut-out, cropped PNG (sprite, icon, pixel art, character,
                  tile, portrait), or a frame set + sprite sheet + JSON atlas (turnaround,
                  animation, vfx)
create_character  a reusable character reference (characters/<id>.png); turnarounds and
                  animations are reference edits of it, so the character stays the same
generate_batch    many assets, grouped by model so weights load once per group

Every result is written as <name>.png (or <name>_sheet.png + <name>.atlas.json + a folder
of frames) with a <name>.json manifest next to it. The other takes are kept in
.spriteforge/<name>/ in case the chosen one is wrong.
"""
import glob
import hashlib
import json
import os
import random
import re
import shutil
import time
import uuid

import numpy as np

from . import comfy, pixel, profiles, score, sprite
from .config import FORGE_ROOT, load_machine, log

OUT_SUBFOLDER = "spriteforge"
REF_GREY = (0.82, 0.82, 0.82)  # the light grey the models are asked to draw behind the subject
_vram = {"group": None}
SINGLE_KINDS = ("sprite", "icon", "pixel", "character", "tile", "portrait")


def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(name).strip()).strip("_")
    if not name:
        raise ValueError("name must contain letters or digits")
    return name


def out_dir_for(project_dir=None, out_dir=None):
    if out_dir:
        return os.path.abspath(out_dir)
    if project_dir:
        return os.path.abspath(os.path.join(project_dir, profiles.load_project(project_dir).get("output_dir", "assets/sprites")))
    return os.path.join(FORGE_ROOT, "output")


def characters_dir(project_dir=None):
    if not project_dir:
        return os.path.join(FORGE_ROOT, "characters")
    return os.path.join(os.path.abspath(project_dir), profiles.load_project(project_dir).get("characters_dir", "characters"))


def _fill(value, ctx):
    """'{name}' alone keeps the value's type; inside a longer string it is formatted in."""
    if not isinstance(value, str):
        return value
    m = re.fullmatch(r"\{(\w+)\}", value)
    if m:
        if m.group(1) not in ctx:
            raise KeyError(f"profile uses {{{m.group(1)}}} but no such value exists")
        return ctx[m.group(1)]
    return re.sub(r"\{(\w+)\}", lambda k: str(ctx.get(k.group(1), k.group(0))), value)


def run_graph(profile, graph_rel, set_map, ctx, run_tag, group=None):
    """Fill a graph, run it, return [(picture, mask)] - one pair per take - and timings."""
    path = profiles.graph_path({**profile, "graph": graph_rel})
    g = json.load(open(path, encoding="utf-8-sig"))
    missing = []
    for target, value in set_map.items():
        optional = target.startswith("?")
        node, _, key = target.lstrip("?").partition(".")
        if node not in g:
            if not optional:
                missing.append(f"{target} (node {node} not in the graph)")
            continue
        filled = _fill(value, ctx)
        if filled is not None:  # null keeps the graph's own value
            g[node]["inputs"][key] = filled
    if missing:
        raise ValueError(f"profile '{profile['id']}' sets inputs its graph does not have: {', '.join(missing)}")
    for node in g.values():  # spriteforge/out_rgb -> spriteforge/<tag>_rgb, same for _mask
        if node["class_type"] == "SaveImage":
            prefix = node["inputs"].get("filename_prefix", "spriteforge/out_rgb")
            node["inputs"]["filename_prefix"] = f"{OUT_SUBFOLDER}/{run_tag}{prefix.rsplit('out', 1)[-1]}"

    group = group or profile.get("vram_group")
    if group != _vram["group"] and _vram["group"] is not None:
        comfy.free_vram()
    try:
        seconds, steps = comfy.run_graph(g)
    except comfy.ComfyError as e:
        if "OutOfMemory" not in str(e) and "out of memory" not in str(e).lower():
            raise
        log("out of GPU memory - freeing VRAM and retrying once ...")
        comfy.free_vram()
        seconds, steps = comfy.run_graph(g)
    _vram["group"] = group
    out = os.path.join(load_machine()["comfy_root"], "output", OUT_SUBFOLDER)
    rgbs = sorted(glob.glob(os.path.join(out, f"{run_tag}_rgb_*.png")))
    masks = sorted(glob.glob(os.path.join(out, f"{run_tag}_mask_*.png")))
    if not rgbs:
        raise RuntimeError(f"the graph ran but saved no picture under {OUT_SUBFOLDER}/{run_tag}")
    return [(r, masks[i] if i < len(masks) else None) for i, r in enumerate(rgbs)], seconds, steps


def build_prompt(profile, prompt, ctx, style="", style_text=""):
    parts = [t.strip().rstrip(".") for t in (style_text, profile.get("style"), style) if t and t.strip()]
    text = profile.get("prompt_template", "{prompt}")
    text = text.replace("{prompt}", (prompt or "").strip().rstrip("."))
    text = text.replace("{style}", (", " + ", ".join(parts)) if parts else "")
    text = _fill(text, ctx)
    text = re.sub(r",\s*,", ",", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _settings_key(p, prompt, extra):
    blob = json.dumps({"profile": p["id"], "graph": p.get("graph"), "params": p["params"], "prompt": prompt, "extra": extra},
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _upload_reference(rgba, name):
    """Edit models get the character on the same light grey they were drawn on."""
    os.makedirs(comfy.input_dir(), exist_ok=True)
    sprite.to_pil_rgb(sprite.on_background(rgba, REF_GREY)).save(os.path.join(comfy.input_dir(), name))
    return name


def _cutout(pair, params):
    rgb_path, mask_path = pair
    mode = params.get("alpha_mode", "cutout") if params.get("remove_background", True) else "none"
    rgba = sprite.load_rgba(rgb_path, mask_path, mode)
    if mode != "none":
        rgba = sprite.clean_alpha(rgba)
        if params.get("defringe", True) and mode == "cutout":
            rgba = sprite.defringe(rgba)
    return rgba


def _rank(cands, text=None, reference=None, key_extra=None):
    """Order: fewest problems, then (likeness to the reference and/or) prompt match."""
    if score.available() and len(cands) > 1:
        try:
            if text:
                for c, s in zip(cands, score.text_scores([c["rgba"] for c in cands], text)):
                    c["clip"] = s
            if reference is not None:
                for c, s in zip(cands, score.image_scores([c["rgba"] for c in cands], reference)):
                    c["likeness"] = s
        except Exception as e:
            log(f"CLIP scoring failed ({e}); keeping the first clean take")
    def key(c):
        return (len(c["problems"]), -((c.get("likeness") or 0) * 2 + (c.get("clip") or 0) + (key_extra(c) if key_extra else 0)),
                c["index"])
    return sorted(cands, key=key)


def _load_png(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGBA"), np.float32) / 255.0


def _preview(rgba, path, size=384):
    """Sprite on a checkerboard, for people and agents to look at."""
    h, w = rgba.shape[:2]
    s = size / max(h, w)
    small = sprite._resize(rgba, max(1, int(w * s)), max(1, int(h * s)), nearest=s >= 2)
    yy, xx = np.mgrid[:small.shape[0], :small.shape[1]]
    board = np.where(((yy // 12 + xx // 12) % 2)[..., None] == 0, 0.86, 0.68).astype(np.float32) * np.ones(3, np.float32)
    a = small[..., 3:4]
    sprite.to_pil_rgb(small[..., :3] * a + board * (1 - a)).save(path)
    return path


# ----------------------------------------------------------------------------- characters

def character_info(character, project_dir=None):
    cid = safe_name(character)
    d = characters_dir(project_dir)
    png, meta = os.path.join(d, cid + ".png"), os.path.join(d, cid + ".json")
    if os.path.isfile(png) and os.path.isfile(meta):
        m = json.load(open(meta, encoding="utf-8"))
        return {"character": cid, "reference": png, "description": m.get("prompt"), "seed": m.get("seed"),
                "style_preset": m.get("style_preset"), "created": m.get("created")}
    return None


def list_characters(project_dir=None):
    d = characters_dir(project_dir)
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                info = character_info(f[:-5], project_dir)
                if info:
                    out.append(info)
    return out


def create_character(character, description, project_dir=None, seed=None, preset="standard", style_preset=None,
                     style="", params=None, force=False, profile="character"):
    """A full-body character reference, saved to characters/<id>.png for turnarounds and animations."""
    cid = safe_name(character)
    existing = character_info(cid, project_dir)
    if existing and not force:
        return {"status": "ok", "cached": True, **existing}
    if not description:
        raise ValueError(f"character '{cid}' does not exist yet: give a description (who they are, outfit, colours, props)")
    if profiles.resolve(profile, project_dir).get("kind") != "character":
        raise ValueError(f"'{profile}' is not a character profile (use character or pixel_character)")
    r = generate_sprite(profile, cid, description, project_dir, characters_dir(project_dir), seed, style, params,
                        True, preset, style_preset)
    r["created"] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(r, open(os.path.join(characters_dir(project_dir), cid + ".json"), "w", encoding="utf-8"), indent=2)
    return {**r, "character": cid, "reference": r["file"]}


# ----------------------------------------------------------------------------- project palette

def project_palette_path(project_dir=None):
    base = os.path.abspath(project_dir) if project_dir else FORGE_ROOT
    rel = profiles.load_project(project_dir).get("palette_file", "palette.hex") if project_dir else "palette.hex"
    return os.path.join(base, rel)


def resolve_palette(palette, project_dir=None):
    """'project' -> the project's frozen palette (palette.hex, one hex colour per line, the Lospec
    format) if it exists yet, else None (the first pixel asset then chooses it and saves it)."""
    if palette != "project":
        return palette
    path = project_palette_path(project_dir)
    if os.path.isfile(path):
        cols = [l.strip().lstrip("#") for l in open(path, encoding="utf-8") if l.strip() and not l.startswith(";")]
        return cols or None
    return None


def save_project_palette(pal_rgb, project_dir=None):
    path = project_palette_path(project_dir)
    with open(path, "w", encoding="utf-8") as f:
        f.write("; Sprite Forge project palette - every pixel asset with palette \"project\" uses these colours\n")
        for c in pal_rgb:
            f.write("%02X%02X%02X\n" % tuple(int(round(v * 255)) for v in c))
    return path


# ----------------------------------------------------------------------------- single images

def _process_single(rgba, p, kind):
    """-> final RGBA, extra arrays to save {suffix: rgba}, info."""
    q = p["params"]
    info, extra = {}, {}
    if kind == "tile":
        rgb = rgba[..., :3]
        info["seam_before"] = sprite.seam_score(rgb)
        if q.get("seamless", True):
            rgb = sprite.make_seamless(rgb, float(q.get("blend", 0.25)))
        size = q.get("size")
        out = np.dstack([rgb, np.ones(rgb.shape[:2], np.float32)])
        if size:
            out = sprite._resize(out, int(size), int(size))
        info["seam"] = sprite.seam_score(out[..., :3])
        if q.get("pixelize"):
            px, pinfo, _pal = pixel.pixelize(out, int(q.get("grid", 32)), int(q.get("colors", 16)), q.get("_palette"))
            extra["pixel"] = px
            extra["pixel_x8"] = sprite.upscale_nearest(px, 8)
            info["pixel"] = pinfo
        extra["tiled_preview"] = np.dstack([sprite.tile_preview(out), np.ones((out.shape[0] * 3, out.shape[1] * 3), np.float32)])
        return out, extra, info
    if kind == "pixel":
        out, pinfo, pal = pixel.pixelize(rgba, q.get("grid", 32), int(q.get("colors", 16)), q.get("_palette"),
                                         bool(q.get("outline")), q.get("canvas"), q.get("anchor", "center"))
        info.update(pinfo)
        info["_pal"] = pal
        scale = int(q.get("preview_scale") or 0)
        if scale > 1:
            extra[f"x{scale}"] = sprite.upscale_nearest(out, scale)
        return out, extra, info
    size = q.get("size")
    if size and (q.get("remove_background", True) or kind != "portrait"):
        out, _ = sprite.fit(rgba, int(size), float(q.get("padding", 0.06)), q.get("anchor", "center"))
    elif q.get("remove_background", True):
        out = sprite.crop(rgba, 4)
    else:
        out = rgba if not size else sprite._resize(rgba, int(size), int(size * rgba.shape[0] / rgba.shape[1]))
    for s in q.get("sizes") or []:
        extra[str(s)] = sprite._resize(out, int(s), int(round(int(s) * out.shape[0] / out.shape[1])))
    return out, extra, info


def _single(p, name, prompt, ctx, out, dst, key, seed, t0):
    kind = p["kind"]
    q = p["params"]
    n = max(1, int(q.get("candidates", 1)))
    run_tag = f"{name}_{uuid.uuid4().hex[:6]}"
    pairs, gen_s, node_steps = run_graph(p, p["graph"], p["set"], {**ctx, "candidates": n}, run_tag)
    work = os.path.join(out, ".spriteforge", name)
    os.makedirs(work, exist_ok=True)
    cands = []
    for i, pair in enumerate(pairs):
        rgba = _cutout(pair, q)
        raw_stats = sprite.analyse(rgba)
        final, extra, info = _process_single(rgba, p, kind)
        problems = sprite.problems_of(raw_stats, kind)
        if kind == "pixel":
            problems += pixel.pixel_problems(info)
        if kind == "tile" and info.get("seam", 0) > 2.0:
            problems.append(f"visible seam (score {info['seam']}; about 1 is seamless)")
        cpath = sprite.save_png(final, os.path.join(work, f"take_{i}.png"))
        cands.append({"index": i, "seed": seed, "rgba": final, "file": cpath, "raw_file": pair[0], "problems": problems,
                      "stats": raw_stats, "extra": extra, "info": info})
    bonus = None
    if kind == "tile":
        bonus = lambda c: -0.05 * max(0, c["info"].get("seam", 1) - 1)
    elif kind == "pixel":  # real pixel art has uniform cells and few strays
        bonus = lambda c: -0.5 * c["info"].get("cell_variance", 0) - 0.002 * c["info"].get("cleaned_pixels", 0)
    ranked = _rank(cands, text=prompt if q.get("rank_by_clip", True) else None, key_extra=bonus)
    best = ranked[0]
    if kind == "pixel" and q.get("palette") == "project" and not q.get("_palette"):
        best["info"]["project_palette_saved"] = save_project_palette(best["info"]["_pal"], q.get("_project_dir"))
    for c in cands:
        c["info"].pop("_pal", None)
    sprite.save_png(best["rgba"], dst)
    files = {"main": dst}
    for suffix, arr in best["extra"].items():
        files[suffix] = sprite.save_png(arr, os.path.join(out, f"{name}_{suffix}.png"))
    preview = _preview(best["rgba"] if kind != "tile" else best["extra"]["tiled_preview"],
                       os.path.join(work, "preview.png"))
    if not q.get("keep_candidates", True):
        shutil.rmtree(work, ignore_errors=True)
        preview = None
    return {
        "status": "ok" if not best["problems"] else "failed_check", "file": dst, "files": files, "preview": preview,
        "problems": best["problems"], "image": {"width": int(best["rgba"].shape[1]), "height": int(best["rgba"].shape[0]),
                                                **{k: v for k, v in best["info"].items()}},
        "raw_stats": best["stats"],
        "candidates": [{k: c.get(k) for k in ("index", "clip", "problems", "file")} for c in cands],
        "chosen": best["index"], "chosen_by": "CLIP prompt match" if best.get("clip") is not None else "first clean take",
        "timings": {"generation_s": round(gen_s, 1), "total_s": round(time.time() - t0, 1)}, "slowest_nodes": node_steps[:3],
    }


# ----------------------------------------------------------------------------- frame sets

VIEWS = {
    "down": "front view: the character faces the viewer",
    "down_right": "three-quarter front view: the character is turned 45 degrees and faces toward the lower right of the picture",
    "right": "side view in profile: the character faces right, toward the right edge of the picture",
    "up_right": "three-quarter back view: the character is turned away from the viewer and faces toward the upper right of the picture, mostly its back is visible",
    "up": "back view: the character faces away from the viewer, only its back is visible",
    "up_left": "three-quarter back view: the character is turned away from the viewer and faces toward the upper left of the picture, mostly its back is visible",
    "left": "side view in profile: the character faces left, toward the left edge of the picture",
    "down_left": "three-quarter front view: the character is turned 45 degrees and faces toward the lower left of the picture",
}
MIRROR = {"left": "right", "up_left": "up_right", "down_left": "down_right"}
ORDER8 = ["down", "down_right", "right", "up_right", "up", "up_left", "left", "down_left"]
ORDER4 = ["down", "right", "up", "left"]

ACTIONS = {
    "walk": ["walking, the front leg stepping forward with the heel touching the ground and the back leg behind, arms swinging opposite to the legs",
             "walking, the legs passing each other under the body, one knee bent and lifted, the body slightly higher",
             "walking, the other leg now stepping forward with the heel touching the ground, arms swinging the other way",
             "walking, the legs passing each other again, the other knee bent and lifted, the body slightly higher"],
    "run": ["running, leaning forward, the front leg reaching far forward, the back leg pushing off, arms pumping",
            "running, both feet off the ground, knees bent, leaning forward",
            "running, leaning forward, the other leg reaching far forward, arms pumping the other way",
            "running, both feet off the ground again, the other knee driving up"],
    "idle": ["standing relaxed in a neutral pose",
             "standing relaxed, breathing in, chest and shoulders slightly raised",
             "standing relaxed, weight shifted slightly onto one leg",
             "standing relaxed, breathing out, shoulders slightly lowered"],
    "attack": ["winding up to strike: the weapon or fist drawn back behind the body, weight on the back foot",
               "mid-swing: the weapon or fist moving forward fast, the body twisting forward",
               "full strike: the arm fully extended forward, weight on the front foot",
               "recovering into a ready fighting stance after the strike"],
    "jump": ["crouching low with knees bent, about to jump", "pushing off the ground, body stretched upward, arms raised",
             "in the air at the top of the jump, knees tucked up", "landing with knees bent, absorbing the impact"],
    "hurt": ["recoiling from a hit, body bent backward, face in pain", "staggering backward, off balance"],
    "death": ["hit hard and staggering backward", "falling backward, knees buckling", "collapsing to the ground",
              "lying flat on the ground, defeated"],
    "cast": ["gathering magic with both hands held together in front of the chest", "raising one hand high, magic glowing in the palm",
             "thrusting the hand forward, releasing the spell", "returning to a ready stance"],
}
FACING_VIEW = {"right": VIEWS["right"], "left": VIEWS["right"], "down": VIEWS["down"], "up": VIEWS["up"]}


def _edit_instruction(change, extra=""):
    return (f"{change}. Keep exactly the same character: the same face, hair, outfit, colours, proportions, props and art style. "
            f"Full body visible from head to feet, centred, plain flat light grey background, no shadow, no text. {extra}").strip()


def _edit_frames(p, jobs, ref_rgba, ctx, name, work, t0):
    """jobs: [(frame_id, instruction)] -> {frame_id: best candidate}. Every frame is an edit of `ref_rgba`."""
    q = p["params"]
    n = max(1, int(q.get("frame_candidates", 1)))
    ref_name = _upload_reference(ref_rgba, f"spriteforge_ref_{name}.png")
    chosen, gen_s = {}, 0.0
    for j, (fid, instruction) in enumerate(jobs):
        tag = f"{name}_{fid}_{uuid.uuid4().hex[:6]}"
        fctx = {**ctx, "prompt": instruction, "ref_file": ref_name, "seed": ctx["seed"] + j, "candidates": n}
        pairs, s, _ = run_graph(p, p["graph"], p["set"], fctx, tag)
        gen_s += s
        cands = []
        for i, pair in enumerate(pairs):
            rgba = _cutout(pair, q)
            stats = sprite.analyse(rgba)
            path = sprite.save_png(rgba, os.path.join(work, f"{fid}_take_{i}.png"))
            cands.append({"index": i, "rgba": rgba, "file": path, "problems": sprite.problems_of(stats, "frame"), "stats": stats})
        best = _rank(cands, reference=ref_rgba)[0]
        log(f"  frame {fid}: take {best['index']} (likeness {best.get('likeness')})")
        chosen[fid] = best
    return chosen, gen_s


def _normalise(frames, cell, padding, same_height):
    """Fit frames into equal cells, feet on one ground line. same_height: every frame scaled to
    the same character height (turnarounds); otherwise one common scale (keeps crouch/jump heights)."""
    crops = [sprite.crop(f) for f in frames]
    avail = cell * (1 - 2 * padding)
    if same_height:
        scales = [avail / c.shape[0] for c in crops]
    else:
        s = min(avail / max(c.shape[0] for c in crops), avail / max(c.shape[1] for c in crops))
        scales = [s] * len(crops)
    k = min(1.0, *[avail / (c.shape[1] * s) for c, s in zip(crops, scales)])
    return [sprite.fit(c, cell, padding, "bottom", scale=s * k)[0] for c, s in zip(crops, scales)]


def _pixel_sheet(frames, q):
    """Pixel-art frames for a sheet: one grid and one palette for all of them."""
    cells, infos, pal = pixel.pixelize_frames(frames, q.get("pixel_grid"), int(q.get("pixel_colors", 16)),
                                              q.get("_palette"), bool(q.get("pixel_outline")), "bottom")
    if q.get("pixel_palette") == "project" and not q.get("_palette"):
        save_project_palette(pal, q.get("_project_dir"))
    problems = [f"frame {i}: {x}" for i, inf in enumerate(infos) for x in pixel.pixel_problems(inf)]
    return cells, problems, {"pixel": {"grid": q.get("pixel_grid"), "cell": infos[0].get("cell"),
                                       "frame_size": list(cells[0].shape[:2][::-1]), "palette_size": len(pal),
                                       "palette": q.get("pixel_palette") or "shared, chosen from all frames"}}


def _write_sheet(name, out, frames, ids, tags, fps, columns=None, preview_scale=0):
    folder = os.path.join(out, name)
    os.makedirs(folder, exist_ok=True)
    frame_files = [sprite.save_png(f, os.path.join(folder, f"{i}.png")) for f, i in zip(frames, ids)]
    sheet, atlas = sprite.pack_sheet(frames, [f"{name}/{i}" for i in ids], f"{name}_sheet.png", columns, tags, fps)
    sheet_path = sprite.save_png(sheet, os.path.join(out, f"{name}_sheet.png"))
    atlas_path = sprite.write_atlas(atlas, os.path.join(out, f"{name}.atlas.json"))
    files = {"sheet": sheet_path, "atlas": atlas_path, "frames": frame_files}
    if preview_scale > 1:
        files[f"sheet_x{preview_scale}"] = sprite.save_png(sprite.upscale_nearest(sheet, preview_scale),
                                                          os.path.join(out, f"{name}_sheet_x{preview_scale}.png"))
    return sheet, files


def _turnaround(p, name, prompt, ctx, character, out, t0):
    q = p["params"]
    order = ORDER8 if int(q.get("directions", 8)) == 8 else ORDER4
    ref = _load_png(character["reference"])
    work = os.path.join(out, ".spriteforge", name)
    os.makedirs(work, exist_ok=True)
    mirror = bool(q.get("mirror", True))
    todo = [d for d in order if d != "down" and not (mirror and d in MIRROR)]
    jobs = [(d, _edit_instruction(f"Show this character from another angle - {VIEWS[d]}", prompt or "")) for d in todo]
    chosen, gen_s = _edit_frames(p, jobs, ref, ctx, name, work, t0)
    frames, problems, report = {}, [], []
    frames["down"] = ref
    for d in order:
        if d == "down":
            continue
        if mirror and d in MIRROR:
            frames[d] = frames.get(MIRROR[d], chosen[MIRROR[d]]["rgba"])[:, ::-1].copy()
            report.append({"direction": d, "mirrored_from": MIRROR[d]})
            continue
        c = chosen[d]
        frames[d] = c["rgba"]
        likeness = c.get("likeness")
        flags = list(c["problems"])
        if likeness is not None and likeness < 0.75:
            flags.append(f"may not look like the character (likeness {likeness})")
        if likeness is not None and likeness > 0.985:
            flags.append("may not have turned: almost identical to the reference")
        problems += [f"{d}: {f}" for f in flags]
        report.append({"direction": d, "likeness": likeness, "problems": flags, "take": c["index"], "file": c["file"]})
    extra = {}
    if q.get("pixel_grid"):
        cells, pp, extra = _pixel_sheet([frames[d] for d in order], q)
        problems += pp
    else:
        cells = _normalise([frames[d] for d in order], int(q.get("cell", 256)), float(q.get("padding", 0.05)), True)
    sheet, files = _write_sheet(name, out, cells, order, [], int(q.get("fps", 8)), columns=len(order),
                                preview_scale=8 if q.get("pixel_grid") else 0)
    return sheet, files, problems, {"directions": order, "frames": report, **extra}, gen_s


def _animation(p, name, prompt, ctx, character, out, t0, project_dir):
    q = p["params"]
    action = (q.get("action") or "walk").lower()
    poses = q.get("poses") or ACTIONS.get(action)
    if not poses:
        raise ValueError(f"unknown action '{action}': use one of {', '.join(ACTIONS)} or give params.poses (one description per frame)")
    nframes = int(q.get("frames") or 0)
    if nframes and nframes != len(poses):
        idx = np.linspace(0, len(poses) - 1, nframes).round().astype(int)
        poses = [poses[i] for i in idx]
    facing = (q.get("facing") or "right").lower()
    if facing not in FACING_VIEW:
        raise ValueError("facing must be right, left, down or up")
    ref = _load_png(character["reference"])
    work = os.path.join(out, ".spriteforge", name)
    os.makedirs(work, exist_ok=True)
    # 1. the character turned to the facing direction (cached next to the character, reused by later animations)
    base_facing = "right" if facing == "left" else facing
    gen_s = 0.0
    if base_facing == "down":
        base = ref
    else:
        cache = os.path.join(os.path.dirname(character["reference"]), f"{character['character']}_{base_facing}.png")
        if os.path.isfile(cache):
            base = _load_png(cache)
        else:
            got, s = _edit_frames(p, [("base", _edit_instruction(f"Show this character from another angle - {VIEWS[base_facing]}"))],
                                  ref, ctx, f"{name}_base", work, t0)
            base, gen_s = got["base"]["rgba"], s
            sprite.save_png(base, cache)
    # 2. each key pose, edited from the turned character
    view = FACING_VIEW[base_facing].split(":")[0]
    jobs = [(f"{i}", _edit_instruction(f"Keep the {view} and change only the pose: {pose}", prompt or "")) for i, pose in enumerate(poses)]
    chosen, s = _edit_frames(p, jobs, base, ctx, name, work, t0)
    gen_s += s
    frames, problems, report = [], [], []
    for i in range(len(poses)):
        c = chosen[str(i)]
        f = c["rgba"][:, ::-1].copy() if facing == "left" else c["rgba"]
        frames.append(f)
        flags = list(c["problems"])
        if c.get("likeness") is not None and c["likeness"] < 0.75:
            flags.append(f"may not look like the character (likeness {c['likeness']})")
        problems += [f"frame {i}: {x}" for x in flags]
        report.append({"frame": i, "pose": poses[i], "likeness": c.get("likeness"), "problems": flags, "take": c["index"]})
    extra = {}
    if q.get("pixel_grid"):
        cells, pp, extra = _pixel_sheet(frames, q)
        problems += pp
    else:
        cells = _normalise(frames, int(q.get("cell", 256)), float(q.get("padding", 0.05)), False)
    ids = [f"{action}_{facing}_{i}" for i in range(len(cells))]
    tags = [{"name": f"{action}_{facing}", "from": 0, "to": len(cells) - 1, "direction": "forward"}]
    sheet, files = _write_sheet(name, out, cells, ids, tags, int(q.get("fps", 8)), preview_scale=8 if q.get("pixel_grid") else 0)
    return sheet, files, problems, {"action": action, "facing": facing, "frames": report, **extra}, gen_s


def _vfx_stages(n):
    peak = int(round(0.35 * (n - 1)))
    out = []
    for i in range(n):
        if i < peak:
            f = (i + 1) / (peak + 1)
            out.append(f"an earlier moment of this same effect, just starting: about {int(f * 100)} percent of its full size, bright and expanding from the centre")
        elif i == peak:
            out.append(None)
        else:
            g = (i - peak) / max(1, n - 1 - peak)
            out.append("a later moment of this same effect, " + (
                "starting to break apart and fade, less bright, more smoke and wisps" if g < 0.5 else
                "mostly dissipated: faint wisps, smoke and a few sparks" if g < 0.95 else "almost gone: only faint traces left"))
    return out, peak


def _vfx(p, name, prompt, ctx, out, t0):
    q = p["params"]
    n = max(2, int(q.get("frames", 6)))
    stages = q.get("stages")
    peak_i = None
    if stages:
        stages = list(stages)
    else:
        stages, peak_i = _vfx_stages(n)
    work = os.path.join(out, ".spriteforge", name)
    os.makedirs(work, exist_ok=True)
    k = max(1, int(q.get("candidates", 1)))
    pairs, gen_s, _ = run_graph(p, p["graph"], p["set"], {**ctx, "candidates": k}, f"{name}_peak_{uuid.uuid4().hex[:6]}")
    cands = []
    for i, pair in enumerate(pairs):
        rgba = _cutout(pair, q)
        cands.append({"index": i, "rgba": rgba, "problems": sprite.problems_of(sprite.analyse(rgba), "vfx"),
                      "file": sprite.save_png(rgba, os.path.join(work, f"peak_take_{i}.png")), "raw": pair[0]})
    peak = _rank(cands, text=prompt)[0]
    peak_rgb = _load_png(peak["raw"])[..., :3]
    ref = np.dstack([peak_rgb, np.ones(peak_rgb.shape[:2], np.float32)])
    jobs = [(str(i), f"Show {s}. Keep the same colours, style and pure black background, centred, nothing else in the picture")
            for i, s in enumerate(stages) if s]
    ep = {**p, "graph": p.get("edit_graph") or p["graph"], "set": p.get("edit_set") or p["set"]}
    os.makedirs(comfy.input_dir(), exist_ok=True)
    ref_name = f"spriteforge_ref_{name}.png"
    sprite.to_pil_rgb(peak_rgb).save(os.path.join(comfy.input_dir(), ref_name))
    frames_full = {}
    for j, (fid, instr) in enumerate(jobs):
        pr, s, _ = run_graph(ep, ep["graph"], ep["set"], {**ctx, "prompt": instr, "ref_file": ref_name, "seed": ctx["seed"] + 1 + j,
                                                         "candidates": 1}, f"{name}_{fid}_{uuid.uuid4().hex[:6]}")
        gen_s += s
        frames_full[fid] = _cutout(pr[0], q)
    cell = int(q.get("cell", 256))
    frames, ids = [], []
    for i, st in enumerate(stages):
        f = peak["rgba"] if st is None else frames_full[str(i)]
        frames.append(sprite._resize(f, cell, cell))  # whole canvas: keeps each stage's size and position
        ids.append(f"{name}_{i}")
    tags = [{"name": name, "from": 0, "to": len(frames) - 1, "direction": "forward"}]
    sheet, files = _write_sheet(name, out, frames, ids, tags, int(q.get("fps", 12)))
    problems = [f"peak: {x}" for x in peak["problems"]]
    empties = [i for i, f in enumerate(frames) if f[..., 3].max() < 0.1]
    if empties:
        problems.append(f"frames {empties} are empty")
    return sheet, files, problems, {"frames": [{"frame": i, "stage": s or "peak"} for i, s in enumerate(stages)],
                                     "peak_frame": peak_i}, gen_s


# ----------------------------------------------------------------------------- entry point

def generate_sprite(profile_id, name, prompt=None, project_dir=None, out_dir=None, seed=None, style="", params=None,
                    force=False, preset="standard", style_preset=None, character=None, character_description=None):
    """Text -> game-ready 2D asset. Returns a manifest dict (also written as <name>.json)."""
    name = safe_name(name)
    p = profiles.resolve(profile_id, project_dir, params, preset=preset, style=style_preset)
    kind = p.get("kind")
    if kind not in SINGLE_KINDS + ("sheet", "vfx"):
        raise ValueError(f"'{profile_id}' is not a sprite profile")
    if kind != "sheet" and not prompt:
        raise ValueError("give a prompt describing the asset")
    out = out_dir_for(project_dir, out_dir)
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, f"{name}.png")
    manifest_path = os.path.join(out, f"{name}.json")

    char = None
    if kind == "sheet":
        if not character:
            raise ValueError(f"profile '{profile_id}' needs `character` (a saved character id; create one with create_character)")
        char = character_info(character, project_dir)
        if not char:
            if not character_description:
                raise ValueError(f"character '{character}' does not exist yet: give character_description to create it first")
            made = create_character(character, character_description, project_dir, seed, style_preset=style_preset, style=style,
                                    profile="pixel_character" if p["params"].get("pixel_grid") else "character")
            if made.get("status") != "ok":
                return {"name": name, "status": "failed_check", "problems": ["the new character reference failed its check: "
                                                                             + "; ".join(made.get("problems", []))], "character": made}
            char = character_info(character, project_dir)

    seed_given = seed is not None
    seed = random.randint(0, 2**31 - 1) if seed is None else int(seed)
    for k in ("palette", "pixel_palette"):
        if k in p["params"]:
            p["params"]["_" + k.replace("pixel_", "")] = resolve_palette(p["params"][k], project_dir)
    p["params"]["_project_dir"] = project_dir
    ctx = {**p["params"], "seed": seed}
    ctx.setdefault("view", "")
    ctx.setdefault("background", "plain flat light grey background")
    if kind not in ("sheet",):
        ctx["prompt"] = build_prompt(p, prompt, ctx, style, p.get("style_text", ""))
    else:
        ctx["prompt"] = ""
        extra = ", ".join(t for t in (p.get("style_text"), style) if t)
        prompt = ". ".join(t for t in ((prompt or "").strip(), extra) if t)

    key = _settings_key(p, prompt, {"character": char and char["character"], "character_ref": char and os.path.getmtime(char["reference"]),
                                    "style": style, "seed": seed if seed_given else None})
    if os.path.isfile(manifest_path) and not force:
        old = json.load(open(manifest_path, encoding="utf-8"))
        if old.get("settings_key") == key and os.path.isfile(old.get("file", "")):
            return {**old, "cached": True}

    if kind == "sheet" and p["params"].get("pixel_grid") and p["graph"].endswith("klein_edit.json"):
        # pixel-art sheets: the same Klein edit, with the pixel-art LoRA so frames stay in style
        p["graph"] = p["graph"].replace("klein_edit.json", "klein_edit_lora.json")
        p["set"] = {**p["set"], "30.lora_name": "{lora}", "30.strength_model": "{lora_strength}"}
    comfy.ensure_server()
    t0 = time.time()
    if kind in SINGLE_KINDS:
        body = _single(p, name, prompt, ctx, out, dst, key, seed, t0)
    else:
        if kind == "sheet" and (p.get("sheet") or p["id"]) == "animation":
            sheet, files, problems, detail, gen_s = _animation(p, name, prompt, ctx, char, out, t0, project_dir)
        elif kind == "sheet":
            sheet, files, problems, detail, gen_s = _turnaround(p, name, prompt, ctx, char, out, t0)
        else:
            sheet, files, problems, detail, gen_s = _vfx(p, name, prompt, ctx, out, t0)
        work = os.path.join(out, ".spriteforge", name)
        os.makedirs(work, exist_ok=True)
        body = {"status": "ok" if not problems else "failed_check", "file": files["sheet"], "files": files,
                "preview": _preview(sheet, os.path.join(work, "preview.png"), 768), "problems": problems,
                "sheet": {"width": int(sheet.shape[1]), "height": int(sheet.shape[0]), "frames": len(files["frames"]),
                          "cell": int(p["params"].get("cell", 256))}, **detail,
                "timings": {"generation_s": round(gen_s, 1), "total_s": round(time.time() - t0, 1)}}
    result = {"name": name, **body, "profile": p["id"], "preset": p["preset"], "kind": kind, "style_preset": p.get("style_preset"),
              "prompt": prompt, "model_prompt": ctx.get("prompt") or None, "seed": seed,
              "character": char and {k: char.get(k) for k in ("character", "description", "reference")},
              "settings": {k: v for k, v in p["params"].items() if not k.startswith("_")},
              "limited_by_vram": p.get("limited_by_vram") or None, "style_ignored": p.get("style_ignored") or None,
              "preset_note": p.get("preset_note"), "settings_key": key}
    result = {k: v for k, v in result.items() if v is not None}
    json.dump(result, open(manifest_path, "w", encoding="utf-8"), indent=2, default=str)
    return result


def generate_batch(jobs, project_dir=None, out_dir=None, style="", force=False):
    """jobs: [{"name", "prompt", "profile"?, "preset"?, "params"?, "character"?, ...}]. Grouped by model."""
    order = []
    for i, job in enumerate(jobs):
        group = profiles.resolve(job.get("profile", "sprite"), project_dir, preset=job.get("preset", "standard")).get("vram_group", "")
        order.append((group, i, job))
    results = [None] * len(jobs)
    for _g, i, job in sorted(order, key=lambda t: (t[0], t[1])):
        try:
            results[i] = generate_sprite(job.get("profile", "sprite"), job["name"], job.get("prompt"), project_dir, out_dir,
                                         job.get("seed"), job.get("style", style), job.get("params"), job.get("force", force),
                                         job.get("preset", "standard"), job.get("style_preset"), job.get("character"),
                                         job.get("character_description"))
        except Exception as e:
            results[i] = {"name": job.get("name"), "status": "error", "error": f"{type(e).__name__}: {e}"}
    return results


def check_file(path, kind="sprite"):
    """Measure any PNG: size, transparency coverage, edge contact, separate shapes, tile seam."""
    from PIL import Image
    img = Image.open(path)
    rgba = np.asarray(img.convert("RGBA"), np.float32) / 255.0
    stats = sprite.analyse(rgba)
    out = {"status": "ok", "image": {**stats, "mode": img.mode, "has_alpha": img.mode in ("RGBA", "LA", "P")}}
    problems = sprite.problems_of(stats, kind) if kind != "tile" else []
    if kind == "tile":
        out["image"]["seam"] = sprite.seam_score(rgba[..., :3])
        if out["image"]["seam"] > 2.0:
            problems.append(f"visible seam (score {out['image']['seam']})")
    out["problems"] = problems
    return out

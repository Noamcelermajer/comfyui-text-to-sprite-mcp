"""MCP server over stdio (JSON-RPC 2.0, one message per line). No dependencies.

Generation takes minutes, which is longer than most clients wait for a tool call,
so generate_sprite and create_character start a background job and wait only `wait_seconds`; `get_job`
picks the result up later. One job runs at a time (one GPU).
"""
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid

from . import comfy, models, pipeline, profiles
from .config import load_machine

PROTOCOL = "2025-06-18"
_out_lock = threading.Lock()
_jobs, _queue = {}, queue.Queue()


def _send(msg):
    with _out_lock:
        sys.__stdout__.write(json.dumps(msg) + "\n")
        sys.__stdout__.flush()


def _worker():
    while True:
        job = _jobs[_queue.get()]
        job["status"], job["started"] = "running", time.time()
        try:
            job["result"] = job["fn"]()
            job["status"] = job["result"].get("status", "ok")
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        job["finished"] = time.time()


def _submit(kind, name, fn, wait_seconds):
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"id": job_id, "kind": kind, "name": name, "status": "queued", "fn": fn, "created": time.time()}
    _queue.put(job_id)
    return _job_view(job_id, wait_seconds)


def _job_view(job_id, wait_seconds=0):
    if job_id not in _jobs:
        raise ValueError(f"no job '{job_id}' (jobs are forgotten when the server restarts; the files and <name>.json stay on disk)")
    job = _jobs[job_id]
    deadline = time.time() + max(0, min(float(wait_seconds or 0), 55))
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(1)
    view = {k: job[k] for k in ("id", "kind", "name", "status") if k in job}
    images = []
    if job["status"] in ("queued", "running"):
        view["elapsed_s"] = round(time.time() - job.get("started", job["created"]))
        view["queue_position"] = sum(1 for j in _jobs.values() if j["status"] == "queued" and j["created"] <= job["created"])
        view["next"] = f"call get_job with id '{job_id}' and wait_seconds 50 until status is no longer queued/running"
    elif "error" in job:
        view["error"] = job["error"]
    else:
        view["result"] = job["result"]
        images = _image_block(job["result"].get("preview"))
    return view, images


# ----------------------------------------------------------------------------- tools

def _image_block(path, max_px=512):
    """The result's preview as an MCP image, so the agent can look at what it made."""
    import base64
    import io
    from PIL import Image
    if not path or not os.path.isfile(path):
        return []
    img = Image.open(path)
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return [{"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/png"}]


def t_status(a):
    m = load_machine()
    return {"gpu": m["gpu"], "ram_gb": m["ram_gb"], "vram_tier": m["tier"], "comfyui_running": comfy.is_up(),
            "comfyui": m["comfy_root"],
            "jobs": [{k: j[k] for k in ("id", "kind", "name", "status")} for j in _jobs.values()][-10:],
            "project": profiles.load_project(a.get("project_dir")) if a.get("project_dir") else None}, []


def t_list_profiles(a):
    return {"profiles": profiles.list_profiles(a.get("project_dir")),
            "styles": profiles.list_styles(a.get("project_dir")),
            "characters": [{k: c.get(k) for k in ("character", "description", "reference")}
                           for c in pipeline.list_characters(a.get("project_dir"))]}, []


def t_generate_sprite(a):
    fn = lambda: pipeline.generate_sprite(a.get("profile", "sprite"), a["name"], a.get("prompt"), a.get("project_dir"),
                                          a.get("out_dir"), a.get("seed"), a.get("style", ""), a.get("params"), a.get("force", False),
                                          a.get("preset", "standard"), a.get("style_preset"), a.get("character"),
                                          a.get("character_description"))
    return _submit("sprite", a["name"], fn, a.get("wait_seconds", 50))


def t_create_character(a):
    fn = lambda: pipeline.create_character(a["character"], a.get("description"), a.get("project_dir"), a.get("seed"),
                                           a.get("preset", "standard"), a.get("style_preset"), a.get("style", ""),
                                           a.get("params"), a.get("force", False))
    return _submit("character", a["character"], fn, a.get("wait_seconds", 50))


def t_animate_pixel(a):
    fn = lambda: pipeline.animate_pixel(a["name"], a["image"], a.get("description"), a.get("project_dir"), a.get("out_dir"),
                                        a.get("seed"), a.get("params"), a.get("force", False), a.get("preset", "standard"),
                                        a.get("poses"))
    return _submit("animate", a["name"], fn, a.get("wait_seconds", 50))


def t_normal_map(a):
    pix = a.get("pixel_art")
    r = pipeline.make_normal_map(a["path"], a.get("out_path"), pix, float(a.get("strength", 1.0)), a.get("y", "up") == "up")
    return r, _image_block(r["preview"])


def t_get_job(a):
    return _job_view(a["id"], a.get("wait_seconds", 50))


def t_check_image(a):
    return pipeline.check_file(a["path"], a.get("kind", "sprite")), (_image_block(a["path"]) if a.get("show") else [])


def t_models(a):
    if a.get("install"):
        return {"installed": models.install(a["install"])}, []
    return models.report(a.get("project_dir")), []


_PROJECT = {"type": "string", "description": "Absolute path of the game project. Its spriteforge.json (style, output folder, characters folder, per-profile settings) is applied and files are written into its output folder."}
_WAIT = {"type": "number", "description": "Seconds to wait for the result before returning a job id (max 55, default 50)."}
_COMMON = {
    "preset": {"type": "string", "enum": ["fast", "standard", "max"],
               "description": "fast = one quick take; standard = the profile's tuned default; max = the bigger model and/or more takes."},
    "style_preset": {"type": "string", "description": "Style id from list_profiles['styles'] (cartoon, hand_painted, flat_vector, dark_fantasy, sci_fi, anime). Defaults to the project's."},
    "style": {"type": "string", "description": "Extra wording added to the prompt after the style preset and the project's style."},
    "params": {"type": "object", "description": "Profile parameters to override, e.g. {\"size\": 256, \"candidates\": 6, \"grid\": 32, \"palette\": \"pico8\", \"directions\": 4, \"action\": \"attack\"}. list_profiles shows each profile's parameters."},
    "seed": {"type": "integer", "description": "Fix the result; change it for a different take."},
    "project_dir": _PROJECT,
    "force": {"type": "boolean", "description": "Regenerate even if a file with the same name and settings exists."},
    "wait_seconds": _WAIT,
}

TOOLS = {
    "forge_status": (t_status, "Hardware, whether ComfyUI is running, recent jobs, and a project's settings.",
                     {"project_dir": _PROJECT}, []),
    "list_profiles": (t_list_profiles, "The 2D asset profiles (sprite, icon, pixel_sprite, character, turnaround, animation, tile, portrait, vfx) with their parameters, presets and prompt guides; the style presets; and the saved characters. Call this first.",
                      {"project_dir": _PROJECT}, []),
    "generate_sprite": (t_generate_sprite,
                        "Text -> game-ready 2D asset: makes several takes, keeps the one that best matches the prompt and passes the checks, cuts it out (transparent PNG), crops and sizes it. Pixel art is snapped to a real pixel grid and palette; turnaround/animation/vfx return frames + a sprite sheet + a JSON atlas. Single sprites take seconds, sheets a minute or more. Returns the result with a preview image, or a job id to poll with get_job.",
                        {"profile": {"type": "string", "description": "sprite (default), icon, pixel_sprite, character, turnaround, animation, tile, portrait or vfx."},
                         "name": {"type": "string", "description": "Asset id; becomes the file name."},
                         "prompt": {"type": "string", "description": "What to draw (see the profile's prompt_guide). Optional for turnaround/animation (extra guidance)."},
                         "character": {"type": "string", "description": "turnaround/animation: a saved character id (list_profiles['characters']). If it does not exist it is created from character_description."},
                         "character_description": {"type": "string", "description": "turnaround/animation: who the character is, outfit, colours, props - used to create a new character."},
                         "out_dir": {"type": "string", "description": "Output folder; overrides the project's."},
                         **_COMMON}, ["name"]),
    "create_character": (t_create_character, "Create (or, with force, replace) a named character: a full-body reference on a transparent background that every later turnaround and animation of this character is made from, so it keeps one look.",
                         {"character": {"type": "string", "description": "Character id, e.g. ranger."},
                          "description": {"type": "string", "description": "Who they are, then outfit, colours and props."},
                          **_COMMON}, ["character", "description"]),
    "animate_pixel": (t_animate_pixel,
                      "Animate an EXISTING pixel-art sprite (a PNG up to 256 px, or an enlarged copy with hard pixels) from a description of the motion. Every frame is redrawn from the sprite and snapped back onto its own pixel grid and palette, so frames match the original exactly. Returns frames, a sheet, a JSON atlas, a GIF and a preview; takes about 15-20 s per frame.",
                      {"name": {"type": "string", "description": "Animation id; becomes the file names."},
                       "image": {"type": "string", "description": "Absolute path of the pixel-art PNG to animate."},
                       "description": {"type": "string", "description": "The motion, e.g. 'swinging the sword down in front of him', 'bouncing and squashing', 'the flame flickering'. walk/run/idle/attack/jump/hurt/death/cast use built-in key poses."},
                       "poses": {"type": "array", "items": {"type": "string"}, "description": "Best results: one short description per frame of what changes, in order (write these from the user's description). Overrides frames."},
                       "out_dir": {"type": "string", "description": "Output folder; overrides the project's."},
                       **{k: v for k, v in _COMMON.items() if k != "style_preset" and k != "style"}}, ["name", "image", "description"]),
    "make_normal_map": (t_normal_map,
                        "Normal map for 2D lighting (Godot CanvasTexture / Light2D, Unity 2D lights) for any sprite PNG, plus a preview lit from four sides. Pixel art keeps its pixel grid with crisp, snapped normals. (Sprite profiles can also make one directly with params.normal_map = true.)",
                        {"path": {"type": "string", "description": "Absolute path of the sprite PNG (transparent background)."},
                         "pixel_art": {"type": "boolean", "description": "Pixel-art mode; default: yes when the image is 256 px or smaller."},
                         "strength": {"type": "number", "description": "Relief strength, 0.5 subtle - 2 strong (default 1)."},
                         "y": {"type": "string", "enum": ["up", "down"], "description": "up = OpenGL, green up (Godot, Unity, default); down = DirectX."},
                         "out_path": {"type": "string", "description": "Where to write <name>_normal.png; default next to the sprite."}}, ["path"]),
    "get_job": (t_get_job, "Status or result of a generate_sprite / create_character / animate_pixel job.",
                {"id": {"type": "string"}, "wait_seconds": _WAIT}, ["id"]),
    "check_image": (t_check_image, "Measure any PNG: size, transparency coverage, whether the subject touches the edge (cut off), separate shapes, tile seam visibility, and problems. show=true also returns the image.",
                    {"path": {"type": "string", "description": "Absolute path of the file."},
                     "kind": {"type": "string", "description": "sprite, icon, character, pixel, tile or vfx (selects the checks)."},
                     "show": {"type": "boolean"}}, ["path"]),
    "models": (t_models, "Which model files each profile and preset needs, what is missing, and advice for this GPU. Pass install=[names] to download catalog items (large; ask the user first).",
               {"project_dir": _PROJECT, "install": {"type": "array", "items": {"type": "string"}, "description": "Catalog file names or repo ids to download."}}, []),
}


def _handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        return {"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "sprite-forge", "version": "0.1.0"},
                "instructions": ("Local 2D game asset generation: sprites, icons, pixel art, characters with 4/8 directions and "
                                 "animation sheets, seamless tiles, portraits, VFX frames, normal maps, and animating existing pixel sprites. Start with list_profiles. Pass project_dir "
                                 "so the project's style, output and characters folders apply. Longer jobs return a job id: poll get_job. "
                                 "Look at the returned preview before calling an asset done.")}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req}}
                          for n, (_f, d, props, req) in TOOLS.items()]}
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            raise LookupError(f"unknown tool {name}")
        try:
            data, images = TOOLS[name][0](args)
            return {"content": [{"type": "text", "text": json.dumps(data, indent=1)}, *images], "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if mid is None:
        return None  # a notification we do not need
    raise LookupError(f"method not found: {method}")


def serve():
    sys.stdout = sys.stderr  # nothing but protocol messages may reach the real stdout
    threading.Thread(target=_worker, daemon=True).start()
    pending = []
    for line in sys.stdin:
        line = line[line.find("{"):].strip() if "{" in line else ""  # tolerate a byte-order mark
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "method" not in msg:
            continue
        mid = msg.get("id")

        def run(msg=msg, mid=mid):
            try:
                result = _handle(msg)
                if mid is not None:
                    _send({"jsonrpc": "2.0", "id": mid, "result": result})
            except LookupError as e:
                if mid is not None:
                    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": str(e)}})
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                if mid is not None:
                    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})

        if msg["method"] == "tools/call":
            # tool calls may block while waiting on a job; keep the reader loop free
            t = threading.Thread(target=run, daemon=True)
            t.start()
            pending.append(t)
        else:
            run()
    for t in pending:  # stdin closed: let calls in flight answer before exiting
        t.join(timeout=60)

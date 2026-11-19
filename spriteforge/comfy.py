"""Talking to ComfyUI: start it, run a graph, time each node, free VRAM, upload inputs."""
import asyncio
import glob
import json
import os
import subprocess
import time
import urllib.request
import uuid

from .config import FORGE_ROOT, load_machine, log

class ComfyError(RuntimeError):
    pass


def url():
    return load_machine()["comfy_url"]


def get(path, timeout=15):
    with urllib.request.urlopen(f"{url()}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_up():
    try:
        get("/system_stats", timeout=4)
        return True
    except Exception:
        return False


def ensure_server(wait_s=300):
    if is_up():
        return
    m = load_machine()
    if not m["comfy_root"]:
        raise ComfyError("ComfyUI was not found. Set comfy_root in machine.json.")
    log("ComfyUI is not running - starting it ...")
    cmd = [m["comfy_python"], "-I", "-W", "ignore::FutureWarning", os.path.join(m["comfy_root"], "main.py"),
           "--port", m["comfy_url"].rsplit(":", 1)[-1], *m.get("comfy_launch_args", [])]
    logf = open(os.path.join(FORGE_ROOT, "comfyui.log"), "ab")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(cmd, cwd=os.path.dirname(m["comfy_root"]), stdout=logf, stderr=logf,
                     stdin=subprocess.DEVNULL, creationflags=flags)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(3)
        if is_up():
            return
    raise ComfyError("ComfyUI did not come up in time; see comfyui.log")


_object_info = {}


def object_info():
    if not _object_info:
        _object_info.update(get("/object_info", timeout=90))
    return _object_info


def free_vram():
    """Drop cached weights. Mesh nodes allocate VRAM outside ComfyUI's model
    manager, so leftover weights from another stage make them run out."""
    req = urllib.request.Request(
        f"{url()}/free", data=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    time.sleep(2)


async def _run_ws(prompt):
    import aiohttp

    client_id = uuid.uuid4().hex
    base = url()
    timings = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        async with session.ws_connect(base.replace("http", "ws", 1) + f"/ws?clientId={client_id}", max_msg_size=0) as ws:
            async with session.post(f"{base}/prompt", json={"prompt": prompt, "client_id": client_id}) as r:
                body = await r.json(content_type=None)
                if r.status != 200:
                    raise ComfyError("ComfyUI rejected the graph: " + json.dumps(body)[:1500])
                prompt_id = body["prompt_id"]
            current, t_last = None, None

            def close(now):
                if current is not None and t_last is not None:
                    timings[current] = timings.get(current, 0.0) + now - t_last

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                m = json.loads(msg.data)
                d = m.get("data") or {}
                if d.get("prompt_id") != prompt_id:
                    continue
                now, kind = time.time(), m.get("type")
                if kind == "execution_start":
                    t_last = now
                elif kind == "executing":
                    close(now)
                    current, t_last = d.get("node"), now
                    if current is None:
                        break
                elif kind == "execution_success":
                    close(now)
                    break
                elif kind == "execution_error":
                    raise ComfyError(f"node {d.get('node_id')} ({d.get('node_type')}) failed: "
                                     f"{d.get('exception_type')}: {str(d.get('exception_message'))[:500]}")
                elif kind == "execution_interrupted":
                    raise ComfyError("the run was interrupted in ComfyUI")
    return prompt_id, timings


def _run_poll(prompt):
    req = urllib.request.Request(f"{url()}/prompt", data=json.dumps({"prompt": prompt}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        prompt_id = json.loads(urllib.request.urlopen(req).read())["prompt_id"]
    except urllib.error.HTTPError as e:
        raise ComfyError("ComfyUI rejected the graph: " + e.read().decode("utf-8", "replace")[:1500])
    return prompt_id, {}


def run_graph(prompt):
    """Run an API-format graph to completion. Returns (seconds, [(node, seconds)...])."""
    t0 = time.time()
    try:
        import aiohttp  # noqa: F401
        prompt_id, timings = asyncio.run(_run_ws(prompt))
    except ImportError:  # plain Python without aiohttp: no per-node timings
        prompt_id, timings = _run_poll(prompt)
    while True:  # the history is the source of truth for completion
        status = get(f"/history/{prompt_id}").get(prompt_id, {}).get("status", {})
        if status.get("completed"):
            break
        if status.get("status_str") == "error":
            msgs = [m[1] for m in status.get("messages", []) if m and m[0] == "execution_error"]
            d = msgs[0] if msgs else {}
            raise ComfyError(f"node {d.get('node_id')} ({d.get('node_type')}) failed: "
                             f"{d.get('exception_type')}: {str(d.get('exception_message'))[:500]}")
        time.sleep(2)
    steps = sorted(((f"{nid}:{prompt.get(nid, {}).get('class_type', '?')}", round(s, 1)) for nid, s in timings.items()),
                   key=lambda kv: -kv[1])
    return round(time.time() - t0, 1), steps


def input_dir():
    return os.path.join(load_machine()["comfy_root"], "input")


def newest_output(prefix, ext):
    out = os.path.join(load_machine()["comfy_root"], "output")
    files = glob.glob(os.path.join(out, f"{prefix}*{ext}"))
    return max(files, key=os.path.getmtime) if files else None


def outputs_of(prefix, ext):
    """Every file a run saved under `prefix` (a batch saves one file per item), oldest first."""
    out = os.path.join(load_machine()["comfy_root"], "output")
    return sorted(glob.glob(os.path.join(out, f"{prefix}*{ext}")), key=os.path.getmtime)

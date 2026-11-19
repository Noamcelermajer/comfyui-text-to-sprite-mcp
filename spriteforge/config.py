"""Machine config: where ComfyUI is, and what hardware this is.

Written once to machine.json by `forge.py doctor`; edit that file to change anything.
"""
import ctypes
import json
import os
import subprocess
import sys

FORGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACHINE_FILE = os.path.join(FORGE_ROOT, "machine.json")

# VRAM tiers, smallest first. A machine gets the largest tier it meets.
TIERS = [8, 12, 16, 24]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def detect_gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout.strip().splitlines()
        gpus = [(l.split(",")[0].strip(), int(l.split(",")[1])) for l in out if "," in l]
        if gpus:
            name, mib = max(gpus, key=lambda g: g[1])
            return {"name": name, "vram_gb": round(mib / 1024, 1)}
    except Exception:
        pass
    return {"name": "unknown", "vram_gb": 0}


def detect_ram_gb():
    try:
        if os.name == "nt":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "tp", "ap", "tv", "av", "ext")]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return round(ms.total / 2**30)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
    except Exception:
        return 0


def tier_for(vram_gb):
    fit = [t for t in TIERS if vram_gb >= t - 0.5]
    return fit[-1] if fit else TIERS[0]


def find_comfy():
    env = os.environ.get("SPRITEFORGE_COMFY")
    candidates = [env] if env else []
    here = FORGE_ROOT
    for _ in range(3):
        here = os.path.dirname(here)
        candidates += [os.path.join(here, "ComfyUI"), here]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "main.py")) and os.path.isdir(os.path.join(c, "models")):
            return c
    return ""




def detect_machine():
    comfy = find_comfy()
    embedded = ""
    if comfy:  # portable build, then the usual virtual-environment locations
        for cand in (os.path.join(os.path.dirname(comfy), "python_embeded", "python.exe"),
                     os.path.join(comfy, "venv", "Scripts", "python.exe"), os.path.join(comfy, ".venv", "Scripts", "python.exe"),
                     os.path.join(comfy, "venv", "bin", "python"), os.path.join(comfy, ".venv", "bin", "python")):
            if os.path.isfile(cand):
                embedded = cand
                break
    gpu = detect_gpu()
    return {
        "comfy_root": comfy,
        "comfy_url": "http://127.0.0.1:8188",
        "comfy_python": embedded or sys.executable,
        "comfy_launch_args": [],
        "gpu": gpu,
        "ram_gb": detect_ram_gb(),
        "tier": tier_for(gpu["vram_gb"]),
    }


def load_machine(refresh=False):
    if not refresh and os.path.isfile(MACHINE_FILE):
        return json.load(open(MACHINE_FILE, encoding="utf-8-sig"))
    m = detect_machine()
    if os.path.isfile(MACHINE_FILE):  # keep hand edits, refresh only the detected hardware
        old = json.load(open(MACHINE_FILE, encoding="utf-8-sig"))
        # keep hand edits, but an empty saved value never hides something now detected
        m = {**m, **{k: v for k, v in old.items() if k not in ("gpu", "ram_gb", "tier") and v not in ("", None)}}
        if old.get("comfy_root") in ("", None) and m["comfy_root"]:
            m["comfy_python"] = detect_machine()["comfy_python"]
    json.dump(m, open(MACHINE_FILE, "w", encoding="utf-8"), indent=2)
    return m


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out

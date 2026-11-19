r"""Smoke test: one job per profile, with timings and peak VRAM.

    python tests/smoke_test.py                 every job at the standard preset
    python tests/smoke_test.py --preset max    every job at max
    python tests/smoke_test.py --only pixel,tile

Output: tests/results/<run>/ with the assets, their manifests, previews and results.json.
Peak VRAM is the whole GPU's (nvidia-smi), so close other GPU programs first.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from spriteforge import comfy, pipeline  # noqa: E402

CHAR = ("smoke_ranger", "a young female ranger with short red hair, a green hooded cloak, brown leather armour and boots, "
        "a longbow on her back")
JOBS = [
    ("sprite_potion", "sprite", "a glowing blue mana potion in a round glass flask with a cork stopper", {}, {}),
    ("sprite_chest", "sprite", "a wooden treasure chest with brass corners and a lock, lid closed", {}, {}),
    ("icon_fire_sword", "icon", "a flaming sword", {}, {}),
    ("pixel_knight", "pixel_sprite", "a knight in silver armour with a red cape and a sword", {"grid": 32}, {}),
    ("pixel_slime_pico8", "pixel_sprite", "a cute green slime monster with big eyes", {"grid": 24, "palette": "pico8", "outline": True}, {}),
    ("tile_cobblestone", "tile", "mossy cobblestone path", {}, {}),
    ("tile_grass_pixel", "tile", "short green grass with small flowers", {"pixelize": True, "grid": 32}, {}),
    ("portrait_dwarf", "portrait", "a grizzled dwarf blacksmith with a braided grey beard, soot on his cheeks, warm forge light", {}, {}),
    ("character_ranger", "character", CHAR[1], {}, {}),
    ("turnaround_ranger", "turnaround", None, {"directions": 8}, {"character": CHAR[0], "character_description": CHAR[1]}),
    ("walk_ranger", "animation", None, {"action": "walk", "facing": "right"}, {"character": CHAR[0]}),
    ("vfx_explosion", "vfx", "fiery orange explosion with black smoke and sparks", {"frames": 6}, {}),
]


class VramPeak:
    def __init__(self):
        self.peak, self._stop = 0, False

    def _run(self):
        while not self._stop:
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout
                self.peak = max(self.peak, max(int(x) for x in out.split()))
            except Exception:
                pass
            time.sleep(0.5)

    def __enter__(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def __exit__(self, *a):
        self._stop = True


def run(only=None, preset="standard"):
    stamp = time.strftime("%Y%m%d_%H%M")
    out = os.path.join(HERE, "results", f"{stamp}_{preset}")
    rows = []
    comfy.ensure_server()
    for name, profile, prompt, params, extra in JOBS:
        if only and not any(o in name for o in only.split(",")):
            continue
        t0 = time.time()
        with VramPeak() as vp:
            try:
                r = pipeline.generate_sprite(profile, name, prompt, out_dir=out, seed=1234, params=params or None, preset=preset,
                                             force=True, project_dir=None, **extra)
            except Exception as e:
                import traceback
                traceback.print_exc()
                r = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        row = {"job": name, "profile": profile, "status": r.get("status"), "error": r.get("error"),
               "wall_s": round(time.time() - t0, 1), "peak_vram_gb": round(vp.peak / 1024, 1), "problems": r.get("problems"),
               "image": r.get("image") or r.get("sheet"), "candidates": [(c.get("clip"), c.get("problems")) for c in r.get("candidates", [])],
               "frames": r.get("frames"), "file": r.get("file"), "preview": r.get("preview"), "timings": r.get("timings")}
        rows.append(row)
        print(json.dumps(row, default=str), flush=True)
        os.makedirs(out, exist_ok=True)
        json.dump(rows, open(os.path.join(out, "results.json"), "w", encoding="utf-8"), indent=2, default=str)
    print(json.dumps({"status": "ok", "results": os.path.join(out, "results.json")}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--preset", default="standard", choices=["fast", "standard", "max"])
    a = ap.parse_args()
    run(a.only, a.preset)

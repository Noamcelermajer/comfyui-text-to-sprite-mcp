"""Resumable, parallel model downloads.

Hugging Face serves each connection at a capped rate, so a large file is fetched as
ranges over several connections at once (about 3x faster on a slow link). Finished
ranges survive an interruption: running again continues where it stopped.
"""
import concurrent.futures as cf
import os
import time
import urllib.request

from .config import log

CHUNK = 64 << 20
UA = {"User-Agent": "sprite-forge"}


def _size(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers.get("Content-Length", 0)), r.headers.get("Accept-Ranges", "") == "bytes"


def _fetch(url, start, end, path):
    want = end - start + 1
    for attempt in range(8):
        have = os.path.getsize(path) if os.path.isfile(path) else 0
        if have >= want:
            return
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={start + have}-{end}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "ab") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
        except Exception as e:  # dropped connection: continue from what arrived
            if attempt == 7:
                raise RuntimeError(f"range {start}-{end}: {e}")
            time.sleep(5 * (attempt + 1))


def download(url, dst, label=None, connections=6):
    """Download `url` to `dst` (atomic rename at the end). Returns dst."""
    label = label or os.path.basename(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    total, ranges = _size(url)
    if not total or not ranges:  # server will not split: one stream
        _fetch(url, 0, max(total, 1) - 1, dst + ".part") if total else None
        if not total:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(dst + ".part", "wb") as f:
                f.write(r.read())
        os.replace(dst + ".part", dst)
        return dst
    parts = [(s, min(s + CHUNK, total) - 1, f"{dst}.part{i:04d}") for i, s in enumerate(range(0, total, CHUNK))]
    log(f"downloading {label} ({total / 1e9:.2f} GB, {connections} connections) ...")
    t0 = time.time()
    with cf.ThreadPoolExecutor(connections) as pool:
        futures = [pool.submit(_fetch, url, s, e, p) for s, e, p in parts]
        for i, fut in enumerate(cf.as_completed(futures)):
            fut.result()
            if (i + 1) % 16 == 0:
                done = sum(os.path.getsize(p) for _, _, p in parts if os.path.isfile(p))
                log(f"  {label}: {done / 1e9:.2f}/{total / 1e9:.2f} GB, {done / 1e6 / (time.time() - t0):.1f} MB/s")
    with open(dst + ".part", "wb") as out:
        for _, _, p in parts:
            with open(p, "rb") as f:
                while True:
                    b = f.read(16 << 20)
                    if not b:
                        break
                    out.write(b)
    if os.path.getsize(dst + ".part") != total:
        raise RuntimeError(f"{label}: size mismatch after download")
    os.replace(dst + ".part", dst)
    for _, _, p in parts:
        os.remove(p)
    log(f"  {label}: done in {time.time() - t0:.0f} s")
    return dst

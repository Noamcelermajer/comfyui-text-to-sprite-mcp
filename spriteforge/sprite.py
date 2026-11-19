"""Image processing that turns a generated picture into a game-ready sprite.

Everything works on float RGBA arrays (H, W, 4) in 0..1 and needs only numpy, Pillow and
scipy, which ComfyUI's Python already has.

  load_rgba    picture + BiRefNet mask -> RGBA (or alpha from brightness, for glowing effects)
  clean_alpha  drop faint haze and stray specks
  defringe     replace the background colour bleeding into soft edges (the grey halo)
  crop / fit   cut to the content and place it on a fixed canvas (centred or feet-on-the-ground)
  pixelize     true low-resolution pixel art (pixel.py: grid recovery, palette lock, cleanup)
  seamless     make a texture tile without a visible seam, and measure the seam
  pack_sheet   frames -> sprite sheet + TexturePacker/Phaser JSON atlas (Godot, Unity, Phaser)
  problems_of  empty, background left in, cut off at the frame edge, several objects
"""
import json
import os

import numpy as np
from PIL import Image
from scipy import ndimage

# ----------------------------------------------------------------------------- loading

def _arr(img):
    return np.asarray(img, dtype=np.float32) / 255.0


def load_rgba(rgb_path, mask_path=None, alpha_mode="cutout"):
    """alpha_mode: cutout (BiRefNet mask), luminance (bright = opaque, for fire/magic on black),
    none (opaque)."""
    rgb = _arr(Image.open(rgb_path).convert("RGB"))
    if alpha_mode == "luminance":
        # The picture is the effect on black: brightness becomes opacity and the colour is
        # un-premultiplied, so drawing it with normal blending over black gives the picture back.
        m = rgb.max(axis=2)
        a = np.clip((m - 0.03) / 0.97, 0, 1)
        col = np.where(a[..., None] > 1e-3, np.clip(rgb / np.maximum(a[..., None], 1e-3), 0, 1), 0)
        return np.dstack([col, a])
    if alpha_mode == "none" or not mask_path:
        return np.dstack([rgb, np.ones(rgb.shape[:2], np.float32)])
    m = Image.open(mask_path).convert("L")
    if m.size != (rgb.shape[1], rgb.shape[0]):
        m = m.resize((rgb.shape[1], rgb.shape[0]), Image.BILINEAR)
    return np.dstack([rgb, _arr(m)])


def save_png(rgba, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray((np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA").save(path, optimize=True)
    return path


def on_background(rgba, colour=(0.5, 0.5, 0.5)):
    """Composite onto a flat colour (for previews, scoring and as an edit reference)."""
    a = rgba[..., 3:4]
    return rgba[..., :3] * a + np.array(colour, np.float32) * (1 - a)


def to_pil_rgb(rgb):
    return Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")


# ----------------------------------------------------------------------------- cleaning

def clean_alpha(rgba, floor=0.06, min_speck=0.002):
    """Zero faint haze below `floor`; remove specks smaller than `min_speck` of the main object."""
    out = rgba.copy()
    a = out[..., 3]
    a[a < floor] = 0
    lab, n = ndimage.label(a > 0.5)
    if n > 1:
        sizes = ndimage.sum(np.ones_like(a), lab, range(1, n + 1))
        keep = sizes >= max(sizes.max() * min_speck, 4)
        bad = ~np.isin(lab, np.nonzero(keep)[0] + 1) & (lab > 0)
        grown = ndimage.binary_dilation(bad, iterations=2)
        solid = ndimage.binary_dilation(np.isin(lab, np.nonzero(keep)[0] + 1), iterations=1)
        a[grown & ~solid] = 0
    out[..., 3] = a
    return out


def defringe(rgba, solid=0.92, radius=6):
    """Soft edge pixels carry the background colour (a grey or white halo on dark scenes).
    Re-colour every pixel that is not fully solid with the colour of the solid pixels next to it."""
    a = rgba[..., 3]
    w = (a >= solid).astype(np.float32)
    if w.sum() < 16:
        return rgba
    rgb = rgba[..., :3]
    num = np.stack([ndimage.gaussian_filter(rgb[..., c] * w, radius) for c in range(3)], axis=-1)
    den = ndimage.gaussian_filter(w, radius)[..., None]
    fill = np.where(den > 1e-4, num / np.maximum(den, 1e-4), rgb)
    edge = ((a < solid) & (a > 0))[..., None]
    out = rgba.copy()
    out[..., :3] = np.where(edge, fill, rgb)
    out[..., :3] = np.where((a <= 0)[..., None], 0, out[..., :3])  # transparent pixels: black, not grey
    return out


def bbox(rgba, thr=0.5):
    ys, xs = np.nonzero(rgba[..., 3] > thr)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def crop(rgba, pad_px=0):
    b = bbox(rgba, 0.05)
    if not b:
        return rgba
    x0, y0, x1, y1 = b
    h, w = rgba.shape[:2]
    return rgba[max(0, y0 - pad_px):min(h, y1 + pad_px), max(0, x0 - pad_px):min(w, x1 + pad_px)]


def _resize(rgba, w, h, nearest=False):
    """Resize with premultiplied alpha so edges do not pick up black."""
    if nearest:
        img = Image.fromarray((np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA").resize((w, h), Image.NEAREST)
        return _arr(img)
    pm = np.dstack([rgba[..., :3] * rgba[..., 3:4], rgba[..., 3]])
    chans = [np.asarray(Image.fromarray(pm[..., c].astype(np.float32), "F").resize((w, h), Image.LANCZOS)) for c in range(4)]
    out = np.clip(np.dstack(chans), 0, 1)
    a = out[..., 3:4]
    out[..., :3] = np.where(a > 1e-4, out[..., :3] / np.maximum(a, 1e-4), 0)
    return np.clip(out, 0, 1)


def fit(rgba, size, padding=0.06, anchor="center", scale=None, nearest=False):
    """Place the cropped sprite on a size x size (or (w, h)) transparent canvas.
    anchor: center, or bottom (feet on the ground line, for characters and frames).
    scale: force this scale factor (so every frame of a sheet is scaled the same)."""
    W, H = (size, size) if isinstance(size, int) else size
    c = crop(rgba)
    ch, cw = c.shape[:2]
    if scale is None:
        scale = min(W * (1 - 2 * padding) / cw, H * (1 - 2 * padding) / ch)
    nw, nh = max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))
    c = _resize(c, nw, nh, nearest)
    canvas = np.zeros((H, W, 4), np.float32)
    x = (W - nw) // 2
    y = (H - nh) // 2 if anchor == "center" else H - int(round(H * padding)) - nh
    x0, y0 = max(0, x), max(0, y)
    sx, sy = x0 - x, y0 - y
    w_, h_ = min(nw - sx, W - x0), min(nh - sy, H - y0)
    canvas[y0:y0 + h_, x0:x0 + w_] = c[sy:sy + h_, sx:sx + w_]
    return canvas, scale


def fit_scale(rgba, size, padding=0.06):
    c = crop(rgba)
    W, H = (size, size) if isinstance(size, int) else size
    return min(W * (1 - 2 * padding) / c.shape[1], H * (1 - 2 * padding) / c.shape[0])


# ----------------------------------------------------------------------------- pixel art

def pixelize(rgba, grid=32, colors=16, palette=None, outline=False, canvas=None, anchor="center"):
    """Real pixel art from a cut-out sprite - see pixel.py (grid recovery, palette lock)."""
    from . import pixel
    out, info, _pal = pixel.pixelize(rgba, grid, colors, palette, outline, canvas, anchor)
    return out, info


def upscale_nearest(rgba, factor):
    h, w = rgba.shape[:2]
    return _resize(rgba, w * factor, h * factor, nearest=True)


# ----------------------------------------------------------------------------- tiles

def seam_score(rgb):
    """How visible the wrap-around seam is: edge-to-edge difference over the typical
    difference between neighbouring pixels. ~1 = seamless, > 2 = clearly visible."""
    rgb = rgb[..., :3]
    inner_x = np.abs(np.diff(rgb, axis=1)).mean() + 1e-6
    inner_y = np.abs(np.diff(rgb, axis=0)).mean() + 1e-6
    sx = np.abs(rgb[:, 0] - rgb[:, -1]).mean() / inner_x
    sy = np.abs(rgb[0, :] - rgb[-1, :]).mean() / inner_y
    return round(float(max(sx, sy)), 2)


def make_seamless(rgb, blend=0.25):
    """Offset-and-blend: the borders come from a half-shifted copy (which wraps cleanly), the
    centre from the original; a smooth weight crossfades them. Works well on organic textures
    (grass, stone, sand, bark); geometric patterns may show a soft double image in the blend band."""
    rgb = rgb[..., :3]
    h, w = rgb.shape[:2]
    shifted = np.roll(np.roll(rgb, h // 2, axis=0), w // 2, axis=1)

    def ramp(n):
        x = np.abs(np.arange(n) - (n - 1) / 2) / ((n - 1) / 2)  # 0 centre .. 1 edge
        t = np.clip((x - (1 - 2 * blend)) / (2 * blend), 0, 1)
        return t * t * (3 - 2 * t)

    edge = np.maximum(ramp(h)[:, None], ramp(w)[None, :])[..., None]
    return rgb * (1 - edge) + shifted * edge


def tile_preview(rgb, n=3):
    return np.tile(rgb[..., :3], (n, n, 1))


# ----------------------------------------------------------------------------- sheets

def pack_sheet(frames, names, image_name, columns=None, tags=None, fps=10):
    """frames: equal-size RGBA arrays. Returns (sheet RGBA, atlas dict).
    The atlas is TexturePacker/Phaser 'hash' JSON, read by Phaser directly, by Godot with the
    Sprite Atlas Importer add-on and by Unity importers; `meta.frameTags` (Aseprite-style)
    names each animation's frame range."""
    fh, fw = frames[0].shape[:2]
    n = len(frames)
    cols = columns or min(n, 8)
    rows = (n + cols - 1) // cols
    sheet = np.zeros((rows * fh, cols * fw, 4), np.float32)
    atlas = {"frames": {}, "meta": {"app": "Sprite Forge", "version": "1.0", "image": image_name, "format": "RGBA8888",
                                     "size": {"w": cols * fw, "h": rows * fh}, "scale": "1", "frameTags": tags or []}}
    for i, (f, nm) in enumerate(zip(frames, names)):
        r, c = divmod(i, cols)
        sheet[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw] = f
        atlas["frames"][nm] = {"frame": {"x": c * fw, "y": r * fh, "w": fw, "h": fh}, "rotated": False, "trimmed": False,
                               "spriteSourceSize": {"x": 0, "y": 0, "w": fw, "h": fh}, "sourceSize": {"w": fw, "h": fh},
                               "duration": int(1000 / fps)}
    return sheet, atlas


def write_atlas(atlas, path):
    json.dump(atlas, open(path, "w", encoding="utf-8"), indent=1)
    return path


# ----------------------------------------------------------------------------- checks

def analyse(rgba):
    a = rgba[..., 3]
    h, w = a.shape
    solid = a > 0.5
    cov = float(solid.mean())
    border = np.concatenate([solid[0], solid[-1], solid[:, 0], solid[:, -1]])
    lab, n = ndimage.label(solid)
    big = 0
    if n:
        sizes = np.sort(ndimage.sum(np.ones_like(a), lab, range(1, n + 1)))[::-1]
        big = int((sizes >= sizes[0] * 0.12).sum())
    b = bbox(rgba)
    return {"width": w, "height": h, "coverage": round(cov, 3), "touches_edge": round(float(border.mean()), 3),
            "objects": big, "bbox": b, "soft_edge_share": round(float(((a > 0.05) & (a < 0.95)).mean() / max(cov, 1e-6)), 3)}


def problems_of(raw_stats, kind, final_stats=None):
    """raw_stats: analyse() of the full generated frame (before cropping)."""
    p = []
    if kind in ("tile", "portrait"):
        return p
    if raw_stats["coverage"] < 0.01:
        p.append("empty: almost nothing was kept after removing the background")
    elif raw_stats["coverage"] > 0.93:
        p.append("background not removed: the cut-out covers the whole picture")
    if kind not in ("vfx",) and raw_stats["touches_edge"] > 0.02:
        p.append("cut off: the subject touches the edge of the picture, part of it is probably missing")
    if kind in ("sprite", "icon", "character", "frame") and raw_stats["objects"] > 1:
        p.append(f"several objects: {raw_stats['objects']} separate shapes instead of one")
    return p

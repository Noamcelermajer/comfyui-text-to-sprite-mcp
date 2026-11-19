"""Pixel-art mode: turn a model's "fake" pixel art into real, grid-aligned pixel art.

Image models draw pixel-art *style* at 512-1024 px: the "pixels" are blocks of uneven
size (6 px here, 7 px there), edges are anti-aliased and there are hundreds of colours.
Downscaling that naively gives mixels and mush. This module recovers the grid instead,
following the approach of the re-gridders that handle AI output best (Pixel Snapper and
proper-pixel-art, both MIT; this is an independent numpy implementation):

  1. quantize first (k-means in OKLab), so anti-aliasing noise does not hide the grid
  2. edge profiles: count colour/alpha changes per column and per row
  3. cell size from the gaps between profile peaks (median), checked against the
     autocorrelation period; the candidate with the cleanest cells wins
     - or forced from the requested `grid` (the sprite's longest side in pixels)
  4. cell boundaries walk the image one cell at a time and snap to the strongest edge
     within +-35% of a cell, so a grid that drifts is followed
  5. each cell takes the most common palette colour of its interior (no blending), and
     is opaque if most of it is
  6. orphan pixels are removed, alpha is fully on/off, optional 1-px outline
  7. the palette is either chosen (k colours) or locked to a named / given palette,
     matched in OKLab; a sheet uses one cell size and one palette for every frame

No dithering: error diffusion scatters single pixels that flicker between frames.
"""
import numpy as np
from scipy import ndimage

PALETTES = {
    "pico8": ["000000", "1D2B53", "7E2553", "008751", "AB5236", "5F574F", "C2C3C7", "FFF1E8",
              "FF004D", "FFA300", "FFEC27", "00E436", "29ADFF", "83769C", "FF77A8", "FFCCAA"],
    "gameboy": ["0F380F", "306230", "8BAC0F", "9BBC0F"],
    "nes": ["000000", "FCFCFC", "BCBCBC", "7C7C7C", "A4E4FC", "3CBCFC", "0078F8", "0000FC", "B8B8F8", "6888FC",
            "0058F8", "0000BC", "D8B8F8", "9878F8", "6844FC", "4428BC", "F8B8F8", "F878F8", "D800CC", "940084",
            "F8A4C0", "F85898", "E40058", "A80020", "F0D0B0", "F87858", "F83800", "A81000", "FCE0A8", "FCA044",
            "E45C10", "881400", "F8D878", "F8B800", "AC7C00", "503000", "D8F878", "B8F818", "00B800", "007800",
            "B8F8B8", "58D854", "00A800", "006800", "B8F8D8", "58F898", "00A844", "005800", "00FCFC", "00E8D8",
            "008888", "004058", "787878"],
    "endesga32": ["BE4A2F", "D77643", "EAD4AA", "E4A672", "B86F50", "733E39", "3E2731", "A22633",
                  "E43B44", "F77622", "FEAE34", "FEE761", "63C74D", "3E8948", "265C42", "193C3E",
                  "124E89", "0099DB", "2CE8F5", "FFFFFF", "C0CBDC", "8B9BB4", "5A6988", "3A4466",
                  "262B44", "181425", "FF0044", "68386C", "B55088", "F6757A", "E8B796", "C28569"],
    "sweetie16": ["1A1C2C", "5D275D", "B13E53", "EF7D57", "FFCD75", "A7F070", "38B764", "257179",
                  "29366F", "3B5DC9", "41A6F6", "73EFF7", "F4F4F4", "94B0C2", "566C86", "333C57"],
}


# ----------------------------------------------------------------------------- colour

def _lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def oklab(rgb):
    """sRGB 0..1 (..., 3) -> OKLab (Ottosson 2020): distances match perceived difference."""
    r, g, b = [_lin(rgb[..., i]) for i in range(3)]
    l = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return np.stack([0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                     1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                     0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s], axis=-1)


def palette_colours(palette):
    """A named palette or a list of hex strings -> (n, 3) sRGB 0..1. None -> None."""
    if not palette:
        return None
    if isinstance(palette, str):
        if palette not in PALETTES:
            raise ValueError(f"unknown palette '{palette}'. Named palettes: {', '.join(PALETTES)} - or give a list of hex colours")
        palette = PALETTES[palette]
    return np.array([[int(h.lstrip('#')[i:i + 2], 16) / 255.0 for i in (0, 2, 4)] for h in palette], np.float32)


def kmeans_palette(rgb_pixels, k, seed=42, iters=15, sample=20000):
    """k colours for these pixels: k-means++ in OKLab, returned as sRGB cluster medians."""
    px = rgb_pixels.reshape(-1, 3)
    if len(px) == 0:
        return np.zeros((1, 3), np.float32)
    rng = np.random.default_rng(seed)
    if len(px) > sample:
        px = px[rng.choice(len(px), sample, replace=False)]
    uniq = np.unique((px * 255).round().astype(np.int32), axis=0)
    if len(uniq) <= k:
        return (uniq / 255.0).astype(np.float32)
    lab = oklab(px)
    centres = [lab[rng.integers(len(lab))]]
    for _ in range(1, k):  # k-means++ seeding
        d = np.min(((lab[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        centres.append(lab[rng.choice(len(lab), p=d / d.sum())] if d.sum() > 0 else lab[rng.integers(len(lab))])
    c = np.array(centres)
    for _ in range(iters):
        lab_idx = ((lab[:, None, :] - c[None]) ** 2).sum(-1).argmin(1)
        new = np.array([lab[lab_idx == j].mean(0) if (lab_idx == j).any() else c[j] for j in range(k)])
        if np.allclose(new, c, atol=1e-5):
            break
        c = new
    lab_idx = ((lab[:, None, :] - c[None]) ** 2).sum(-1).argmin(1)
    # report each cluster by the median of its real pixels, so colours stay ones the model drew
    return np.array([np.median(px[lab_idx == j], axis=0) if (lab_idx == j).any() else px[0] for j in range(k)], np.float32)


def nearest(rgb, pal):
    """Index of the nearest palette colour (OKLab) for every pixel."""
    a, b = oklab(rgb.reshape(-1, 3)), oklab(pal)
    idx = np.empty(len(a), np.int32)
    for s in range(0, len(a), 65536):
        idx[s:s + 65536] = ((a[s:s + 65536, None, :] - b[None]) ** 2).sum(-1).argmin(1)
    return idx.reshape(rgb.shape[:-1])


# ----------------------------------------------------------------------------- grid

def _profiles(labels, solid):
    """Changes of colour or opacity between neighbouring pixels, summed per column / row."""
    lx = (labels[:, 1:] != labels[:, :-1]) | (solid[:, 1:] != solid[:, :-1])
    ly = (labels[1:, :] != labels[:-1, :]) | (solid[1:, :] != solid[:-1, :])
    col = np.concatenate([[0], lx.sum(0)]).astype(np.float32)  # col[x]: an edge between x-1 and x
    row = np.concatenate([[0], ly.sum(1)]).astype(np.float32)
    return col, row


def _peak_period(profile):
    if profile.max() <= 0:
        return None
    p = ndimage.uniform_filter1d(profile, 1)
    thr = 0.2 * p.max()
    peaks = [i for i in range(1, len(p) - 1) if p[i] >= thr and p[i] >= p[i - 1] and p[i] >= p[i + 1]]
    gaps = np.diff(peaks)
    gaps = gaps[gaps >= 2]
    return float(np.median(gaps)) if len(gaps) >= 3 else None


def _autocorr_period(profile, lo=2, hi=64):
    x = profile - profile.mean()
    if not x.any():
        return None
    ac = np.correlate(x, x, "full")[len(x) - 1:]
    hi = min(hi, len(x) // 3)
    if hi <= lo:
        return None
    lag = lo + int(np.argmax(ac[lo:hi]))
    return float(lag)


def _boundaries(profile, cell, length):
    """Cell edges: best global offset, then walk one cell at a time snapping to strong edges."""
    cell = max(1.0, float(cell))
    best_o, best_e = 0, -1.0
    for o in range(int(np.ceil(cell))):
        pos = np.arange(o, length, cell).round().astype(int)
        e = profile[pos[pos < length]].sum()
        if e > best_e:
            best_o, best_e = o, e
    r = max(1, int(round(0.35 * cell)))
    strong = 0.2 * profile.max() if profile.max() > 0 else 1
    b = [best_o]
    while b[-1] + cell < length:
        t = int(round(b[-1] + cell))
        lo, hi = max(b[-1] + 1, t - r), min(length - 1, t + r)
        if lo > hi:
            break
        w = profile[lo:hi + 1]
        nxt = lo + int(np.argmax(w)) if w.max() >= strong else t
        b.append(min(nxt, length))
    if b[0] > 0.5 * cell:
        b.insert(0, 0)
    elif b[0] > 0:
        b[0] = 0
    if length - b[-1] > 0.5 * cell:
        b.append(length)
    else:
        b[-1] = length
    return np.array(sorted(set(b)), int)


def _cells(labels, solid, bx, by, pal_rgb):
    """Most common palette index of each cell's interior; opaque if most of the cell is."""
    out_idx = np.zeros((len(by) - 1, len(bx) - 1), np.int32)
    out_a = np.zeros_like(out_idx, dtype=bool)
    k = len(pal_rgb)
    for j in range(len(by) - 1):
        y0, y1 = by[j], by[j + 1]
        iy = max(0, int((y1 - y0) * 0.2))
        for i in range(len(bx) - 1):
            x0, x1 = bx[i], bx[i + 1]
            ix = max(0, int((x1 - x0) * 0.2))
            s = solid[y0 + iy:y1 - iy or None, x0 + ix:x1 - ix or None]
            if s.size == 0:
                s = solid[y0:y1, x0:x1]
                lab = labels[y0:y1, x0:x1]
            else:
                lab = labels[y0 + iy:y1 - iy or None, x0 + ix:x1 - ix or None]
            if s.mean() >= 0.5:
                out_a[j, i] = True
                vals = lab[s]
                out_idx[j, i] = np.bincount(vals, minlength=k).argmax() if vals.size else 0
    return out_idx, out_a


def _variance(lab_img, solid, bx, by):
    """Mean colour spread inside cells (OKLab): low = the picture really was on this grid."""
    tot, n = 0.0, 0
    for j in range(len(by) - 1):
        for i in range(len(bx) - 1):
            s = solid[by[j]:by[j + 1], bx[i]:bx[i + 1]]
            if s.mean() >= 0.5:
                tot += float(lab_img[by[j]:by[j + 1], bx[i]:bx[i + 1]][s].std(0).mean())
                n += 1
    return tot / max(n, 1)


def detect_cell(rgba, colors=16):
    """Estimate the size of the model's pseudo-pixels (in source pixels) - or None."""
    solid = rgba[..., 3] >= 0.5
    pal = kmeans_palette(rgba[..., :3][solid], colors)
    labels = nearest(rgba[..., :3], pal)
    col, row = _profiles(labels, solid)
    cands = {c for c in (_peak_period(col), _peak_period(row), _autocorr_period(col), _autocorr_period(row)) if c and c >= 2}
    if not cands:
        return None, {}
    lab = oklab(rgba[..., :3])
    scored = []
    for c in sorted(cands):
        bx, by = _boundaries(col, c, rgba.shape[1]), _boundaries(row, c, rgba.shape[0])
        scored.append((_variance(lab, solid, bx, by) * (1 + 0.15 * (c < 3)), c))
    v, c = min(scored)
    return c, {"candidates": sorted(cands), "cell_variance": round(v, 4)}


# ----------------------------------------------------------------------------- conversion

def regrid(rgba, grid=None, colors=16, palette=None, cell=None, pal_rgb=None):
    """rgba: the cut-out sprite (cropped). grid: the longest side in output pixels (forces the
    cell size); None = detect the model's own pixel size. pal_rgb: an already chosen palette
    (sheets share one). Returns (small RGBA, palette used, info)."""
    h, w = rgba.shape[:2]
    solid = rgba[..., 3] >= 0.5
    fixed = palette_colours(palette)
    if pal_rgb is None:
        pal_rgb = fixed if fixed is not None else kmeans_palette(rgba[..., :3][solid], int(colors))
    labels = nearest(rgba[..., :3], pal_rgb)
    col, row = _profiles(labels, solid)
    info = {}
    if cell is None:
        if grid:
            cell = max(h, w) / float(grid)
            info["cell_from"] = "requested grid"
        else:
            cell, det = detect_cell(rgba, int(colors))
            info.update(det)
            info["cell_from"] = "detected"
            if not cell:
                cell = max(h, w) / 32.0
                info["cell_from"] = "no grid found - fell back to 32 pixels"
    bx, by = _boundaries(col, cell, w), _boundaries(row, cell, h)
    if grid:  # exactly `grid` cells along the longest side
        long_b = bx if w >= h else by
        if len(long_b) - 1 != int(grid):
            n = int(grid)
            length = w if w >= h else h
            even = np.linspace(0, length, n + 1).round().astype(int)
            if w >= h:
                bx = even
                by = np.linspace(0, h, max(1, int(round(h / (w / n)))) + 1).round().astype(int)
            else:
                by = even
                bx = np.linspace(0, w, max(1, int(round(w / (h / n)))) + 1).round().astype(int)
    idx, alpha = _cells(labels, solid, bx, by, pal_rgb)
    small = np.dstack([pal_rgb[idx], alpha.astype(np.float32)])
    small[~alpha, :3] = 0
    edge_energy = (col[bx[1:-1]].mean() if len(bx) > 2 else 0) / (col.mean() + 1e-6)
    info.update(cell=round(float(cell), 2), size=[int(small.shape[1]), int(small.shape[0])],
                grid_confidence=round(float(edge_energy), 2),
                cell_variance=round(_variance(oklab(rgba[..., :3]), solid, bx, by), 4))
    return small, pal_rgb, info


def clean(small, min_neighbours=1):
    """Remove orphans (opaque pixels with no opaque 4-neighbour) and lone noise pixels
    (a pixel whose 8 neighbours all share one other colour). Returns (rgba, removed count)."""
    out = small.copy()
    a = out[..., 3] > 0
    n4 = ndimage.convolve(a.astype(int), np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]]), mode="constant")
    orphans = a & (n4 < min_neighbours)
    out[orphans] = 0
    a = out[..., 3] > 0
    key = (out[..., 0] * 255).round().astype(int) * 65536 + (out[..., 1] * 255).round().astype(int) * 256 + (out[..., 2] * 255).round().astype(int)
    key = np.where(a, key, -1)
    fixed = 0
    H, W = key.shape
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if key[y, x] < 0:
                continue
            nb = np.delete(key[y - 1:y + 2, x - 1:x + 2].ravel(), 4)
            if nb[0] >= 0 and (nb == nb[0]).all() and nb[0] != key[y, x]:
                out[y, x, :3] = out[y - 1, x - 1, :3]
                fixed += 1
    return out, int(orphans.sum()) + fixed


def outline(small, pal_rgb):
    """1-pixel outline around the sprite in the palette's darkest colour."""
    out = np.pad(small, ((1, 1), (1, 1), (0, 0)))
    a = out[..., 3] > 0
    ring = ndimage.binary_dilation(a, structure=np.ones((3, 3), bool)) & ~a
    dark = pal_rgb[np.argmin(oklab(pal_rgb)[:, 0])]
    out[ring, :3] = dark
    out[ring, 3] = 1
    return out


def on_canvas(small, canvas, anchor="center"):
    """Place on a canvas x canvas grid (e.g. 32x32). Crops if the sprite is larger."""
    cv = np.zeros((canvas, canvas, 4), np.float32)
    oh, ow = min(small.shape[0], canvas), min(small.shape[1], canvas)
    x = (canvas - ow) // 2
    y = (canvas - oh) // 2 if anchor == "center" else canvas - oh
    cv[y:y + oh, x:x + ow] = small[:oh, :ow]
    return cv


def stats(small):
    """Pixel-art quality numbers: colours, orphans left, partial alpha (should be 0)."""
    a = small[..., 3]
    solid = a > 0
    cols = np.unique((small[solid][:, :3] * 255).round().astype(int), axis=0) if solid.any() else []
    n4 = ndimage.convolve(solid.astype(int), np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]]), mode="constant")
    return {"colours_used": int(len(cols)), "orphans": int((solid & (n4 == 0)).sum()),
            "partial_alpha": int(((a > 0) & (a < 1)).sum()), "opaque_pixels": int(solid.sum())}


def pixelize(rgba, grid=32, colors=16, palette=None, outline_px=False, canvas=None, anchor="center", pal_rgb=None, cell=None):
    """Full pixel-art conversion of one cut-out sprite. grid: int, or 'auto'/None to keep the
    model's own pixel size. Returns (rgba, info, palette)."""
    from .sprite import clean_alpha, crop
    c = crop(clean_alpha(rgba))
    g = None if grid in (None, "auto", 0) else int(grid)
    small, pal, info = regrid(c, g, colors, palette, cell, pal_rgb)
    small, removed = clean(small)
    if outline_px:
        small = outline(small, pal)
    if canvas:
        small = on_canvas(small, int(canvas), anchor)
    info.update(stats(small), cleaned_pixels=removed, palette=(palette if isinstance(palette, str) else
                                                               "custom" if palette else f"{len(pal)} colours chosen"))
    return small, info, pal


def pixelize_frames(frames, grid=32, colors=16, palette=None, outline_px=False, anchor="bottom"):
    """A sheet's frames with ONE palette and ONE pixel size, so the character does not
    change colour or resolution between frames. The tallest frame sets the scale."""
    from .sprite import clean_alpha, crop
    crops = [crop(clean_alpha(f)) for f in frames]
    fixed = palette_colours(palette)
    if fixed is None:
        allpx = np.concatenate([c[..., :3][c[..., 3] >= 0.5] for c in crops if (c[..., 3] >= 0.5).any()])
        fixed = kmeans_palette(allpx, int(colors))
    ref = max(crops, key=lambda c: c.shape[0])
    cell = (ref.shape[0] / float(grid)) if grid not in (None, "auto", 0) else (detect_cell(ref, int(colors))[0] or ref.shape[0] / 32.0)
    out, infos = [], []
    for c in crops:
        small, _, info = regrid(c, None, colors, None, cell, fixed)
        small, removed = clean(small)
        if outline_px:
            small = outline(small, fixed)
        out.append(small)
        infos.append({**info, **stats(small), "cleaned_pixels": removed})
    W = max(s.shape[1] for s in out) + 2
    H = max(s.shape[0] for s in out) + 2
    side = max(W, H)
    return [on_canvas(s, side, anchor) for s in out], infos, fixed


def pixel_problems(info, kind="pixel"):
    p = []
    if info.get("cell_from", "").startswith("no grid"):
        p.append("no clear pixel grid found: the picture was not drawn as pixel art, so this is a plain downscale")
    if info.get("partial_alpha"):
        p.append(f"{info['partial_alpha']} semi-transparent pixels (pixel art should be fully on or off)")
    if info.get("opaque_pixels", 1) and info.get("orphans", 0) > 0.02 * info["opaque_pixels"]:
        p.append(f"{info['orphans']} stray single pixels")
    if info.get("opaque_pixels", 0) < 12:
        p.append("almost empty after conversion")
    return p

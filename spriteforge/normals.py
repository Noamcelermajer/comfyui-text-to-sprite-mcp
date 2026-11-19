"""Normal maps for 2D lighting (Godot CanvasTexture / Light2D, Unity 2D lights, Phaser light2d).

A sprite has no depth, so - like the dedicated 2D tools (Laigter, SpriteIlluminator) - the
surface is inferred from the picture itself and needs no model:

  bevel    a rounded rim from the outline inwards: the silhouette reads as a solid object
  regions  a small bevel per colour region: plates, belts, cloth folds catch light separately
  emboss   brighter pixels sit slightly higher: painted highlights become real relief

The three heights are summed, and the normal is the slope of that surface. For pixel art the
map stays on the sprite's own pixel grid with hard edges, and the normals are snapped to a
few directions so the lighting stays crisp instead of smooth.

Encoding: RGB = normal * 0.5 + 0.5, alpha = the sprite's alpha. `y_up=True` is the OpenGL
convention (green = up) that Godot and Unity use; `y_up=False` is DirectX (green = down).
"""
import numpy as np
from scipy import ndimage


def _luma(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def height_map(rgba, bevel=None, region_bevel=None, emboss=0.35, pixel=False):
    a = rgba[..., 3] > 0.5
    h, w = a.shape
    if not a.any():
        return np.zeros((h, w), np.float32)
    span = max(1.0, min(h, w))
    d = ndimage.distance_transform_edt(np.pad(a, 1))[1:-1, 1:-1]
    # inflate the whole silhouette into a rounded form (a pillow), not just a thin rim:
    # most of the surface then faces some direction and a moving light visibly sweeps across it
    b = bevel or max(1.5, 0.85 * float(d.max()))
    t = np.clip(d / b, 0, 1)
    dome = np.sqrt(1 - (1 - t) ** 2) * (0.5 * b if pixel else 0.35 * b)
    # colour regions: each patch of (nearly) one colour gets its own small rim
    key = (rgba[..., :3] * (31 if not pixel else 255)).round().astype(np.int32)
    key = key[..., 0] * 1_000_000 + key[..., 1] * 1000 + key[..., 2]
    edges = np.zeros_like(a)
    edges[:, 1:] |= key[:, 1:] != key[:, :-1]
    edges[1:, :] |= key[1:, :] != key[:-1, :]
    rb = region_bevel or (1.5 if pixel else max(2.0, 0.02 * span))
    dr = ndimage.distance_transform_edt(~edges)
    regions = np.clip(dr / rb, 0, 1)
    lum = _luma(rgba[..., :3])
    lum = (lum - lum[a].mean()) if a.any() else lum
    height = dome + (0.9 if pixel else 1.5) * regions + emboss * lum * (2.0 if pixel else 4.0)
    if not pixel:
        height = ndimage.gaussian_filter(height, 0.8)
    return np.where(a, height, 0).astype(np.float32)


def _snap(n, levels=8):
    """Pixel art: snap the tilt direction to `levels` compass directions and the tilt to
    flat / half / steep, so neighbouring pixels share exactly one normal."""
    xy = n[..., :2]
    r = np.linalg.norm(xy, axis=-1)
    ang = np.arctan2(xy[..., 1], xy[..., 0])
    step = 2 * np.pi / levels
    ang = (np.round(ang / step) % levels) * step  # wrap: +180 and -180 degrees are one direction
    tilt = np.where(r < 0.25, 0.0, np.where(r < 0.6, 0.45, 0.8))
    out = np.stack([np.cos(ang) * tilt, np.sin(ang) * tilt, np.sqrt(np.clip(1 - tilt ** 2, 0, 1))], -1)
    return out


def normal_map(rgba, strength=1.0, pixel=False, y_up=True, levels=8, **kw):
    """-> RGBA normal map (float 0..1) the size of the sprite."""
    hgt = height_map(rgba, pixel=pixel, **kw)
    gy, gx = np.gradient(hgt)
    s = float(strength) * (0.9 if pixel else 1.0)
    nx, ny = -gx * s, (gy if y_up else -gy) * s  # image y runs down; OpenGL green runs up
    n = np.stack([nx, ny, np.ones_like(nx)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    if pixel:
        n = _snap(n, levels)
    a = rgba[..., 3:4]
    rgb = np.where(a > 0.5, n * 0.5 + 0.5, np.array([0.5, 0.5, 1.0], np.float32))
    return np.dstack([rgb, rgba[..., 3]]).astype(np.float32)


def lit_preview(rgba, nmap, light=(-0.7, 0.6, 0.35), ambient=0.12, y_up=True):
    """The sprite lit by one light (default: upper left, in front) - shows where light hits."""
    n = nmap[..., :3] * 2 - 1
    if not y_up:
        n = n * np.array([1, -1, 1], np.float32)
    L = np.array(light, np.float32)
    L /= np.linalg.norm(L)
    shade = ambient + (1 - ambient) * np.clip((n * L).sum(-1), 0, 1) * 1.4
    out = rgba.copy()
    out[..., :3] = np.clip(rgba[..., :3] * shade[..., None], 0, 1)
    return out


def light_strip(rgba, nmap, y_up=True):
    """Four lit versions side by side: light from the left, top, right and bottom."""
    lights = [(-1, 0.1, 0.35), (0, 1, 0.35), (1, 0.1, 0.35), (0, -1, 0.35)]
    frames = [lit_preview(rgba, nmap, l, y_up=y_up) for l in lights]
    return np.concatenate(frames, axis=1)

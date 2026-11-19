"""Procedural pixel-art motions: the classic ones animators do by hand with transforms.

Squash and stretch, bobbing, breathing, shaking and flashing are deformations of the same
drawing, which image models do badly (they redraw the sprite instead). Done here directly on
the sprite's pixels with nearest-neighbour scaling, so no new colours appear, the outline
stays one pixel wide and the result is exact. Every frame sits on one shared canvas with
the feet on the same ground line, so the motion is kept when the frames are played.
"""
import re

import numpy as np

MOTIONS = {
    "bounce": r"\b(bounc|squash|stretch|hop)",
    "bob": r"\b(bob|float|hover|levitat|drift)",
    "breathe": r"\b(breath|idle breath|inhale)",
    "shake": r"\b(shak|tremb|shiver|vibrat|jitter)",
    "pulse": r"\b(puls|throb|heartbeat|grow and shrink)",
    "flash": r"\b(flash|blink white|hit flash|damage flash)",
}


def detect(description):
    d = (description or "").lower()
    for name, pat in MOTIONS.items():
        if re.search(pat, d):
            return name
    return None


def _scale(sprite, sx, sy):
    h, w = sprite.shape[:2]
    nh, nw = max(1, int(round(h * sy))), max(1, int(round(w * sx)))
    yi = np.minimum((np.arange(nh) / sy).astype(int), h - 1)
    xi = np.minimum((np.arange(nw) / sx).astype(int), w - 1)
    return sprite[yi][:, xi]


def _place(canvas_hw, part, dx=0, lift=0):
    """Bottom-centred on the canvas, moved right by dx and up by lift pixels."""
    H, W = canvas_hw
    out = np.zeros((H, W, 4), np.float32)
    h, w = part.shape[:2]
    x = (W - w) // 2 + int(dx)
    y = H - h - int(lift)
    ys, xs = max(0, y), max(0, x)
    ye, xe = min(H, y + h), min(W, x + w)
    if ye > ys and xe > xs:
        out[ys:ye, xs:xe] = part[ys - y:ye - y, xs - x:xe - x]
    return out


def frames_for(sprite, motion, n=None, amount=1.0):
    """sprite: native-size RGBA pixel art (hard alpha). -> list of equal-size frames."""
    h, w = sprite.shape[:2]
    a = float(amount)
    if motion == "bounce":
        jump = max(2, int(round(h * 0.35 * a)))
        keys = [(1.0, 1.0, 0), (1.0 + 0.18 * a, 1.0 - 0.18 * a, 0), (1.0 - 0.1 * a, 1.0 + 0.12 * a, jump * 0.5),
                (1.0, 1.0, jump), (1.0 - 0.08 * a, 1.0 + 0.1 * a, jump * 0.5), (1.0 + 0.12 * a, 1.0 - 0.12 * a, 0)]
    elif motion == "bob":
        amp = max(1, int(round(h * 0.06 * a)))
        keys = [(1, 1, round(amp * (1 + np.sin(2 * np.pi * i / 4 - np.pi / 2)) / 2 * 2)) for i in range(4)]
    elif motion == "breathe":
        keys = [(1, 1, 0), (1.0 + 0.02 * a, 1.0 + 0.05 * a, 0), (1.0 + 0.03 * a, 1.0 + 0.07 * a, 0), (1.0 + 0.01 * a, 1.0 + 0.03 * a, 0)]
    elif motion == "pulse":
        keys = [(1, 1, 0), (1.0 + 0.08 * a, 1.0 + 0.08 * a, 0), (1.0 + 0.14 * a, 1.0 + 0.14 * a, 0), (1.0 + 0.06 * a, 1.0 + 0.06 * a, 0)]
    elif motion == "shake":
        d = max(1, int(round(w * 0.06 * a)))
        offsets = [0, -d, d, -d, d, 0]
        keys = [(1, 1, 0, o) for o in offsets]
    elif motion == "flash":
        keys = [(1, 1, 0, 0, 0), (1, 1, 0, 0, 1), (1, 1, 0, 0, 0), (1, 1, 0, 0, 1)]
    else:
        raise ValueError(f"unknown motion '{motion}'")
    if n and n != len(keys):
        keys = [keys[i] for i in np.linspace(0, len(keys) - 1, int(n)).round().astype(int)]
    parts = []
    for k in keys:
        sx, sy, lift = k[0], k[1], k[2]
        dx = k[3] if len(k) > 3 else 0
        part = _scale(sprite, sx, sy)
        if len(k) > 4 and k[4]:
            part = part.copy()
            solid = part[..., 3] > 0
            part[solid, :3] = 1.0  # white hit flash
        parts.append((part, dx, lift))
    H = max(p.shape[0] + int(l) for p, _, l in parts) + 1
    W = max(p.shape[1] + 2 * abs(int(d)) for p, d, _ in parts) + 2
    return [_place((H, W), p, d, l) for p, d, l in parts]


def has_outline(sprite):
    """True when the sprite is ringed by a 1-pixel dark outline (most of its edge pixels are
    the darkest colour)."""
    from scipy import ndimage
    a = sprite[..., 3] > 0
    if a.sum() < 12:
        return False
    edge = a & ~ndimage.binary_erosion(a)
    lum = sprite[..., :3].mean(-1)
    dark = lum[a].min() + 0.08
    return float((lum[edge] <= dark).mean()) > 0.7


def outline_colour(sprite):
    a = sprite[..., 3] > 0
    lum = sprite[..., :3].mean(-1)
    i = np.unravel_index(np.argmin(np.where(a, lum, 9)), lum.shape)
    return sprite[i][:3].copy()


def reapply_outline(frame, colour):
    """Ring the frame with a 1-pixel outline of `colour` (on the outside, where it is missing)."""
    from scipy import ndimage
    out = frame.copy()
    a = out[..., 3] > 0
    lum = out[..., :3].mean(-1)
    inner = a & (lum > colour.mean() + 0.08)  # the coloured body, without any outline already there
    ring = ndimage.binary_dilation(inner, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)) & ~inner
    out[ring, :3] = colour
    out[ring, 3] = 1.0
    return out

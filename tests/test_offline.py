"""Offline tests of the image processing (no GPU, no ComfyUI): python tests/test_offline.py"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from spriteforge import pipeline, pixel, profiles, sprite  # noqa: E402

TMP = tempfile.mkdtemp(prefix="spriteforge_test_")


def fake_generation(name, shape="potion", size=1024, touch_edge=False, two=False):
    """A picture on light grey with an antialiased object, and its soft mask - like ComfyUI saves."""
    rgb = Image.new("RGB", (size, size), (209, 209, 209))
    mask = Image.new("L", (size, size), 0)
    big = 4
    hi = Image.new("L", (size * big, size * big), 0)
    d, dm = ImageDraw.Draw(rgb), ImageDraw.Draw(hi)
    box = (300, 220, 724, 860) if not touch_edge else (600, 200, 1100, 800)
    d.ellipse(box, fill=(180, 40, 60), outline=(60, 10, 20), width=12)
    d.rectangle((460, 140, 564, 260), fill=(140, 90, 50))
    dm.ellipse(tuple(v * big for v in box), fill=255)
    dm.rectangle((460 * big, 140 * big, 564 * big, 260 * big), fill=255)
    if two:
        d.ellipse((40, 40, 200, 200), fill=(30, 160, 40))
        dm.ellipse((160, 160, 800, 800), fill=255)
    mask = hi.resize((size, size), Image.LANCZOS)
    rp, mp = os.path.join(TMP, name + "_rgb.png"), os.path.join(TMP, name + "_mask.png")
    rgb.save(rp)
    mask.save(mp)
    return rp, mp


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        check.failed += 1


check.failed = 0


def main():
    params = profiles.resolve("sprite")["params"]
    pair = fake_generation("potion")
    rgba = pipeline._cutout(pair, params)
    stats = sprite.analyse(rgba)
    check(sprite.problems_of(stats, "sprite") == [], f"clean sprite has no problems {stats}")
    edge = rgba[(rgba[..., 3] > 0.1) & (rgba[..., 3] < 0.9)][:, :3]
    check(len(edge) and abs(edge.mean() - 0.82) > 0.1, f"defringe removed the grey halo (edge mean {edge.mean():.2f})")
    out, extra, _ = pipeline._process_single(rgba, profiles.resolve("sprite"), "sprite")
    check(out.shape[:2] == (512, 512), f"fit to 512 canvas {out.shape}")
    b = sprite.bbox(out)
    check(b[0] > 20 and b[2] < 492, f"padding kept {b}")

    cut = sprite.analyse(pipeline._cutout(fake_generation("cut", touch_edge=True), params))
    check(any("cut off" in p for p in sprite.problems_of(cut, "sprite")), "subject touching the edge is flagged")
    two = sprite.analyse(pipeline._cutout(fake_generation("two", two=True), params))
    print("     two-object stats:", two)

    # pixel art
    px, info = sprite.pixelize(rgba, grid=32, colors=8)
    check(max(px.shape[:2]) == 32, f"pixelize grid 32 -> {px.shape[:2]}")
    check(set(np.unique(px[..., 3])) <= {0.0, 1.0}, "pixel art alpha is hard")
    check(info["colours_used"] <= 8, f"palette limited: {info['colours_used']} colours")
    px2, info2 = sprite.pixelize(rgba, grid=32, palette="pico8", outline=True, canvas=40)
    cols = np.unique((px2[px2[..., 3] > 0][:, :3] * 255).round().astype(int), axis=0)
    pico = np.array([[int(h[i:i + 2], 16) for i in (0, 2, 4)] for h in pixel.PALETTES["pico8"]])
    check(all(any((c == pc).all() for pc in pico) or c.sum() < 60 for c in cols), "pico8 palette respected")
    check(px2.shape[:2] == (40, 40), "pixel canvas 40x40")

    # tile
    noise = np.random.default_rng(1).random((256, 256, 3)).astype(np.float32)
    grad = np.dstack([np.linspace(0, 1, 256)[None, :].repeat(256, 0)] * 3).astype(np.float32) * 0.6 + noise * 0.4
    check(sprite.seam_score(grad) > 2, f"gradient has a seam ({sprite.seam_score(grad)})")
    sl = sprite.make_seamless(grad)
    check(sprite.seam_score(sl) < 1.6, f"make_seamless removes it ({sprite.seam_score(sl)})")

    # luminance alpha
    vfx = np.zeros((128, 128, 3), np.float32)
    vfx[40:90, 40:90] = [1.0, 0.5, 0.1]
    Image.fromarray((vfx * 255).astype(np.uint8)).save(os.path.join(TMP, "vfx.png"))
    v = sprite.load_rgba(os.path.join(TMP, "vfx.png"), None, "luminance")
    check(v[0, 0, 3] == 0 and v[60, 60, 3] > 0.95, "luminance alpha: black transparent, bright opaque")

    # sheet
    frames = pipeline._normalise([rgba, rgba[:, ::-1], rgba[100:, :]], 128, 0.05, True)
    sheet, atlas = sprite.pack_sheet(frames, ["a", "b", "c"], "s.png", tags=[{"name": "t", "from": 0, "to": 2}])
    check(sheet.shape[:2] == (128, 384) and atlas["frames"]["c"]["frame"]["x"] == 256, "sheet + atlas layout")
    bottoms = [sprite.bbox(f)[3] for f in frames]
    check(max(bottoms) - min(bottoms) <= 2, f"frames share a ground line {bottoms}")
    pixel_tests()
    normal_and_anim_tests()
    print(f"\n{check.failed} failed; files in {TMP}")
    sys.exit(1 if check.failed else 0)



def fake_pixel_art(seed=3):
    """A real 24x32 pixel sprite, drawn the way models draw 'pixel art': uneven 6/7 px blocks,
    a soft edge, noise and an offset. Returns (true sprite, fake hi-res RGBA)."""
    rng = np.random.default_rng(seed)
    pal = np.array([[0.1, 0.1, 0.15], [0.8, 0.2, 0.2], [0.95, 0.8, 0.6], [0.2, 0.4, 0.8], [0.9, 0.9, 0.9], [0.3, 0.6, 0.2]])
    h, w = 32, 24
    idx = rng.integers(1, 6, (h, w))
    idx = ndi_smooth(idx)
    alpha = np.zeros((h, w), bool)
    yy, xx = np.mgrid[:h, :w]
    alpha[:, :] = True
    for (y, x) in [(0, 0), (0, 1), (1, 0), (0, w - 1), (0, w - 2), (1, w - 1), (h - 1, 0), (h - 1, w - 1)]:
        alpha[y, x] = False
    true = np.dstack([pal[idx], alpha.astype(float)]).astype(np.float32)
    true[~alpha, :3] = 0
    sizes_x = [6 + (i % 3 == 0) for i in range(w)]
    sizes_y = [7 - (j % 4 == 0) for j in range(h)]
    big = np.repeat(np.repeat(true, sizes_y, axis=0), sizes_x, axis=1)
    big = np.pad(big, ((23, 17), (19, 21), (0, 0)))
    from scipy import ndimage as ndi
    soft = np.stack([ndi.gaussian_filter(big[..., c], 0.8) for c in range(4)], -1)
    soft[..., :3] += rng.normal(0, 0.025, soft[..., :3].shape)
    return true, np.clip(soft, 0, 1).astype(np.float32)


def normal_and_anim_tests():
    from spriteforge import normals
    disc = np.zeros((40, 40, 4), np.float32)
    yy, xx = np.mgrid[:40, :40]
    m = (yy - 20) ** 2 + (xx - 20) ** 2 <= 17 ** 2
    disc[m] = [0.6, 0.6, 0.6, 1]
    for pix in (True, False):
        n = normals.normal_map(disc, pixel=pix)
        check(n[20, 5, 0] < 0.45 and n[20, 35, 0] > 0.55, f"normals (pixel={pix}): left edge faces left, right edge faces right")
        check(n[5, 20, 1] > 0.55 and n[35, 20, 1] < 0.45, f"normals (pixel={pix}): top faces up (OpenGL green up)")
        check(abs(n[20, 20, 2] - 1.0) < 0.15, f"normals (pixel={pix}): centre faces the viewer")
    nd = normals.normal_map(disc, pixel=True, y_up=False)
    check(nd[5, 20, 1] < 0.45, "DirectX convention flips green")
    k = normals.normal_map(disc, pixel=True)
    dirs = len(np.unique((k[m][:, :3] * 255).round().astype(int), axis=0))
    check(dirs <= 17, f"pixel normals are snapped to few directions ({dirs})")
    # enlarged pixel art is read back at its native size
    true, _ = fake_pixel_art()
    big = sprite.upscale_nearest(true, 8)
    path = os.path.join(TMP, "x8.png")
    sprite.save_png(big, path)
    small, k8 = pipeline.load_pixel_art(path)
    check(k8 == 8 and small.shape[:2] == true.shape[:2], f"x8 copy read back as {small.shape[:2]} at scale {k8}")
    gif = pipeline.write_gif([true, true[:, ::-1]], os.path.join(TMP, "t.gif"), 4, 4)
    check(os.path.getsize(gif) > 100, "animated GIF written")
    check(len(pipeline._pose_list("walking forward", 0)) == 4, "built-in walk poses found in a description")
    check(len(pipeline._pose_list("the flame flickering", 6)) == 6, "6 key moments for a free description")
    check(pipeline._pose_list("x", 3, ["a", "b"]) == ["a", "b"], "explicit poses win")
    from spriteforge import motion
    for m in motion.MOTIONS:
        fr = motion.frames_for(true, m)
        cols = lambda f: {tuple(c) for c in (f[f[..., 3] > 0][:, :3] * 255).round().astype(int)}
        allowed = cols(true) | {(255, 255, 255)}
        same = len({f.shape for f in fr}) == 1
        check(same and all(cols(f) <= allowed for f in fr), f"procedural {m}: {len(fr)} equal frames, no new colours")
    check(motion.detect("bouncing happily") == "bounce" and motion.detect("swinging a sword") is None, "motion detection")


def ndi_smooth(idx):
    from scipy import ndimage as ndi
    return ndi.median_filter(idx, 3)


def pixel_tests():
    true, fake = fake_pixel_art()
    for grid in ("auto", 32):
        out, info, _pal = pixel.pixelize(fake, grid, colors=6)
        same_size = out.shape[:2] == true.shape[:2]
        acc = None
        if same_size:
            m = true[..., 3] > 0
            acc = float((np.abs(out[..., :3] - true[..., :3]).max(-1)[m] < 0.12).mean())
            alpha_ok = float(((out[..., 3] > 0) == m).mean())
        check(same_size, f"grid={grid}: recovered size {out.shape[:2]} (true {true.shape[:2]}), cell {info['cell']}")
        if same_size:
            check(acc > 0.9 and alpha_ok > 0.95, f"grid={grid}: {acc:.0%} of pixels have the right colour, alpha {alpha_ok:.0%}")
        check(info["partial_alpha"] == 0, "no semi-transparent pixels")
    naive, _ = sprite._resize(sprite.crop(fake), 24, 32), None
    m = true[..., 3] > 0
    nacc = float((np.abs(naive[..., :3] - true[..., :3]).max(-1)[m] < 0.12).mean())
    print(f"     (a plain resize gets {nacc:.0%} of pixels right)")


if __name__ == "__main__":
    main()

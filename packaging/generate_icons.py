#!/usr/bin/env python3
"""Build Tubing Master .icns (macOS) and .ico (Windows) from packaging/icons/icon_1024.png."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICONS = ROOT / "icons"
SRC = ICONS / "icon_1024.png"
MAC_SQUARE = ICONS / "icon_mac_square.png"

# How much of the canvas the cropped artwork fills (after trimming flat blue).
# ~0.84 matches macOS Dock safe area; 0.96 looked oversized next to system icons.
_ARTWORK_SCALE = 0.84
# Superellipse exponent — ~5 matches macOS Big Sur+ squircle closely.
_SQUIRCLE_N = 5.0


def _require_source() -> None:
    if not SRC.is_file():
        raise SystemExit(f"Missing source icon: {SRC}")


def _require_pillow():
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("Install Pillow: pip install pillow") from exc
    return Image


def _background_rgba(img) -> tuple[int, int, int, int]:
    """Sample flat background from source corners."""
    w, h = img.size
    samples = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    r = sum(p[0] for p in samples) // 4
    g = sum(p[1] for p in samples) // 4
    b = sum(p[2] for p in samples) // 4
    a = sum(p[3] if len(p) > 3 else 255 for p in samples) // 4
    return r, g, b, a


def _crop_to_content(img, bg: tuple[int, int, int, int], *, padding_ratio: float = 0.02):
    """Trim flat background from source art so tube/die can scale up."""
    import numpy as np

    arr = np.array(img.convert("RGBA"))
    rgb = arr[:, :, :3].astype(np.int16)
    bg_rgb = np.array(bg[:3], dtype=np.int16)
    diff = np.abs(rgb - bg_rgb).max(axis=2)
    mask = diff > 28
    if not mask.any():
        return img

    ys, xs = np.where(mask)
    w, h = img.size
    span = max(int(xs.max() - xs.min()), int(ys.max() - ys.min()), 1)
    pad = int(round(padding_ratio * span))
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(w, int(xs.max()) + 1 + pad)
    y1 = min(h, int(ys.max()) + 1 + pad)
    return img.crop((x0, y0, x1, y1))


def _squircle_mask(size: int):
    """macOS-style rounded square (superellipse) alpha mask."""
    import numpy as np
    from PIL import Image

    y, x = np.ogrid[:size, :size]
    cx = cy = (size - 1) / 2.0
    a = b = size / 2.0
    nx = np.abs(x - cx) / a
    ny = np.abs(y - cy) / b
    inside = (nx**_SQUIRCLE_N + ny**_SQUIRCLE_N) <= 1.0
    alpha = (inside.astype(np.uint8) * 255)
    return Image.fromarray(alpha, mode="L")


def build_mac_icon(size: int, *, squircle: bool):
    """
    Prepare icon canvas for macOS-like display.

    ``squircle=False`` — full square with inset artwork (for .icns; macOS adds its mask).
    ``squircle=True`` — same inset + squircle alpha (Qt window icon, .ico, previews).
    """
    Image = _require_pillow()
    Resampling = Image.Resampling

    src = Image.open(SRC).convert("RGBA")
    src = src.resize((size, size), Resampling.LANCZOS)
    bg = _background_rgba(src)
    content = _crop_to_content(src, bg)

    inner = max(1, int(round(size * _ARTWORK_SCALE)))
    cw, ch = content.size
    scale = min(inner / cw, inner / ch)
    new_w = max(1, int(round(cw * scale)))
    new_h = max(1, int(round(ch * scale)))
    artwork = content.resize((new_w, new_h), Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), bg)
    canvas.paste(artwork, ((size - new_w) // 2, (size - new_h) // 2), artwork)

    if not squircle:
        return canvas

    mask = _squircle_mask(size)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)
    return out


def write_mac_square_master() -> Path:
    """Square master with safe inset — input for iconutil / .icns."""
    Image = _require_pillow()
    img = build_mac_icon(1024, squircle=False)
    img.save(MAC_SQUARE, format="PNG")
    return MAC_SQUARE


def build_ico() -> Path:
    Image = _require_pillow()

    out = ICONS / "icon.ico"
    master = build_mac_icon(256, squircle=True)
    master.save(
        out,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return out


def build_runtime_png() -> Path:
    """256×256 squircle PNG for Qt window / taskbar icon."""
    Image = _require_pillow()
    Resampling = Image.Resampling

    out = ROOT.parent / "tubing_master" / "assets" / "icon.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img = build_mac_icon(256, squircle=True)
    img.save(out, format="PNG")
    return out


def build_icns() -> Path:
    if sys.platform != "darwin":
        raise SystemExit("icon.icns requires macOS (iconutil). Run on Mac or skip.")

    master = MAC_SQUARE if MAC_SQUARE.is_file() else write_mac_square_master()

    iconset = ICONS / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()

    spec = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for name, px in spec:
        subprocess.run(
            ["sips", "-z", str(px), str(px), str(master), "--out", str(iconset / name)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    out = ICONS / "icon.icns"
    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    finally:
        shutil.rmtree(iconset, ignore_errors=True)
    return out


def main() -> None:
    _require_source()
    _require_pillow()
    ICONS.mkdir(parents=True, exist_ok=True)

    square = write_mac_square_master()
    print(f"Wrote {square}")

    preview = ICONS / "icon_squircle_preview.png"
    build_mac_icon(1024, squircle=True).save(preview, format="PNG")
    print(f"Wrote {preview}")

    ico = build_ico()
    print(f"Wrote {ico}")

    png = build_runtime_png()
    print(f"Wrote {png}")

    if sys.platform == "darwin":
        icns = build_icns()
        print(f"Wrote {icns}")
    else:
        print("Skipped icon.icns (not on macOS)")


if __name__ == "__main__":
    main()

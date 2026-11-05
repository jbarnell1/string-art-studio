"""Cut the chosen Gemini logo candidate into the app icons.

    .venv/Scripts/python.exe scripts/make_icons.py docs/brand/candidates/01-ring-portrait.jpg

Writes stringart/web/static/icons/{icon-512,icon-192,maskable-512,favicon-64}.png and
docs/brand/mark.png (the trimmed mark on its paper background).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "stringart" / "web" / "static" / "icons"
PAPER = (246, 243, 238)


def trim_to_mark(img: Image.Image, pad_frac: float = 0.06) -> Image.Image:
    arr = np.asarray(img.convert("RGB")).astype(np.int32)
    bg = np.median(arr[:8, :8].reshape(-1, 3), axis=0)
    diff = np.abs(arr - bg).sum(axis=2)
    ys, xs = np.nonzero(diff > 60)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    side = max(y1 - y0, x1 - x0)
    cy, cx = (y0 + y1) // 2, (x0 + x1) // 2
    half = int(side * (0.5 + pad_frac))
    box = (cx - half, cy - half, cx + half, cy + half)
    canvas = Image.new("RGB", (2 * half, 2 * half), PAPER)
    canvas.paste(img.convert("RGB"), (-box[0], -box[1]))
    return canvas


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/brand/candidates/01-ring-portrait.jpg"
    img = Image.open(src)
    mark = trim_to_mark(img)
    OUT.mkdir(parents=True, exist_ok=True)
    (ROOT / "docs/brand").mkdir(parents=True, exist_ok=True)
    mark.resize((1024, 1024), Image.LANCZOS).save(ROOT / "docs/brand/mark.png")
    mark.resize((512, 512), Image.LANCZOS).save(OUT / "icon-512.png")
    mark.resize((192, 192), Image.LANCZOS).save(OUT / "icon-192.png")
    mark.resize((64, 64), Image.LANCZOS).save(OUT / "favicon-64.png")
    # Maskable: the mark in the safe zone (inner 80%) on the paper tone.
    m = Image.new("RGB", (512, 512), PAPER)
    inner = mark.resize((400, 400), Image.LANCZOS)
    m.paste(inner, (56, 56))
    m.save(OUT / "maskable-512.png")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()

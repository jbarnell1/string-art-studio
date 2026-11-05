"""Benchmark both engines on a synthetic portrait-like target.

    .venv/Scripts/python.exe scripts/bench.py [--engine a|b] [--res 800] [--lines 4000] [--pins 240]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stringart.board import BoardConfig  # noqa: E402
from stringart.engine.greedy import run_greedy  # noqa: E402
from stringart.engine.render import comparison_image, encode_png, render_rgb  # noqa: E402
from stringart.engine.setcover import run_setcover  # noqa: E402
from stringart.engine.state import EngineParams, RenderState  # noqa: E402
from stringart.prep import board_weight  # noqa: E402


def synthetic_target(res: int) -> np.ndarray:
    """A soft 'face': light background, darker oval, two dark eyes, a mouth, hair mass."""
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float32) / res
    img = np.ones((res, res), dtype=np.float32)  # white
    face = ((xx - 0.5) / 0.28) ** 2 + ((yy - 0.52) / 0.36) ** 2 < 1
    img[face] = 0.72
    hair = (((xx - 0.5) / 0.32) ** 2 + ((yy - 0.36) / 0.3) ** 2 < 1) & (yy < 0.42)
    img[hair] = 0.12
    for cx in (0.4, 0.6):
        eye = ((xx - cx) / 0.045) ** 2 + ((yy - 0.5) / 0.03) ** 2 < 1
        img[eye] = 0.05
    mouth = (((xx - 0.5) / 0.1) ** 2 + ((yy - 0.68) / 0.025) ** 2 < 1) & (yy > 0.68)
    img[mouth] = 0.2
    shade = (xx > 0.62) & face
    img[shade] -= 0.25
    img = cv2.GaussianBlur(img, (0, 0), res / 160)
    return np.clip(img, 0, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="a")
    ap.add_argument("--res", type=int, default=800)
    ap.add_argument("--lines", type=int, default=4000)
    ap.add_argument("--pins", type=int, default=240)
    ap.add_argument("--refine", type=float, default=0.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--image", default="", help="use a real photo through the prep pipeline")
    ap.add_argument("--opacity", type=float, default=1.0)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--protect", type=float, default=0.5)
    ap.add_argument("--crop", default="0.5,0.5,1.0", help="cx,cy,size")
    args = ap.parse_args()

    board = BoardConfig(pin_count=args.pins, max_lines=args.lines, work_res=args.res, thread_opacity=args.opacity)
    cx, cy, cs = (float(v) for v in args.crop.split(","))
    if args.image:
        from stringart.prep import PrepSettings, load_source, prepare_target
        rgb = load_source(Path(args.image).read_bytes())
        gray = prepare_target(rgb, PrepSettings(white_point=0.92, black_point=0.05, gamma=args.gamma, crop_cx=cx, crop_cy=cy, crop_size=cs), board, args.res)
    else:
        gray = synthetic_target(args.res)
    target = 1.0 - gray
    yy, xx = np.mgrid[0:args.res, 0:args.res]
    r = args.res / 2
    weight = board_weight(board, args.res)
    state = RenderState(target, weight, board, light_protection=args.protect)
    print(f"res={args.res} a={state.a:.3f} sigma={state.sigma:.2f}px k={state.k:.3f} pad={state.pad}")
    params = EngineParams(engine=args.engine, refine_seconds=args.refine, time_budget_s=900)
    t0 = time.perf_counter()
    last = {"t": t0}

    def progress(ev):
        now = time.perf_counter()
        if now - last["t"] > 2:
            last["t"] = now
            print(f"  {ev['phase']}: lines={ev['lines']} t={ev['elapsed_s']:.1f}s")

    result = run_greedy(state, params, progress) if args.engine == "a" else run_setcover(state, params, progress)
    dt = time.perf_counter() - t0
    print(f"engine={result.engine} lines={result.line_count} error {result.error_initial:.4f} -> {result.error:.4f} "
          f"thread={result.thread_length_mm / 1000:.0f} m time={dt:.1f}s stop={result.stop_reason} "
          f"repair={result.repair_lines}/{result.repair_error:+.4f} refined={result.refined_changes}")
    if args.out:
        rgb = render_rgb(board, result.sequence, 1200)
        cmp_img = comparison_image(gray, rgb, blur_sigma_px=0)
        Path(args.out).write_bytes(encode_png(cmp_img))
        print("wrote", args.out)


if __name__ == "__main__":
    main()

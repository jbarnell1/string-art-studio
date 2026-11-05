"""Benchmark the engines on a synthetic face or a real photo through the prep pipeline.

    .venv/Scripts/python.exe scripts/bench.py --image samples/portrait-man.jpg --crop 0.5,0.42,0.62 \
        --gamma 0.75 --face 1 --structure 0.6 --refine 20 --out out.png

Prints the whole-board error and, when a face is detected, the error inside the face region, so
weight changes can be judged on what matters in a portrait.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stringart.board import BoardConfig  # noqa: E402
from stringart.engine.greedy import run_greedy  # noqa: E402
from stringart.engine.render import comparison_image, coverage_image, encode_png, render_rgb  # noqa: E402
from stringart.engine.setcover import run_setcover  # noqa: E402
from stringart.engine.state import EngineParams, RenderState  # noqa: E402
from stringart.faces import auto_importance, detect_faces  # noqa: E402
from stringart.prep import PrepSettings, board_weight, load_source, prepare_target  # noqa: E402


def synthetic_target(res: int) -> np.ndarray:
    """A soft 'face': light background, darker oval, two dark eyes, a mouth, hair mass."""
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float32) / res
    img = np.ones((res, res), dtype=np.float32)
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


def region_error(gray: np.ndarray, board: BoardConfig, seq: list[int], region: np.ndarray, sigma_px: float) -> float:
    """RMS of the blurred difference between target darkness and rendered coverage inside region."""
    res = gray.shape[0]
    cov = coverage_image(board, seq, res)
    t = 1.0 - gray
    if sigma_px > 0:
        cov = cv2.GaussianBlur(cov, (0, 0), sigma_px)
        t = cv2.GaussianBlur(t, (0, 0), sigma_px)
    d = (t - cov) ** 2
    return float(math.sqrt(float((d * region).sum()) / max(float(region.sum()), 1.0)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="a")
    ap.add_argument("--res", type=int, default=800)
    ap.add_argument("--lines", type=int, default=4000)
    ap.add_argument("--pins", type=int, default=240)
    ap.add_argument("--refine", type=float, default=20.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--image", default="", help="use a real photo through the prep pipeline")
    ap.add_argument("--opacity", type=float, default=1.0)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--protect", type=float, default=0.5)
    ap.add_argument("--crop", default="0.5,0.5,1.0", help="cx,cy,size")
    ap.add_argument("--face", type=float, default=0.0, help="face emphasis 0..2")
    ap.add_argument("--structure", type=float, default=0.0, help="structure weight 0..2")
    ap.add_argument("--clahe", type=float, default=0.0)
    ap.add_argument("--edge", type=float, default=0.0)
    ap.add_argument("--white", type=float, default=0.92)
    ap.add_argument("--black", type=float, default=0.05)
    args = ap.parse_args()

    board = BoardConfig(pin_count=args.pins, max_lines=args.lines, work_res=args.res, thread_opacity=args.opacity)
    if args.crop == "auto" and args.image:
        from stringart.faces import portrait_frame
        src = load_source(Path(args.image).read_bytes())
        fr = portrait_frame(detect_faces(src), src.shape[1], src.shape[0])
        cx, cy, cs = fr["crop_cx"], fr["crop_cy"], fr["crop_size"]
        print(f"auto frame: {fr}")
    else:
        cx, cy, cs = (float(v) for v in args.crop.split(","))
    prep = PrepSettings(white_point=args.white, black_point=args.black, gamma=args.gamma, crop_cx=cx, crop_cy=cy, crop_size=cs,
                        face_emphasis=args.face, structure_weight=args.structure, clahe_clip=args.clahe,
                        edge_strength=args.edge)
    auto = None
    faces = []
    face_region = None
    if args.image:
        rgb = load_source(Path(args.image).read_bytes())
        h, w = rgb.shape[:2]
        gray = prepare_target(rgb, prep, board, args.res)
        faces = detect_faces(rgb)
        auto = auto_importance(faces, prep, w, h, args.res, args.face) if args.face > 0 else None
        face_region = auto_importance(faces, prep, w, h, args.res, 1.0)
    else:
        gray = synthetic_target(args.res)
    target = 1.0 - gray
    weight = board_weight(board, args.res, auto_importance=auto, gray=gray, structure_weight=args.structure)
    state = RenderState(target, weight, board, light_protection=args.protect)
    print(f"res={args.res} a={state.a:.3f} sigma={state.sigma:.2f}px k={state.k:.3f} faces={len(faces)} "
          f"weight mean={weight.mean():.3f} max={weight.max():.2f}")
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
          f"repair={result.repair_lines}/{result.repair_error:+.4f} refined={result.refined_changes} "
          f"regrown={result.regrown_lines}")
    # Plain (unweighted) errors so runs with different weights stay comparable.
    board_region = (board_weight(board, args.res) > 0.999).astype(np.float32)
    line = f"plain error: board={region_error(gray, board, result.sequence, board_region, state.sigma):.4f}"
    if face_region is not None:
        face_mask = (face_region > 0.55).astype(np.float32)
        line += (f" face={region_error(gray, board, result.sequence, face_mask, state.sigma):.4f}"
                 f" face-sharp={region_error(gray, board, result.sequence, face_mask, 0):.4f}")
    print(line)
    if args.out:
        rgb_out = render_rgb(board, result.sequence, 1200)
        cmp_img = comparison_image(gray, rgb_out, blur_sigma_px=0)
        Path(args.out).write_bytes(encode_png(cmp_img))
        print("wrote", args.out)


if __name__ == "__main__":
    main()

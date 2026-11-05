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
from stringart.engine.state import dec, enc  # noqa: E402
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


def region_error(gray: np.ndarray, board: BoardConfig, seq: list[int], region: np.ndarray, sigma_px: float,
                 state: RenderState | None = None) -> tuple[float, float]:
    """Two RMS errors inside region after the blur: the v1 coverage error (target darkness 1 - gray
    against coverage) and the lightness error (encoded linear light, what a viewer sees)."""
    res = gray.shape[0]
    cov = coverage_image(board, seq, res)
    t = 1.0 - gray
    lin_t = dec(gray)
    yb, yt = (state.y_board, state.y_thread) if state is not None else (0.90, 0.007)
    lin_c = yb + (yt - yb) * cov
    if sigma_px > 0:
        cov = cv2.GaussianBlur(cov, (0, 0), sigma_px)
        t = cv2.GaussianBlur(t, (0, 0), sigma_px)
        lin_t = cv2.GaussianBlur(lin_t, (0, 0), sigma_px)
        lin_c = cv2.GaussianBlur(lin_c, (0, 0), sigma_px)
    d = (t - cov) ** 2
    dl = (enc(lin_t) - enc(lin_c)) ** 2
    n = max(float(region.sum()), 1.0)
    return float(math.sqrt(float((d * region).sum()) / n)), float(math.sqrt(float((dl * region).sum()) / n))


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
    ap.add_argument("--lookahead", type=int, default=1)
    ap.add_argument("--search", default="polish", help="polish, refine or none")
    ap.add_argument("--objective", default="linear", help="linear or coverage")
    ap.add_argument("--palette", default="", help="single, cmyk, warm, earth, primary, auto4, auto6 or hex,hex,...")
    ap.add_argument("--black", type=float, default=0.05)
    args = ap.parse_args()

    board = BoardConfig(pin_count=args.pins, max_lines=args.lines, work_res=args.res, thread_opacity=args.opacity)
    if args.palette:
        from stringart.engine.color import PALETTE_PRESETS
        if args.palette.startswith("auto"):
            board.palette_auto = int(args.palette[4:] or 4)
        else:
            board.palette = PALETTE_PRESETS.get(args.palette, args.palette.split(","))
        board.validate()
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
    weight = board_weight(board, args.res, auto_importance=auto, gray=gray, structure_weight=args.structure)
    if board.is_color and args.image:
        from stringart.engine.color import ColorState, auto_palette, color_name, palette_order, run_color_greedy
        from stringart.engine.render import render_layers_rgb
        from stringart.prep import prepare_target_rgb
        target_rgb = prepare_target_rgb(rgb, prep, board, args.res)
        colors = auto_palette(target_rgb, board.palette_auto, weight, board_color=board.board_color) if board.palette_auto >= 2 else list(board.palette)
        colors = palette_order(colors, board.invert)
        print("palette", [(c, color_name(c)) for c in colors])
        cstate = ColorState(target_rgb, weight, board, colors)
        params = EngineParams(engine="a", refine_seconds=args.refine, time_budget_s=900, local_search=args.search)
        t0 = time.perf_counter()
        seqs, result = run_color_greedy(cstate, params, lambda ev: None)
        dt = time.perf_counter() - t0
        print(f"colour lines={[len(s) - 1 for s in seqs]} error {result.error_initial:.4f} -> {result.error:.4f} "
              f"thread={result.thread_length_mm / 1000:.0f} m time={dt:.1f}s stop={result.stop_reason} refined={result.refined_changes} regrown={result.regrown_lines}")
        if args.out:
            layers = [{"color": c, "sequence": s} for c, s in zip(colors, seqs)]
            rgb_out = render_layers_rgb(board, layers, 1200)
            Path(args.out).write_bytes(encode_png(comparison_image(target_rgb, rgb_out, blur_sigma_px=0)))
            print("wrote", args.out)
        return
    state = RenderState(gray, weight, board, light_protection=args.protect, objective=args.objective)
    print(f"res={args.res} a={state.a:.3f} sigma={state.sigma:.2f}px k={state.k:.3f} faces={len(faces)} "
          f"weight mean={weight.mean():.3f} max={weight.max():.2f}")
    params = EngineParams(engine=args.engine, refine_seconds=args.refine, time_budget_s=900,
                          lookahead=args.lookahead, local_search=args.search, objective=args.objective)
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
          f"regrown={result.regrown_lines} rounds={result.polish_rounds}")
    # Plain (unweighted) errors so runs with different weights stay comparable.
    board_region = (board_weight(board, args.res) > 0.999).astype(np.float32)
    cb, lb = region_error(gray, board, result.sequence, board_region, state.sigma, state)
    line = f"plain error: board cov={cb:.4f} light={lb:.4f}"
    if face_region is not None:
        face_mask = (face_region > 0.55).astype(np.float32)
        cf, lf = region_error(gray, board, result.sequence, face_mask, state.sigma, state)
        _, lfs = region_error(gray, board, result.sequence, face_mask, 0, state)
        line += f" | face cov={cf:.4f} light={lf:.4f} light-sharp={lfs:.4f}"
    print(line)
    if args.out:
        rgb_out = render_rgb(board, result.sequence, 1200)
        cmp_img = comparison_image(gray, rgb_out, blur_sigma_px=0)
        Path(args.out).write_bytes(encode_png(cmp_img))
        print("wrote", args.out)


if __name__ == "__main__":
    main()

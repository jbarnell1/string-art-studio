"""`string-art-studio` command: serve the app, or generate from the command line."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="string-art-studio")
    sub = ap.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the web app (default)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--root-path", default=None, help="external path prefix, e.g. /stringart")
    gen = sub.add_parser("generate", help="generate a plan from a photo without the web app")
    gen.add_argument("image")
    gen.add_argument("--out", default="plan", help="output stem (writes <out>.json, <out>.png)")
    gen.add_argument("--engine", default="a", choices=["a", "b"])
    gen.add_argument("--pins", type=int, default=240)
    gen.add_argument("--lines", type=int, default=4000)
    gen.add_argument("--diameter", type=float, default=610.0)
    gen.add_argument("--gamma", type=float, default=0.8)
    gen.add_argument("--protect", type=float, default=0.5)
    gen.add_argument("--res", type=int, default=800)
    args = ap.parse_args(argv)

    if args.cmd in (None, "serve"):
        import uvicorn

        from stringart.config import load_settings
        s = load_settings()
        host = getattr(args, "host", None) or s.host
        port = getattr(args, "port", None) or s.port
        root = getattr(args, "root_path", None) or s.root_path
        uvicorn.run("stringart.web.app:factory", factory=True, host=host, port=port, root_path=root, log_level="info")
        return 0

    from stringart.board import BoardConfig, thread_estimate
    from stringart.engine.greedy import run_greedy
    from stringart.engine.render import encode_png, render_rgb
    from stringart.engine.setcover import run_setcover
    from stringart.engine.state import EngineParams, RenderState
    from stringart.prep import PrepSettings, board_weight, load_source, prepare_target, target_darkness

    board = BoardConfig(pin_count=args.pins, max_lines=args.lines, diameter_mm=args.diameter, work_res=args.res)
    prep = PrepSettings(gamma=args.gamma, black_point=0.04, white_point=0.92)
    rgb = load_source(Path(args.image).read_bytes())
    gray = prepare_target(rgb, prep, board, args.res)
    state = RenderState(target_darkness(gray, board), board_weight(board, args.res), board, light_protection=args.protect)
    params = EngineParams(engine=args.engine, time_budget_s=600)
    t0 = time.time()

    def progress(ev: dict) -> None:
        sys.stderr.write(f"\r{ev.get('phase', '')}: {ev.get('lines', 0)} lines, {time.time() - t0:.0f}s   ")

    result = run_greedy(state, params, progress) if args.engine == "a" else run_setcover(state, params, progress)
    sys.stderr.write("\n")
    est = thread_estimate(board, result.sequence)
    out = Path(args.out)
    out.with_suffix(".json").write_text(json.dumps({"board": board.to_dict(), "sequence": result.sequence,
                                                     "metrics": result.to_dict(), "thread": est.to_dict()}), encoding="utf-8")
    out.with_suffix(".png").write_bytes(encode_png(render_rgb(board, result.sequence, 1600)))
    print(f"{result.line_count} lines, error {result.error:.4f}, thread {est.length_mm / 1000:.0f} m "
          f"(buy {est.with_margin_mm / 1000:.0f} m), {result.elapsed_s:.0f}s -> {out.with_suffix('.json')}, {out.with_suffix('.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

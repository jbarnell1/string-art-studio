"""Generation jobs: a small worker pool, per-run progress, previews on disk and cancellation.

A job loads the project's image, builds the target and weights exactly as the editor previewed
them, runs the chosen engine, then writes the sequence, the final render and the comparison image
into the run's folder. Progress events fan out to any number of SSE listeners.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from stringart.board import BoardConfig, thread_estimate
from stringart.config import Settings
from stringart.db import Database
from stringart.engine.color import ColorState, auto_palette, color_name, palette_order, run_color_greedy
from stringart.engine.greedy import run_greedy
from stringart.engine.render import comparison_image, encode_jpeg, encode_png, render_layers_rgb
from stringart.exports.sequence import layers_thread_estimate
from stringart.engine.setcover import run_setcover
from stringart.engine.state import EngineParams, RenderState
from stringart.faces import auto_importance, detect_faces, portrait_frame
from stringart.prep import (PrepSettings, board_weight, decode_mask_png, load_source, prepare_target,
                            prepare_target_rgb, remove_background)

log = logging.getLogger("stringart.jobs")


class ProjectFiles:
    """Where a project's files live on disk."""

    def __init__(self, settings: Settings, project_id: str):
        self.root = settings.projects_dir / project_id
        self.runs = self.root / "runs"

    def ensure(self) -> None:
        self.runs.mkdir(parents=True, exist_ok=True)

    @property
    def source(self) -> Path | None:
        for ext in ("jpg", "jpeg", "png", "heic", "heif", "webp", "bmp", "tif", "tiff"):
            p = self.root / f"source.{ext}"
            if p.exists():
                return p
        return None

    @property
    def mask(self) -> Path:
        return self.root / "mask.png"

    @property
    def subject_alpha(self) -> Path:
        return self.root / "subject_alpha.npy"

    @property
    def faces(self) -> Path:
        return self.root / "faces.json"

    def layers(self, run_id: str) -> Path:
        return self.runs / run_id / "layers.json"

    def run_dir(self, run_id: str) -> Path:
        d = self.runs / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d


class ImageCache:
    """Decoded source images, keyed by path and modification time."""

    def __init__(self, capacity: int = 6):
        self.capacity = capacity
        self._items: dict[str, tuple[float, np.ndarray]] = {}
        self._lock = threading.Lock()

    def get(self, path: Path) -> np.ndarray:
        key = str(path)
        mtime = path.stat().st_mtime
        with self._lock:
            hit = self._items.get(key)
            if hit and hit[0] == mtime:
                return hit[1]
        rgb = load_source(path.read_bytes())
        with self._lock:
            if len(self._items) >= self.capacity:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (mtime, rgb)
        return rgb


class RunProgress:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"phase": "queued", "lines": 0, "elapsed_s": 0.0, "preview_version": 0}
        self.listeners: list[queue.Queue] = []
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.state.update(event)
            snapshot = dict(self.state)
            listeners = list(self.listeners)
        for q in listeners:
            try:
                q.put_nowait(snapshot)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self.lock:
            self.listeners.append(q)
            q.put_nowait(dict(self.state))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)


class JobManager:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="gen")
        self.progress: dict[str, RunProgress] = {}
        self.images = ImageCache()
        self._lock = threading.Lock()
        for run in db.stale_runs():  # the server restarted mid-run
            db.set_run_status(run["id"], "failed", error_message="server restarted during the run")

    def files(self, project_id: str) -> ProjectFiles:
        return ProjectFiles(self.settings, project_id)

    # Target building (shared with the live preview) -----------------------------------------

    def subject_alpha(self, project_id: str, rgb: np.ndarray, compute: bool) -> np.ndarray | None:
        files = self.files(project_id)
        if files.subject_alpha.exists():
            return np.load(files.subject_alpha)
        if not compute:
            return None
        alpha = remove_background(rgb)
        if alpha is not None:
            np.save(files.subject_alpha, alpha.astype(np.float32))
        return alpha

    def build_target(self, project_id: str, prep: PrepSettings, board: BoardConfig, res: int) -> np.ndarray:
        files = self.files(project_id)
        src = files.source
        if src is None:
            raise FileNotFoundError("project has no image")
        rgb = self.images.get(src)
        alpha = self.subject_alpha(project_id, rgb, compute=prep.background_removal) if prep.background_removal else None
        return prepare_target(rgb, prep, board, res, subject_alpha=alpha)

    def faces(self, project_id: str) -> dict[str, Any]:
        """Detected faces for the project's photo, cached beside it (keyed by the file's mtime)."""
        files = self.files(project_id)
        src = files.source
        if src is None:
            return {"faces": [], "width": 0, "height": 0, "frame": None}
        mtime = src.stat().st_mtime
        if files.faces.exists():
            try:
                cached = json.loads(files.faces.read_text(encoding="utf-8"))
                if cached.get("mtime") == mtime:
                    return cached
            except (ValueError, OSError):
                pass
        rgb = self.images.get(src)
        h, w = rgb.shape[:2]
        faces = detect_faces(rgb)
        out = {"mtime": mtime, "width": w, "height": h, "faces": faces, "frame": portrait_frame(faces, w, h) if faces else None}
        try:
            files.faces.write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass
        return out

    def build_target_rgb(self, project_id: str, prep: PrepSettings, board: BoardConfig, res: int) -> np.ndarray:
        files = self.files(project_id)
        src = files.source
        if src is None:
            raise FileNotFoundError("project has no image")
        rgb = self.images.get(src)
        alpha = self.subject_alpha(project_id, rgb, compute=prep.background_removal) if prep.background_removal else None
        return prepare_target_rgb(rgb, prep, board, res, subject_alpha=alpha)

    def resolve_palette(self, project_id: str, prep: PrepSettings, board: BoardConfig, target_rgb: np.ndarray | None = None,
                        weight: np.ndarray | None = None) -> list[str]:
        """Thread colours in build order for this board: the chosen palette, or k-means from the photo."""
        if not board.is_color:
            return [board.thread_color]
        if board.palette_auto >= 2:
            if target_rgb is None:
                target_rgb = self.build_target_rgb(project_id, prep, board, 400)
            colors = auto_palette(target_rgb, board.palette_auto, weight, board_color=board.board_color)
        else:
            colors = list(board.palette)
        return palette_order(colors, board.invert)

    def build_weight(self, project_id: str, prep: PrepSettings, board: BoardConfig, res: int,
                     gray: np.ndarray | None = None) -> np.ndarray:
        """Weights as the optimiser uses them: rim fade, painted mask, detected faces, structure."""
        files = self.files(project_id)
        importance = None
        if files.mask.exists():
            try:
                importance = decode_mask_png(files.mask.read_bytes())
            except ValueError:
                importance = None
        auto = None
        if prep.face_emphasis > 0:
            info = self.faces(project_id)
            if info["faces"]:
                auto = auto_importance(info["faces"], prep, info["width"], info["height"], res, prep.face_emphasis)
        return board_weight(board, res, importance, prep.mask_strength, auto_importance=auto, gray=gray,
                            structure_weight=prep.structure_weight)

    # Jobs ---------------------------------------------------------------------------------------

    def submit(self, run: dict[str, Any]) -> None:
        prog = RunProgress()
        with self._lock:
            self.progress[run["id"]] = prog
        self.pool.submit(self._execute, run, prog)

    def get_progress(self, run_id: str) -> RunProgress | None:
        with self._lock:
            return self.progress.get(run_id)

    def cancel(self, run_id: str) -> bool:
        prog = self.get_progress(run_id)
        if prog is None:
            return False
        prog.cancel.set()
        return True

    def _execute(self, run: dict[str, Any], prog: RunProgress) -> None:
        run_id = run["id"]
        project_id = run["project_id"]
        out = self.files(project_id).run_dir(run_id)
        try:
            if prog.cancel.is_set():
                raise _CancelledEarly()
            self.db.set_run_status(run_id, "running")
            prog.publish({"phase": "preparing", "status": "running"})
            prep = PrepSettings.from_dict(run["prep"])
            board = BoardConfig.from_dict(run["board"])
            params = EngineParams.from_dict(run["params"])
            params.engine = run["engine"]
            res = board.work_res
            gray = self.build_target(project_id, prep, board, res)
            weight = self.build_weight(project_id, prep, board, res, gray=gray)
            color = board.is_color
            target_rgb = self.build_target_rgb(project_id, prep, board, res) if color else None
            colors = self.resolve_palette(project_id, prep, board, target_rgb, weight) if color else [board.thread_color]
            if color:
                cstate = ColorState(target_rgb, weight, board, colors)
                cstate.cancel_check = prog.cancel.is_set
                state = None
                # A colour plan costs about three times a single thread per line (ADR-017).
                params.time_budget_s = min(params.time_budget_s * 3.0, 3600.0)
            else:
                state = RenderState(gray, weight, board, light_protection=params.light_protection,
                                    objective=params.objective)
                state.cancel_check = prog.cancel.is_set
            # The target as the optimiser saw it, for the compare view.
            shown = target_rgb if color else gray
            (out / "target.jpg").write_bytes(encode_jpeg((np.clip(shown, 0, 1) * 255).astype(np.uint8)))
            prog.publish({"phase": "generating", "status": "running", "colors": colors})
            last_preview = [0.0]

            def on_progress(ev: dict[str, Any]) -> None:
                preview = ev.pop("preview", None)
                now = time.time()
                if preview is not None and now - last_preview[0] > 0.7:
                    last_preview[0] = now
                    tmp = out / "live.tmp"
                    tmp.write_bytes(encode_jpeg(preview, quality=70))
                    tmp.replace(out / "live.jpg")
                    ev["preview_version"] = int(now * 1000)
                ev.setdefault("status", "running")
                prog.publish(ev)

            if color:
                seqs, result = run_color_greedy(cstate, params, on_progress)
                layers = [{"name": color_name(c), "color": c, "sequence": [int(p) for p in s]} for c, s in zip(colors, seqs)]
                layers = [layer for layer in layers if len(layer["sequence"]) >= 2] or layers[-1:]   # a colour with no lines is left out
                sigma, a_pass, objective = cstate.sigma, cstate.a, "lab"
            else:
                result = run_greedy(state, params, on_progress) if params.engine == "a" else run_setcover(state, params, on_progress)
                layers = [{"name": color_name(board.thread_color), "color": board.thread_color, "sequence": [int(p) for p in result.sequence]}]
                sigma, a_pass, objective = state.sigma, state.a, state.objective
            if result.cancelled:
                self.db.set_run_status(run_id, "cancelled", metrics=result.to_dict())
                prog.publish({"phase": "cancelled", "status": "cancelled"})
                return
            prog.publish({"phase": "rendering", "lines": result.line_count})
            seq = [p for layer in layers for p in layer["sequence"]]
            (out / "sequence.json").write_text(json.dumps(seq), encoding="utf-8")
            if color:
                (out / "layers.json").write_text(json.dumps(layers), encoding="utf-8")
            render = render_layers_rgb(board, layers, self.settings.render_res)
            (out / "render.png").write_bytes(encode_png(render))
            sigma_px = (board.viewing_distance_m * board.blur_mm_per_m) / board.pixel_mm(self.settings.render_res)
            cmp_img = comparison_image(shown, render, blur_sigma_px=0.0)
            (out / "compare.jpg").write_bytes(encode_jpeg(cmp_img, quality=85))
            cmp_blur = comparison_image(shown, render, blur_sigma_px=sigma_px)
            (out / "compare_blur.jpg").write_bytes(encode_jpeg(cmp_blur, quality=85))
            estimate = layers_thread_estimate(board, layers)
            metrics = result.to_dict()
            metrics.update({
                "thread_with_margin_mm": estimate["with_margin_mm"],
                "spools_1000yd": estimate["spools"],
                "work_res": res,
                "sigma_px": sigma,
                "coverage_per_pass": a_pass,
                "objective": objective,
                "layers": [{"name": layer["name"], "color": layer["color"], "lines": max(len(layer["sequence"]) - 1, 0),
                            "thread_length_mm": board.sequence_length_mm(layer["sequence"])} for layer in layers],
            })
            self.db.set_run_status(run_id, "done", metrics=metrics)
            prog.publish({"phase": "done", "status": "done", "lines": result.line_count, "metrics": metrics,
                          "preview_version": int(time.time() * 1000)})
        except _CancelledEarly:
            self.db.set_run_status(run_id, "cancelled")
            prog.publish({"phase": "cancelled", "status": "cancelled"})
        except Exception as err:  # noqa: BLE001
            log.error("run %s failed: %s\n%s", run_id, err, traceback.format_exc())
            self.db.set_run_status(run_id, "failed", error_message=str(err)[:500])
            prog.publish({"phase": "failed", "status": "failed", "error": str(err)[:500]})
        finally:
            def _forget() -> None:
                time.sleep(120)
                with self._lock:
                    self.progress.pop(run_id, None)
            threading.Thread(target=_forget, daemon=True).start()

    def shutdown(self) -> None:
        for prog in list(self.progress.values()):
            prog.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


class _CancelledEarly(Exception):
    pass

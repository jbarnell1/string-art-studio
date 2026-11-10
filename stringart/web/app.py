"""FastAPI application: the JSON API under /api and the PWA shell at /.

Routes are mounted at the root. When the app sits behind a path prefix (Tailscale serve strips it),
uvicorn's --root-path tells the shell where it lives; every URL the browser uses is relative to
that base."""
from __future__ import annotations

import json
import math
import logging
from contextlib import asynccontextmanager
import queue
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stringart import __version__
from stringart.board import BoardConfig, thread_estimate
from stringart.config import Settings, load_settings
from stringart.db import Database, new_id
from stringart.engine.state import EngineParams
from stringart.exports import bundle as bundle_mod
from stringart.exports import fab as fab_mod
from stringart.exports import sequence as seq_mod
from stringart.exports import template_pdf
from stringart import faces as faces_mod
from stringart.jobs import JobManager
from stringart.voice import EXTRA_PHRASES, VoiceError, VoiceService, change_phrase, pin_phrase
from stringart.engine.color import PALETTE_PRESETS, color_name
from stringart.estimate import build_estimate
from stringart.exports import jig as jig_mod
from stringart.prep import (PrepSettings, background_removal_available, encode_source_jpeg, load_source,
                            preview_jpeg)

log = logging.getLogger("stringart.web")
STATIC = Path(__file__).parent / "static"
ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "heic", "heif", "webp", "bmp", "tif", "tiff"}
MAX_UPLOAD = 40 * 1024 * 1024


class Context:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.projects_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.jobs = JobManager(settings, self.db)
        self.voice = VoiceService(settings.piper_url, settings.data_dir / "voice", settings.piper_voice)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ctx = Context(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        ctx.jobs.shutdown()
        ctx.db.close()

    app = FastAPI(title="String Art Studio", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    app.state.ctx = ctx

    def get_ctx() -> Context:
        return ctx

    register_api(app, get_ctx)
    register_shell(app)
    return app


# ------------------------------------------------------------------------------------------------
# Shell: index, manifest, service worker, static files
# ------------------------------------------------------------------------------------------------

def _base(request: Request) -> str:
    root = request.scope.get("root_path", "") or ""
    return root.rstrip("/")


def register_shell(app: FastAPI) -> None:
    index_html = (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        base = _base(request)
        html = index_html.replace("__BASE__", base).replace("__VERSION__", __version__)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest(request: Request) -> JSONResponse:
        base = _base(request)
        scope = (base or "") + "/"
        data = {
            "id": scope,
            "name": "String Art Studio",
            "short_name": "String Art",
            "description": "Turn a photo into a pin-and-thread build plan.",
            "start_url": scope,
            "scope": scope,
            "display": "standalone",
            "orientation": "any",
            "background_color": "#f3f2ee",
            "theme_color": "#f3f2ee",
            "icons": [
                {"src": f"{base}/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": f"{base}/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": f"{base}/static/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        }
        return JSONResponse(data, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ------------------------------------------------------------------------------------------------
# API
# ------------------------------------------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str = "Untitled"
    notes: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    notes: str | None = None
    prep: dict[str, Any] | None = None
    board: dict[str, Any] | None = None
    engine: dict[str, Any] | None = None


class PreviewIn(BaseModel):
    prep: dict[str, Any] = {}
    board: dict[str, Any] = {}
    size: int = 640


class RunIn(BaseModel):
    engine: str = "a"           # "a", "b" or "both"
    prep: dict[str, Any] | None = None
    board: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    label: str = ""


class PresetIn(BaseModel):
    name: str
    board: dict[str, Any]
    engine: dict[str, Any] = {}


class VoiceIn(BaseModel):
    voice: str | None = None
    speed: float = 1.0


class ProgressIn(BaseModel):
    step: int
    device: str = ""


class LabelIn(BaseModel):
    label: str


def _project_or_404(ctx: Context, pid: str) -> dict[str, Any]:
    p = ctx.db.get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _run_or_404(ctx: Context, rid: str) -> dict[str, Any]:
    r = ctx.db.get_run(rid)
    if not r:
        raise HTTPException(404, "run not found")
    return r


def _read_sequence(ctx: Context, run: dict[str, Any]) -> list[int]:
    p = ctx.jobs.files(run["project_id"]).run_dir(run["id"]) / "sequence.json"
    if not p.exists():
        raise HTTPException(409, "run has no sequence yet")
    return json.loads(p.read_text(encoding="utf-8"))


def _read_layers(ctx: Context, run: dict[str, Any]) -> list[dict[str, Any]]:
    """Thread layers in build order: layers.json for colour runs, else the flat sequence as one layer."""
    files = ctx.jobs.files(run["project_id"])
    p = files.layers(run["id"])
    if p.exists():
        layers = json.loads(p.read_text(encoding="utf-8"))
        # A colour that got no lines is not a layer anyone strings.
        used = [layer for layer in layers if len(layer.get("sequence") or []) >= 2]
        return used or layers[-1:]
    board = BoardConfig.from_dict(run["board"])
    return [{"name": color_name(board.thread_color), "color": board.thread_color, "sequence": _read_sequence(ctx, run)}]


def _change_phrases(board: BoardConfig, layers: list[dict[str, Any]]) -> list[str]:
    """One phrase per colour change (before layer 1, 2, ...): tie off, change, start."""
    out = []
    for prev, layer in zip(layers[:-1], layers[1:]):
        tie = int(prev["sequence"][-1]) if prev["sequence"] else 0
        start = int(layer["sequence"][0]) if layer["sequence"] else 0
        out.append(change_phrase(layer["name"], tie, start, board.sector_label(start)))
    return out


def weight_overlay_png(w) -> bytes:
    import cv2
    import numpy as np
    res = w.shape[0]
    yy, xx = np.mgrid[0:res, 0:res]
    r = res / 2.0
    inside = (xx - r + 0.5) ** 2 + (yy - r + 0.5) ** 2 <= (r * 0.93) ** 2
    rel = np.where(w >= 1.0, (w - 1.0) / 2.0, -(1.0 - w))      # +1 at triple weight, -1 at zero
    rel = np.clip(rel, -1.0, 1.0) * inside
    rgba = np.zeros((res, res, 4), dtype=np.uint8)
    more = rel > 0
    rgba[..., 0] = np.where(more, 168, 47)
    rgba[..., 1] = np.where(more, 50, 112)
    rgba[..., 2] = np.where(more, 30, 187)
    rgba[..., 3] = (np.clip(np.abs(rel), 0, 1) * 170).astype(np.uint8)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    return buf.tobytes()


def _attachment(data: bytes, filename: str, media_type: str) -> Response:
    return Response(data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def register_api(app: FastAPI, get_ctx) -> None:  # noqa: C901 - one flat router is easier to read here

    @app.get("/api/health")
    def health(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        return {"ok": True, "version": __version__, "background_removal": background_removal_available(),
                "faces": faces_mod.available(), "voice": ctx.voice.configured(), "workers": ctx.settings.workers}

    @app.get("/api/defaults")
    def defaults() -> dict[str, Any]:
        return {"prep": PrepSettings().to_dict(), "board": BoardConfig().to_dict(), "engine": EngineParams().to_dict(),
                "background_removal": background_removal_available()}

    # Projects ----------------------------------------------------------------------------------

    @app.get("/api/projects")
    def list_projects(ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
        return ctx.db.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        p = ctx.db.create_project(body.name.strip() or "Untitled", PrepSettings().to_dict(), BoardConfig().to_dict(),
                                  EngineParams().to_dict(), body.notes)
        ctx.jobs.files(p["id"]).ensure()
        return p

    @app.get("/api/projects/{pid}")
    def get_project(pid: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        p = _project_or_404(ctx, pid)
        p["runs"] = ctx.db.list_runs(pid)
        return p

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: ProjectPatch, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        _project_or_404(ctx, pid)
        fields: dict[str, Any] = {}
        if body.name is not None:
            fields["name"] = body.name.strip() or "Untitled"
        if body.notes is not None:
            fields["notes"] = body.notes
        if body.prep is not None:
            fields["prep"] = PrepSettings.from_dict(body.prep).to_dict()
        if body.board is not None:
            fields["board"] = BoardConfig.from_dict(body.board).to_dict()
        if body.engine is not None:
            fields["engine"] = EngineParams.from_dict(body.engine).to_dict()
        return ctx.db.update_project(pid, **fields)  # type: ignore[return-value]

    @app.delete("/api/projects/{pid}", status_code=204)
    def delete_project(pid: str, ctx: Context = Depends(get_ctx)) -> Response:
        _project_or_404(ctx, pid)
        ctx.db.delete_project(pid)
        root = ctx.jobs.files(pid).root
        if root.exists():
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        return Response(status_code=204)

    # Image, mask, preview --------------------------------------------------------------------

    @app.post("/api/projects/{pid}/image")
    async def upload_image(pid: str, file: UploadFile = File(...), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        _project_or_404(ctx, pid)
        ext = (Path(file.filename or "upload.jpg").suffix.lstrip(".") or "jpg").lower()
        if ext not in ALLOWED_IMAGE_EXT:
            raise HTTPException(415, f"unsupported image type .{ext}")
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "image larger than 40 MB")
        try:
            rgb = load_source(data)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(400, f"could not decode image: {err}") from err
        files = ctx.jobs.files(pid)
        files.ensure()
        old = files.source
        if old is not None:
            old.unlink()
        if files.subject_alpha.exists():
            files.subject_alpha.unlink()
        if files.faces.exists():
            files.faces.unlink()
        # Stored re-encoded as JPEG: EXIF rotation applied, size capped, HEIC decoded once.
        (files.root / "source.jpg").write_bytes(encode_source_jpeg(rgb, max_side=2400, quality=93))
        h, w = rgb.shape[:2]
        return ctx.db.update_project(pid, has_image=True, image_width=w, image_height=h)  # type: ignore[return-value]

    @app.get("/api/projects/{pid}/image")
    def get_image(pid: str, size: int = 1200, ctx: Context = Depends(get_ctx)) -> Response:
        _project_or_404(ctx, pid)
        src = ctx.jobs.files(pid).source
        if src is None:
            raise HTTPException(404, "no image")
        rgb = ctx.jobs.images.get(src)
        return Response(encode_source_jpeg(rgb, max_side=max(200, min(size, 2400))), media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache"})

    @app.put("/api/projects/{pid}/mask")
    async def put_mask(pid: str, request: Request, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        _project_or_404(ctx, pid)
        data = await request.body()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "mask too large")
        files = ctx.jobs.files(pid)
        files.ensure()
        if not data:
            if files.mask.exists():
                files.mask.unlink()
            return ctx.db.update_project(pid, has_mask=False)  # type: ignore[return-value]
        files.mask.write_bytes(data)
        return ctx.db.update_project(pid, has_mask=True)  # type: ignore[return-value]

    @app.get("/api/projects/{pid}/mask")
    def get_mask(pid: str, ctx: Context = Depends(get_ctx)) -> Response:
        _project_or_404(ctx, pid)
        m = ctx.jobs.files(pid).mask
        if not m.exists():
            raise HTTPException(404, "no mask")
        return Response(m.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.get("/api/projects/{pid}/faces")
    def get_faces(pid: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        """Detected faces (normalised source coordinates) and a suggested portrait frame."""
        _project_or_404(ctx, pid)
        info = ctx.jobs.faces(pid)
        return {"faces": info["faces"], "width": info["width"], "height": info["height"], "frame": info.get("frame")}

    @app.post("/api/projects/{pid}/weightmap")
    def weightmap(pid: str, body: PreviewIn, ctx: Context = Depends(get_ctx)) -> Response:
        """The importance weights the optimiser would use, as a PNG overlay: red where an area
        counts more than normal, blue where it counts less, transparent where it is neutral."""
        _project_or_404(ctx, pid)
        prep = PrepSettings.from_dict(body.prep)
        board = BoardConfig.from_dict(body.board)
        size = max(200, min(body.size, 800))
        try:
            gray = ctx.jobs.build_target(pid, prep, board, size)
        except FileNotFoundError:
            raise HTTPException(404, "no image") from None
        w = ctx.jobs.build_weight(pid, prep, board, size, gray=gray)
        return Response(weight_overlay_png(w), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{pid}/preview")
    def preview(pid: str, body: PreviewIn, ctx: Context = Depends(get_ctx)) -> Response:
        _project_or_404(ctx, pid)
        prep = PrepSettings.from_dict(body.prep)
        board = BoardConfig.from_dict(body.board)
        size = max(200, min(body.size, 1200))
        try:
            img = ctx.jobs.build_target_rgb(pid, prep, board, size) if board.is_color else ctx.jobs.build_target(pid, prep, board, size)
        except FileNotFoundError:
            raise HTTPException(404, "no image") from None
        return Response(preview_jpeg(img), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{pid}/palette")
    def resolve_palette(pid: str, body: PreviewIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        """The thread colours a run with these settings would use, in build order, with names."""
        _project_or_404(ctx, pid)
        prep = PrepSettings.from_dict(body.prep)
        board = BoardConfig.from_dict(body.board)
        try:
            colors = ctx.jobs.resolve_palette(pid, prep, board)
        except FileNotFoundError:
            colors = [board.thread_color] if not board.palette else list(board.palette)
        return {"colors": [{"color": c, "name": color_name(c)} for c in colors], "is_color": board.is_color,
                "presets": PALETTE_PRESETS}

    # Runs ------------------------------------------------------------------------------------

    @app.post("/api/projects/{pid}/runs", status_code=201)
    def start_runs(pid: str, body: RunIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        project = _project_or_404(ctx, pid)
        if not project["has_image"]:
            raise HTTPException(409, "upload a photo first")
        prep = PrepSettings.from_dict(body.prep if body.prep is not None else project["prep"]).to_dict()
        board = BoardConfig.from_dict(body.board if body.board is not None else project["board"]).to_dict()
        params = EngineParams.from_dict(body.params if body.params is not None else project["engine"]).to_dict()
        ctx.db.update_project(pid, prep=prep, board=board, engine=params)
        engines = ["a", "b"] if body.engine == "both" else [body.engine if body.engine in ("a", "b") else "a"]
        if BoardConfig.from_dict(board).is_color:
            engines = ["a"]   # colour runs use engine A only (ADR-017)
        group = new_id() if len(engines) > 1 else ""
        runs = []
        for eng in engines:
            p = dict(params)
            p["engine"] = eng
            run = ctx.db.create_run(pid, eng, prep, board, p, group_id=group, label=body.label)
            ctx.jobs.submit(run)
            runs.append(run)
        return {"runs": runs}

    @app.get("/api/runs/{rid}")
    def get_run(rid: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        run = _run_or_404(ctx, rid)
        prog = ctx.jobs.get_progress(rid)
        run["progress"] = prog.state if prog else None
        run["build_progress"] = ctx.db.get_progress(rid)
        return run

    @app.patch("/api/runs/{rid}")
    def patch_run(rid: str, body: LabelIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        _run_or_404(ctx, rid)
        ctx.db.update_run(rid, label=body.label[:80])
        return _run_or_404(ctx, rid)

    @app.delete("/api/runs/{rid}", status_code=204)
    def delete_run(rid: str, ctx: Context = Depends(get_ctx)) -> Response:
        run = _run_or_404(ctx, rid)
        ctx.jobs.cancel(rid)
        ctx.db.delete_run(rid)
        import shutil
        shutil.rmtree(ctx.jobs.files(run["project_id"]).runs / rid, ignore_errors=True)
        return Response(status_code=204)

    @app.post("/api/runs/{rid}/cancel")
    def cancel_run(rid: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        run = _run_or_404(ctx, rid)
        if run["status"] in ("queued", "running"):
            if not ctx.jobs.cancel(rid):
                ctx.db.set_run_status(rid, "cancelled")
        return {"ok": True}

    @app.get("/api/runs/{rid}/events")
    async def run_events(rid: str, ctx: Context = Depends(get_ctx)) -> StreamingResponse:
        run = _run_or_404(ctx, rid)
        prog = ctx.jobs.get_progress(rid)

        async def gen():
            import asyncio
            if prog is None:
                yield f"data: {json.dumps({'phase': run['status'], 'status': run['status'], 'metrics': run['metrics']})}\n\n"
                return
            q = prog.subscribe()
            try:
                idle = 0.0
                while True:
                    try:
                        ev = q.get_nowait()
                        idle = 0.0
                    except queue.Empty:
                        await asyncio.sleep(0.25)
                        idle += 0.25
                        if idle >= 15:
                            idle = 0.0
                            yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("status") in ("done", "failed", "cancelled"):
                        return
            finally:
                prog.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def _run_file(ctx: Context, rid: str, name: str, media: str) -> FileResponse:
        run = _run_or_404(ctx, rid)
        p = ctx.jobs.files(run["project_id"]).run_dir(rid) / name
        if not p.exists():
            raise HTTPException(404, f"{name} not ready")
        return FileResponse(p, media_type=media, headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs/{rid}/live.jpg")
    def run_live(rid: str, ctx: Context = Depends(get_ctx)) -> FileResponse:
        return _run_file(ctx, rid, "live.jpg", "image/jpeg")

    @app.get("/api/runs/{rid}/render.png")
    def run_render(rid: str, ctx: Context = Depends(get_ctx)) -> FileResponse:
        return _run_file(ctx, rid, "render.png", "image/png")

    @app.get("/api/runs/{rid}/target.jpg")
    def run_target(rid: str, ctx: Context = Depends(get_ctx)) -> FileResponse:
        return _run_file(ctx, rid, "target.jpg", "image/jpeg")

    @app.get("/api/runs/{rid}/compare.jpg")
    def run_compare(rid: str, blur: int = 0, ctx: Context = Depends(get_ctx)) -> FileResponse:
        return _run_file(ctx, rid, "compare_blur.jpg" if blur else "compare.jpg", "image/jpeg")

    @app.get("/api/runs/{rid}/sequence.{fmt}")
    def run_sequence(rid: str, fmt: str, ctx: Context = Depends(get_ctx)) -> Response:
        run = _run_or_404(ctx, rid)
        layers = _read_layers(ctx, run)
        board = BoardConfig.from_dict(run["board"])
        stem = f"stringart-{rid}-sequence"
        if fmt == "json":
            return _attachment(seq_mod.sequence_json(board, layers), f"{stem}.json", "application/json")
        if fmt == "csv":
            return _attachment(seq_mod.sequence_csv(board, layers), f"{stem}.csv", "text/csv")
        if fmt == "txt":
            return _attachment(seq_mod.sequence_txt(board, layers), f"{stem}.txt", "text/plain")
        raise HTTPException(404, "format must be json, csv or txt")

    @app.get("/api/runs/{rid}/estimate")
    def run_estimate(rid: str, pace: float = 400.0, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        """Time, wraps per pin, brad length and spools for this plan at a building pace (lines an hour)."""
        run = _run_or_404(ctx, rid)
        return build_estimate(BoardConfig.from_dict(run["board"]), _read_layers(ctx, run), pace)

    @app.get("/api/runs/{rid}/jig.zip")
    def run_jig(rid: str, bed: float = 225.0, bit: float = 1.0, clearance: float = 0.2, skirt: int = 1,
                edge: float = 0.4, ctx: Context = Depends(get_ctx)) -> Response:
        """3D-printable drilling jig for this plan's board: keyed ring segments, a height block
        set from the build estimate, and a hole-fit test strip (ADR-021)."""
        run = _run_or_404(ctx, rid)
        board = BoardConfig.from_dict(run["board"])
        est = build_estimate(board, _read_layers(ctx, run))
        settings = jig_mod.JigSettings(bed_mm=bed, bit_mm=bit, hole_clearance_mm=clearance, skirt=bool(skirt),
                                       edge_clearance_mm=edge, gauge_height_mm=math.ceil(est["brad"]["exposed_mm"]))
        try:
            data = jig_mod.jig_zip(board, settings, embed_mm=est["brad"]["embed_mm"])
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        return _attachment(data, f"drill-jig-{board.pin_count}pins-{board.diameter_mm:.0f}mm.zip", "application/zip")

    @app.get("/api/runs/{rid}/steps")
    def run_steps(rid: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        """Everything build mode needs in one call."""
        run = _run_or_404(ctx, rid)
        layers = _read_layers(ctx, run)
        board = BoardConfig.from_dict(run["board"])
        seq = [int(p) for layer in layers for p in layer["sequence"]]
        starts, offset = [], 0
        for layer in layers:
            starts.append(offset)
            offset += len(layer["sequence"])
        labels = [board.sector_label(i) for i in range(board.pin_count)]
        return {"run_id": rid, "project_id": run["project_id"], "sequence": seq, "pin_count": board.pin_count,
                "sector_count": board.sector_count, "labels": labels,
                "layers": [{"name": l["name"], "color": l["color"], "start": s, "lines": max(len(l["sequence"]) - 1, 0)}
                           for l, s in zip(layers, starts)],
                "change_phrases": _change_phrases(board, layers),
                "phrases": [pin_phrase(i, lab) for i, lab in enumerate(labels)], "extra_phrases": list(EXTRA_PHRASES),
                "thread": seq_mod.layers_thread_estimate(board, layers), "progress": ctx.db.get_progress(rid),
                "metrics": run["metrics"]}

    # Voice (Piper) ------------------------------------------------------------------------------

    @app.get("/api/voice")
    def voice_status(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        return ctx.voice.status()

    @app.get("/api/voice/clip.wav")
    def voice_clip(text: str, voice: str | None = None, speed: float = 1.0, ctx: Context = Depends(get_ctx)) -> Response:
        if not ctx.voice.configured():
            raise HTTPException(503, "no voice server configured")
        try:
            data = ctx.voice.clip(text, voice, speed)
        except VoiceError as err:
            raise HTTPException(503, str(err)) from err
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=31536000"})

    @app.post("/api/runs/{rid}/voice/prewarm")
    def voice_prewarm(rid: str, body: VoiceIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        """Render every pin phrase this plan can need, so build mode never waits on synthesis."""
        run = _run_or_404(ctx, rid)
        if not ctx.voice.status()["available"]:
            raise HTTPException(503, "voice server not available")
        board = BoardConfig.from_dict(run["board"])
        layers = _read_layers(ctx, run)
        pins = sorted({int(p) for layer in layers for p in layer["sequence"]})
        phrases = [pin_phrase(i, board.sector_label(i)) for i in pins] + list(EXTRA_PHRASES) + _change_phrases(board, layers)
        return ctx.voice.prewarm(phrases, body.voice, body.speed)

    @app.get("/api/voice/prewarm/{job_id}")
    def voice_prewarm_status(job_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        job = ctx.voice.prewarm_status(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    @app.get("/api/runs/{rid}/template.pdf")
    def run_template(rid: str, paper: str = "letter", ctx: Context = Depends(get_ctx)) -> Response:
        run = _run_or_404(ctx, rid)
        board = BoardConfig.from_dict(run["board"])
        if paper not in ("letter", "a4", "single"):
            raise HTTPException(400, "paper must be letter, a4 or single")
        return _attachment(template_pdf.pin_template_pdf(board, paper=paper), f"pin-template-{paper}.pdf", "application/pdf")

    @app.get("/api/runs/{rid}/fab.{fmt}")
    def run_fab(rid: str, fmt: str, variant: str = "holes", hole: float | None = None,
                ctx: Context = Depends(get_ctx)) -> Response:
        run = _run_or_404(ctx, rid)
        board = BoardConfig.from_dict(run["board"])
        if fmt == "svg":
            return _attachment(fab_mod.fab_svg(board, hole_diameter_mm=hole, variant=variant), f"board-{variant}.svg", "image/svg+xml")
        if fmt == "dxf":
            return _attachment(fab_mod.fab_dxf(board, hole_diameter_mm=hole, variant=variant), f"board-{variant}.dxf", "application/dxf")
        raise HTTPException(404, "format must be svg or dxf")

    @app.get("/api/runs/{rid}/progress")
    def get_build_progress(rid: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        _run_or_404(ctx, rid)
        return ctx.db.get_progress(rid)

    @app.put("/api/runs/{rid}/progress")
    def put_build_progress(rid: str, body: ProgressIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        _run_or_404(ctx, rid)
        return ctx.db.set_progress(rid, max(0, body.step), body.device[:60])

    # Presets ------------------------------------------------------------------------------------

    @app.get("/api/presets")
    def list_presets(ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
        return ctx.db.list_presets()

    @app.post("/api/presets", status_code=201)
    def create_preset(body: PresetIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        return ctx.db.create_preset(body.name.strip()[:60] or "Preset", BoardConfig.from_dict(body.board).to_dict(),
                                    EngineParams.from_dict(body.engine).to_dict())

    @app.delete("/api/presets/{preset_id}", status_code=204)
    def delete_preset(preset_id: str, ctx: Context = Depends(get_ctx)) -> Response:
        ctx.db.delete_preset(preset_id)
        return Response(status_code=204)

    # Bundles ------------------------------------------------------------------------------------

    @app.get("/api/projects/{pid}/bundle.zip")
    def export_bundle(pid: str, ctx: Context = Depends(get_ctx)) -> Response:
        project = _project_or_404(ctx, pid)
        runs = []
        for run in ctx.db.list_runs(pid):
            p = ctx.jobs.files(pid).run_dir(run["id"]) / "sequence.json"
            run = dict(run)
            run["sequence"] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
            lp = ctx.jobs.files(pid).layers(run["id"])
            if lp.exists():
                run["layers"] = json.loads(lp.read_text(encoding="utf-8"))
            runs.append(run)
        files = ctx.jobs.files(pid)
        src = files.source
        data = bundle_mod.write_bundle(project, runs, src.read_bytes() if src else None,
                                       files.mask.read_bytes() if files.mask.exists() else None,
                                       src.suffix.lstrip(".") if src else "jpg")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in project["name"])[:40] or "project"
        return _attachment(data, f"{safe}-{pid}.zip", "application/zip")

    @app.post("/api/projects/import", status_code=201)
    async def import_bundle(file: UploadFile = File(...), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
        data = await file.read()
        try:
            parsed = bundle_mod.read_bundle(data)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        meta = parsed["project"]
        project = ctx.db.create_project(str(meta.get("name", "Imported"))[:80], PrepSettings.from_dict(meta.get("prep")).to_dict(),
                                        BoardConfig.from_dict(meta.get("board")).to_dict(),
                                        EngineParams.from_dict(meta.get("engine")).to_dict(), str(meta.get("notes", "")))
        files = ctx.jobs.files(project["id"])
        files.ensure()
        if parsed["source"]:
            rgb = load_source(parsed["source"])
            (files.root / "source.jpg").write_bytes(encode_source_jpeg(rgb, max_side=2400, quality=93))
            h, w = rgb.shape[:2]
            ctx.db.update_project(project["id"], has_image=True, image_width=w, image_height=h)
        if parsed["mask"]:
            files.mask.write_bytes(parsed["mask"])
            ctx.db.update_project(project["id"], has_mask=True)
        for run in parsed["runs"]:
            seq = run.get("sequence") or []
            if not seq:
                continue
            board = BoardConfig.from_dict(run.get("board")).to_dict()
            prep = PrepSettings.from_dict(run.get("prep")).to_dict()
            params = EngineParams.from_dict(run.get("params")).to_dict()
            new = ctx.db.create_run(project["id"], str(run.get("engine", "a")), prep, board, params,
                                    label=str(run.get("label", ""))[:80])
            out = files.run_dir(new["id"])
            (out / "sequence.json").write_text(json.dumps([int(p) for p in seq]), encoding="utf-8")
            layers = run.get("layers") or None
            if layers:
                (out / "layers.json").write_text(json.dumps(layers), encoding="utf-8")
            try:
                from stringart.engine.render import encode_png, render_layers_rgb
                bcfg = BoardConfig.from_dict(board)
                (out / "render.png").write_bytes(encode_png(render_layers_rgb(bcfg, layers or [{"color": bcfg.thread_color, "sequence": seq}], 1000)))
            except Exception:  # noqa: BLE001
                pass
            ctx.db.set_run_status(new["id"], "done", metrics=run.get("metrics") or {})
        return ctx.db.get_project(project["id"])  # type: ignore[return-value]


def factory() -> FastAPI:
    """uvicorn stringart.web.app:factory --factory"""
    return create_app()

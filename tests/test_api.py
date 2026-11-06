"""End-to-end API test: create a project, upload a photo, preview, generate with a tiny board,
wait for the run, fetch exports and the build steps, export and import the bundle."""
from __future__ import annotations

import io
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stringart.config import Settings
from stringart.web.app import create_app


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=0, root_path="", workers=1,
                        preview_res=320, render_res=500, allow_origins="", piper_url="", piper_voice="en_US-lessac-medium")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _photo_bytes() -> bytes:
    yy, xx = np.mgrid[0:300, 0:300]
    img = np.full((300, 300, 3), 240, dtype=np.uint8)
    disk = (xx - 150) ** 2 + (yy - 150) ** 2 < 90 ** 2
    img[disk] = (60, 60, 60)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG")
    return buf.getvalue()


def _wait_done(client: TestClient, run_id: str, timeout: float = 120.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("done", "failed", "cancelled"):
            return run
        time.sleep(0.3)
    raise AssertionError("run did not finish")


def test_full_flow(client: TestClient):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/").status_code == 200
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["scope"] == "/"

    p = client.post("/api/projects", json={"name": "Test"}).json()
    pid = p["id"]
    r = client.post(f"/api/projects/{pid}/image", files={"file": ("photo.jpg", _photo_bytes(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["has_image"] is True

    r = client.post(f"/api/projects/{pid}/preview", json={"prep": {"gamma": 0.8}, "board": {}, "size": 256})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    mask = Image.new("L", (64, 64), 128)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    assert client.put(f"/api/projects/{pid}/mask", content=buf.getvalue()).json()["has_mask"] is True

    board = {"pin_count": 100, "max_lines": 120, "work_res": 300, "min_skip": 10}
    params = {"refine_seconds": 1, "time_budget_s": 60}
    r = client.post(f"/api/projects/{pid}/runs", json={"engine": "both", "board": board, "params": params})
    assert r.status_code == 201
    runs = r.json()["runs"]
    assert len(runs) == 2 and runs[0]["group_id"] == runs[1]["group_id"]

    done = [_wait_done(client, run["id"]) for run in runs]
    for run in done:
        assert run["status"] == "done", run["error_message"]
        assert run["metrics"]["line_count"] > 10
        assert run["metrics"]["error"] < run["metrics"]["error_initial"]
    rid = done[0]["id"]

    assert client.get(f"/api/runs/{rid}/render.png").status_code == 200
    assert client.get(f"/api/runs/{rid}/compare.jpg").status_code == 200
    assert client.get(f"/api/runs/{rid}/target.jpg").status_code == 200
    steps = client.get(f"/api/runs/{rid}/steps").json()
    assert len(steps["sequence"]) == done[0]["metrics"]["line_count"] + 1
    assert len(steps["labels"]) == 100
    assert client.get(f"/api/runs/{rid}/sequence.csv").status_code == 200
    assert client.get(f"/api/runs/{rid}/sequence.json").status_code == 200
    assert client.get(f"/api/runs/{rid}/template.pdf?paper=single").content.startswith(b"%PDF")
    assert client.get(f"/api/runs/{rid}/fab.svg").status_code == 200
    assert client.get(f"/api/runs/{rid}/fab.dxf?variant=ring").status_code == 200

    est = client.get(f"/api/runs/{rid}/estimate?pace=300").json()
    assert est["lines"] == done[0]["metrics"]["line_count"] and est["lines_per_hour"] == 300
    assert est["brad"]["label"] and est["hours"]["total"] > est["hours"]["stringing"] > 0
    jig = client.get(f"/api/runs/{rid}/jig.zip?bed=225&bit=1.0&clearance=0.2&skirt=1")
    assert jig.status_code == 200 and jig.content[:2] == b"PK"
    assert client.put(f"/api/runs/{rid}/progress", json={"step": 7, "device": "test"}).json()["step"] == 7
    assert client.get(f"/api/runs/{rid}/progress").json()["step"] == 7

    preset = client.post("/api/presets", json={"name": "Small", "board": board}).json()
    assert any(x["id"] == preset["id"] for x in client.get("/api/presets").json())

    bundle = client.get(f"/api/projects/{pid}/bundle.zip")
    assert bundle.status_code == 200
    imported = client.post("/api/projects/import", files={"file": ("b.zip", bundle.content, "application/zip")}).json()
    assert imported["has_image"] is True
    assert len(client.get(f"/api/projects/{imported['id']}").json()["runs"]) == 2

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_cancel(client: TestClient):
    p = client.post("/api/projects", json={"name": "Cancel"}).json()
    client.post(f"/api/projects/{p['id']}/image", files={"file": ("photo.jpg", _photo_bytes(), "image/jpeg")})
    r = client.post(f"/api/projects/{p['id']}/runs", json={"engine": "a", "board": {"pin_count": 240, "max_lines": 4000, "work_res": 600},
                                                           "params": {"refine_seconds": 0}})
    rid = r.json()["runs"][0]["id"]
    time.sleep(0.5)
    assert client.post(f"/api/runs/{rid}/cancel").json()["ok"] is True
    run = _wait_done(client, rid)
    assert run["status"] == "cancelled"


def test_faces_weightmap_and_voice_endpoints(client: TestClient):
    health = client.get("/api/health").json()
    assert health["faces"] is True and health["voice"] is False
    p = client.post("/api/projects", json={"name": "Faces"}).json()
    pid = p["id"]
    client.post(f"/api/projects/{pid}/image", files={"file": ("photo.jpg", _photo_bytes(), "image/jpeg")})
    info = client.get(f"/api/projects/{pid}/faces").json()
    assert info["faces"] == [] and info["frame"] is None and info["width"] == 300
    r = client.post(f"/api/projects/{pid}/weightmap", json={"prep": {"structure_weight": 1.0}, "board": {}, "size": 256})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"\x89PNG")
    assert client.get("/api/voice").json()["available"] is False
    assert client.get("/api/voice/clip.wav?text=Pin+1.+A+1.").status_code == 503
    r = client.post(f"/api/projects/{pid}/runs", json={"engine": "a", "board": {"pin_count": 60, "max_lines": 40, "work_res": 200, "min_skip": 5},
                                                       "params": {"refine_seconds": 0.5}})
    rid = r.json()["runs"][0]["id"]
    run = _wait_done(client, rid)
    assert run["status"] == "done"
    steps = client.get(f"/api/runs/{rid}/steps").json()
    assert steps["phrases"][7] == "Pin 7. A 8." and "Voice on." in steps["extra_phrases"]
    assert client.post(f"/api/runs/{rid}/voice/prewarm", json={"speed": 1.0}).status_code == 503


def _color_photo_bytes() -> bytes:
    yy, xx = np.mgrid[0:300, 0:300]
    img = np.full((300, 300, 3), 245, dtype=np.uint8)
    img[(xx - 100) ** 2 + (yy - 95) ** 2 < 32 ** 2] = (215, 40, 30)      # small red disc, upper left
    img[(xx - 170) ** 2 + (yy - 190) ** 2 < 65 ** 2] = (20, 20, 20)      # large black disc, lower right
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG")
    return buf.getvalue()


def test_colour_run_layers_and_exports(client: TestClient):
    p = client.post("/api/projects", json={"name": "Colour"}).json()
    pid = p["id"]
    client.post(f"/api/projects/{pid}/image", files={"file": ("photo.jpg", _color_photo_bytes(), "image/jpeg")})
    board = {"pin_count": 80, "max_lines": 260, "work_res": 240, "min_skip": 8, "palette": ["#161616", "#d9261c"]}
    pal = client.post(f"/api/projects/{pid}/palette", json={"prep": {}, "board": board, "size": 240}).json()
    assert [c["name"] for c in pal["colors"]] == ["red", "black"] and pal["is_color"] is True
    r = client.post(f"/api/projects/{pid}/preview", json={"prep": {}, "board": board, "size": 240})
    assert r.status_code == 200
    r = client.post(f"/api/projects/{pid}/runs", json={"engine": "both", "board": board, "params": {"refine_seconds": 1}})
    runs = r.json()["runs"]
    assert len(runs) == 1  # colour plans run on engine A only
    run = _wait_done(client, runs[0]["id"])
    assert run["status"] == "done", run["error_message"]
    rid = run["id"]
    layers = run["metrics"]["layers"]
    assert [l["name"] for l in layers] == ["red", "black"], layers
    assert sum(l["lines"] for l in layers) == run["metrics"]["line_count"]
    steps = client.get(f"/api/runs/{rid}/steps").json()
    assert len(steps["layers"]) == 2 and steps["layers"][1]["start"] > 0
    assert len(steps["change_phrases"]) == 1 and "black thread" in steps["change_phrases"][0]
    csv_text = client.get(f"/api/runs/{rid}/sequence.csv").text.splitlines()
    assert csv_text[0] == "step,layer,color,pin,label,from_pin,length_mm"
    assert any(line.split(",")[2] == "black" for line in csv_text[1:])
    assert client.get(f"/api/runs/{rid}/render.png").status_code == 200
    bundle = client.get(f"/api/projects/{pid}/bundle.zip")
    imported = client.post("/api/projects/import", files={"file": ("c.zip", bundle.content, "application/zip")}).json()
    new_run = client.get(f"/api/projects/{imported['id']}").json()["runs"][0]
    assert len(client.get(f"/api/runs/{new_run['id']}/steps").json()["layers"]) == 2

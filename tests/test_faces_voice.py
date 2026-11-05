"""Face detection, automatic importance, and the Piper voice service (against a fake server)."""
from __future__ import annotations

import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from stringart import faces
from stringart.prep import PrepSettings, board_weight, load_source
from stringart.board import BoardConfig
from stringart.voice import VoiceError, VoiceService, pin_phrase

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "portrait-man.jpg"


def test_detects_the_sample_face_and_frames_it():
    rgb = load_source(SAMPLE.read_bytes())
    found = faces.detect_faces(rgb)
    assert len(found) == 1
    f = found[0]
    assert 0.2 < f["x"] < 0.5 and 0.3 < f["w"] < 0.6
    assert set(f["landmarks"]) == set(faces.LANDMARKS)
    h, w = rgb.shape[:2]
    frame = faces.portrait_frame(found, w, h)
    assert 0.5 <= frame["crop_size"] <= 1.0
    assert abs(frame["crop_cx"] - (f["x"] + f["w"] / 2)) < 0.05


def test_auto_importance_raises_face_weights():
    rgb = load_source(SAMPLE.read_bytes())
    found = faces.detect_faces(rgb)
    h, w = rgb.shape[:2]
    prep = PrepSettings(crop_cx=0.5, crop_cy=0.49, crop_size=0.71)
    imp = faces.auto_importance(found, prep, w, h, 256, 1.0)
    assert imp.shape == (256, 256) and imp.max() > 0.8 and imp.min() >= 0.5
    board = BoardConfig(work_res=256)
    plain = board_weight(board, 256)
    weighted = board_weight(board, 256, auto_importance=imp)
    centre = (slice(100, 156), slice(100, 156))
    assert weighted[centre].mean() > plain[centre].mean() * 1.5
    assert faces.auto_importance([], prep, w, h, 256, 1.0) is None


def test_structure_weight_follows_edges():
    gray = np.ones((256, 256), dtype=np.float32)
    gray[:, 128:] = 0.0
    board = BoardConfig(work_res=256)
    w = board_weight(board, 256, gray=gray, structure_weight=1.0)
    assert w[128, 128] > w[128, 40] * 1.5


def test_pin_phrase():
    assert pin_phrase(37, "C14") == "Pin 37. C 14."
    assert pin_phrase(0, "A1") == "Pin 0. A 1."


def _wav(n_samples: int = 200) -> bytes:
    data = b"\x00\x00" * n_samples
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
    return header + b"data" + struct.pack("<I", len(data)) + data


class _FakePiper(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path == "/voices":
            body = json.dumps({"en_US-lessac-medium": {"language": {"code": "en_US", "name_english": "English"}},
                               "en_GB-alan-medium": {"language": {"code": "en_GB"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n))
        _FakePiper.calls.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()
        self.wfile.write(_wav())


@pytest.fixture()
def fake_piper():
    srv = HTTPServer(("127.0.0.1", 0), _FakePiper)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _FakePiper.calls.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_voice_service_caches_clips(tmp_path: Path, fake_piper: str):
    svc = VoiceService(fake_piper, tmp_path / "voice")
    st = svc.status()
    assert st["available"] is True and st["default"] == "en_US-lessac-medium"
    assert [v["name"] for v in st["voices"]] == ["en_GB-alan-medium", "en_US-lessac-medium"]
    a = svc.clip("Pin 1. A 1.", None, 1.25)
    b = svc.clip("Pin 1. A 1.", None, 1.25)
    assert a.startswith(b"RIFF") and a == b
    assert len(_FakePiper.calls) == 1
    assert _FakePiper.calls[0]["voice"] == "en_US-lessac-medium"
    assert abs(_FakePiper.calls[0]["length_scale"] - 0.8) < 1e-6
    job = svc.prewarm(["Pin 1. A 1.", "Pin 2. A 2.", "Voice on."], None, 1.25)
    for _ in range(100):
        job = svc.prewarm_status(job["id"])
        if job["finished"]:
            break
        threading.Event().wait(0.05)
    assert job["finished"] and job["done"] == 3 and job["failed"] == 0
    assert len(_FakePiper.calls) == 3  # the cached one was not synthesised again


def test_voice_service_without_server(tmp_path: Path):
    svc = VoiceService("", tmp_path / "voice")
    assert svc.status()["available"] is False
    with pytest.raises(VoiceError):
        svc.clip("Pin 1. A 1.")
    down = VoiceService("http://127.0.0.1:9", tmp_path / "voice")
    assert down.status()["available"] is False

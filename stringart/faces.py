"""Face detection for portraits: framing suggestions and an automatic importance map.

Uses the YuNet model bundled in models/ (see models/README.md). Everything is in normalised
source coordinates (fractions of the image width and height) so the client can draw it on any
size of preview; the importance map is rendered in frame space with the same affine the prep
pipeline uses to crop, so it follows the frame exactly.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from stringart.prep import PrepSettings, frame_matrix

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
LANDMARKS = ("right_eye", "left_eye", "nose", "mouth_right", "mouth_left")
_lock = threading.Lock()
_detector: Any = None
_detector_size: tuple[int, int] = (0, 0)


def available() -> bool:
    return MODEL_PATH.exists() and hasattr(cv2, "FaceDetectorYN")


def detect_faces(rgb: np.ndarray, max_side: int = 640) -> list[dict[str, Any]]:
    """Faces sorted largest first. Each: x, y, w, h (fractions), score, landmarks {name: [x, y]}."""
    if not available():
        return []
    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(w, h))
    small = rgb
    if scale < 1.0:
        small = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    bgr = cv2.cvtColor(np.ascontiguousarray(small), cv2.COLOR_RGB2BGR)
    sh, sw = bgr.shape[:2]
    global _detector, _detector_size
    with _lock:
        if _detector is None:
            _detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (sw, sh), 0.6, 0.3, 50)
        if _detector_size != (sw, sh):
            _detector.setInputSize((sw, sh))
            _detector_size = (sw, sh)
        _, found = _detector.detect(bgr)
    if found is None:
        return []
    out = []
    for f in found:
        x, y, bw, bh = (float(v) / scale for v in f[:4])
        pts = (f[4:14].reshape(5, 2) / scale).astype(float)
        out.append({
            "x": x / w, "y": y / h, "w": bw / w, "h": bh / h, "score": float(f[14]),
            "landmarks": {name: [float(px) / w, float(py) / h] for name, (px, py) in zip(LANDMARKS, pts)},
        })
    out.sort(key=lambda d: d["w"] * d["h"], reverse=True)
    return out


def portrait_frame(faces: list[dict[str, Any]], w: int, h: int) -> dict[str, float]:
    """Head-and-shoulders crop around every detected face, as crop_cx, crop_cy, crop_size."""
    if not faces:
        return {"crop_cx": 0.5, "crop_cy": 0.5, "crop_size": 1.0}
    x0 = min(f["x"] for f in faces) * w
    y0 = min(f["y"] for f in faces) * h
    x1 = max(f["x"] + f["w"] for f in faces) * w
    y1 = max(f["y"] + f["h"] for f in faces) * h
    short = float(min(w, h))
    fh, fw = y1 - y0, x1 - x0
    # The face fills the circle: hair line and chin near the rim. The wheel zooms out for shoulders.
    side = max(fh * 1.4, fw * 1.5)
    side = float(min(max(side, short * 0.15), short))
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0 + 0.04 * fh
    half = side / 2.0
    cx = w / 2.0 if w < side else min(max(cx, half), w - half)
    cy = h / 2.0 if h < side else min(max(cy, half), h - half)
    return {"crop_cx": round(cx / w, 4), "crop_cy": round(cy / h, 4), "crop_size": round(side / short, 4)}


def auto_importance(faces: list[dict[str, Any]], prep: PrepSettings, w: int, h: int, res: int,
                    emphasis: float) -> np.ndarray | None:
    """Importance map in frame space at res px (0.5 neutral, 1.0 = three times the weight):
    an ellipse over each face, and discs over the eyes, nose and mouth. None without faces."""
    if not faces or emphasis <= 0:
        return None
    m = frame_matrix(prep, w, h, res)

    def to_frame(px: float, py: float) -> tuple[float, float]:
        return float(m[0, 0] * px + m[0, 1] * py + m[0, 2]), float(m[1, 0] * px + m[1, 1] * py + m[1, 2])

    scale = float(np.hypot(m[0, 0], m[0, 1]))
    face_layer = np.full((res, res), 0.5, dtype=np.float32)
    feature_layer = np.full((res, res), 0.5, dtype=np.float32)
    face_val = min(1.0, 0.5 + 0.22 * emphasis)
    feat_val = min(1.0, 0.5 + 0.36 * emphasis)
    blur = 1.0
    for f in faces:
        fx, fy, fw, fh = f["x"] * w, f["y"] * h, f["w"] * w, f["h"] * h
        cx, cy = to_frame(fx + fw / 2.0, fy + fh / 2.0)
        axes = (int(round(fw * scale * 0.62)), int(round(fh * scale * 0.72)))
        if min(axes) < 1:
            continue
        cv2.ellipse(face_layer, (int(round(cx)), int(round(cy))), axes, -prep.rotation, 0, 360, face_val, -1,
                    lineType=cv2.LINE_AA)
        lm = f.get("landmarks") or {}
        r = max(2, int(round(fh * scale * 0.17)))
        blur = max(blur, fh * scale * 0.05)
        pts = [to_frame(lm[name][0] * w, lm[name][1] * h) for name in ("right_eye", "left_eye", "nose") if name in lm]
        if "mouth_right" in lm and "mouth_left" in lm:
            mx = (lm["mouth_right"][0] + lm["mouth_left"][0]) / 2.0 * w
            my = (lm["mouth_right"][1] + lm["mouth_left"][1]) / 2.0 * h
            pts.append(to_frame(mx, my))
        for (px, py) in pts:
            cv2.circle(feature_layer, (int(round(px)), int(round(py))), r, feat_val, -1, lineType=cv2.LINE_AA)
    imp = np.maximum(face_layer, feature_layer)
    imp = cv2.GaussianBlur(imp, (0, 0), float(blur))
    return np.clip(imp, 0.0, 1.0).astype(np.float32)

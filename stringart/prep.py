"""Image preparation: turn the source photo into the target the optimiser matches.

The editor's settings map onto this pipeline one to one, and the preview endpoint runs the same
code at a smaller size, so what the user sees is exactly what the engine is asked to match.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, asdict
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from stringart.board import BoardConfig

try:  # HEIC from phones
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass

MAX_SOURCE_SIDE = 2400


@dataclass
class PrepSettings:
    # Crop in normalised source coordinates: centre and the side of the square frame as a
    # fraction of the shorter source side. Rotation in degrees, clockwise.
    crop_cx: float = 0.5
    crop_cy: float = 0.5
    crop_size: float = 1.0
    rotation: float = 0.0
    brightness: float = 0.0        # -1 .. 1
    contrast: float = 0.0          # -1 .. 1
    gamma: float = 1.0             # 0.3 .. 3
    black_point: float = 0.0       # 0 .. 1
    white_point: float = 1.0       # 0 .. 1
    # Brightest white (ADR-023): the lightest the target ever asks for. Below 1 even the highlights
    # get a light, even hatching; a pale area crossed by a few stray lines reads as a hole.
    white_level: float = 0.8       # 0.5 .. 1
    clahe_clip: float = 0.0        # 0 = off, up to 4
    clahe_grid: int = 8
    mix_r: float = 0.299
    mix_g: float = 0.587
    mix_b: float = 0.114
    edge_strength: float = 0.0     # 0 .. 2
    edge_radius: float = 2.0       # px at 1000 px
    background_removal: bool = False
    mask_strength: float = 1.0     # how strongly the painted importance mask counts
    face_emphasis: float = 1.0     # 0 .. 2: raise the weight of detected faces and their features
    structure_weight: float = 0.5  # 0 .. 2: raise the weight along edges of the target

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PrepSettings":
        data = dict(data or {})
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        p = cls(**known)
        p.validate()
        return p

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        self.crop_cx = float(min(max(self.crop_cx, 0.0), 1.0))
        self.crop_cy = float(min(max(self.crop_cy, 0.0), 1.0))
        self.crop_size = float(min(max(self.crop_size, 0.05), 3.0))
        self.rotation = float(((self.rotation + 180.0) % 360.0) - 180.0)
        self.brightness = float(min(max(self.brightness, -1.0), 1.0))
        self.contrast = float(min(max(self.contrast, -1.0), 1.0))
        self.gamma = float(min(max(self.gamma, 0.3), 3.0))
        self.black_point = float(min(max(self.black_point, 0.0), 0.95))
        self.white_point = float(min(max(self.white_point, self.black_point + 0.05), 1.0))
        self.white_level = float(min(max(self.white_level, 0.5), 1.0))
        self.clahe_clip = float(min(max(self.clahe_clip, 0.0), 4.0))
        self.clahe_grid = int(min(max(self.clahe_grid, 2), 16))
        self.edge_strength = float(min(max(self.edge_strength, 0.0), 2.0))
        self.edge_radius = float(min(max(self.edge_radius, 0.5), 10.0))
        self.mask_strength = float(min(max(self.mask_strength, 0.0), 3.0))
        self.face_emphasis = float(min(max(self.face_emphasis, 0.0), 2.0))
        self.structure_weight = float(min(max(self.structure_weight, 0.0), 2.0))


def load_source(data: bytes) -> np.ndarray:
    """Decode an upload (JPEG, PNG, HEIC, ...) to RGB uint8, EXIF-rotated and capped in size."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img.convert("RGBA"))
        img = bg.convert("RGB")
    else:
        img = img.convert("RGB")
    w, h = img.size
    scale = MAX_SOURCE_SIDE / max(w, h)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return np.asarray(img)


def encode_source_jpeg(rgb: np.ndarray, max_side: int = 1600, quality: int = 90) -> bytes:
    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def frame_matrix(prep: PrepSettings, w: int, h: int, res: int) -> np.ndarray:
    """2x3 affine from source pixels to frame pixels: rotate about the crop centre, then scale."""
    short = min(w, h)
    side = prep.crop_size * short
    cx, cy = prep.crop_cx * w, prep.crop_cy * h
    scale = res / side
    theta = math.radians(prep.rotation)
    cos, sin = math.cos(theta), math.sin(theta)
    a = scale * cos
    b = scale * sin
    return np.array([
        [a, b, res / 2.0 - (a * cx + b * cy)],
        [-b, a, res / 2.0 - (-b * cx + a * cy)],
    ], dtype=np.float64)


def frame_source(rgb: np.ndarray, prep: PrepSettings, res: int) -> np.ndarray:
    """Crop, zoom and rotate the source onto a res x res square (RGB float32 in [0, 1]).
    Areas outside the source are white."""
    h, w = rgb.shape[:2]
    m = frame_matrix(prep, w, h, res)
    scale = float(np.hypot(m[0, 0], m[0, 1]))
    flags = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    out = cv2.warpAffine(rgb, m, (res, res), flags=flags, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=(255, 255, 255))
    return out.astype(np.float32) / 255.0


def tone_pipeline(framed_rgb: np.ndarray, prep: PrepSettings) -> np.ndarray:
    """Grayscale target in [0, 1] (1 = white) from the framed RGB square."""
    mix = np.array([prep.mix_r, prep.mix_g, prep.mix_b], dtype=np.float32)
    total = float(mix.sum())
    mix = mix / total if abs(total) > 1e-6 else np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gray = framed_rgb @ mix
    gray = np.clip(gray, 0, 1)
    if prep.clahe_clip > 0:
        clahe = cv2.createCLAHE(clipLimit=prep.clahe_clip, tileGridSize=(prep.clahe_grid, prep.clahe_grid))
        gray = clahe.apply((gray * 255).astype(np.uint8)).astype(np.float32) / 255.0
    # Brightness and contrast around mid grey, then gamma.
    c = 1.0 + prep.contrast * (1.5 if prep.contrast > 0 else 1.0)
    gray = (gray - 0.5) * c + 0.5 + prep.brightness * 0.5
    gray = np.clip(gray, 0, 1)
    if abs(prep.gamma - 1.0) > 1e-3:
        gray = np.power(gray, 1.0 / prep.gamma)
    # Levels: black and white points stretch to true black and true white.
    gray = (gray - prep.black_point) / max(prep.white_point - prep.black_point, 1e-3)
    gray = np.clip(gray, 0, 1)
    if prep.edge_strength > 0:
        res = gray.shape[0]
        radius = max(0.5, prep.edge_radius * res / 1000.0)
        blurred = cv2.GaussianBlur(gray, (0, 0), radius)
        detail = gray - blurred
        gray = np.clip(gray + prep.edge_strength * 1.5 * detail, 0, 1)
    if prep.white_level < 1.0:
        gray = gray * prep.white_level
    return gray.astype(np.float32)


def remove_background(rgb: np.ndarray) -> np.ndarray | None:
    """Subject alpha in [0, 1] from a local model (rembg), or None when it is not installed."""
    try:
        from rembg import remove  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    out = remove(Image.fromarray(rgb))
    alpha = np.asarray(out.convert("RGBA"))[..., 3].astype(np.float32) / 255.0
    return alpha


def background_removal_available() -> bool:
    try:
        import rembg  # type: ignore  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def apply_subject_alpha(rgb: np.ndarray, alpha: np.ndarray | None, fill_white: bool = True) -> np.ndarray:
    if alpha is None:
        return rgb
    fill = 255 if fill_white else 0
    a = alpha[..., None]
    return np.clip(rgb.astype(np.float32) * a + fill * (1.0 - a), 0, 255).astype(np.uint8)


def prepare_target(rgb: np.ndarray, prep: PrepSettings, board: BoardConfig, res: int,
                   subject_alpha: np.ndarray | None = None) -> np.ndarray:
    """The grayscale the optimiser sees (1 = white), at res px, inside the frame."""
    src = apply_subject_alpha(rgb, subject_alpha, fill_white=not board.invert) if prep.background_removal else rgb
    framed = frame_source(src, prep, res)
    return tone_pipeline(framed, prep)


def prepare_target_rgb(rgb: np.ndarray, prep: PrepSettings, board: BoardConfig, res: int,
                       subject_alpha: np.ndarray | None = None) -> np.ndarray:
    """The colour target for a palette run: the framed photo with the same tone pipeline applied
    through its luminance, so chroma follows the grey the user tuned. Float32 sRGB in [0, 1]."""
    src = apply_subject_alpha(rgb, subject_alpha, fill_white=not board.invert) if prep.background_removal else rgb
    framed = frame_source(src, prep, res)
    gray_in = np.clip(framed @ np.array([0.299, 0.587, 0.114], dtype=np.float32), 1e-3, 1.0)
    gray_out = tone_pipeline(framed, prep)
    ratio = (gray_out / gray_in)[..., None]
    return np.clip(framed * ratio, 0.0, 1.0).astype(np.float32)


def target_darkness(gray: np.ndarray, board: BoardConfig) -> np.ndarray:
    """What thread coverage should look like: darkness on a light board, lightness on a dark one."""
    return gray.astype(np.float32) if board.invert else (1.0 - gray).astype(np.float32)


def importance_factor(imp: np.ndarray) -> np.ndarray:
    """Mask values to weight factors: 0.5 is neutral, 1.0 triples the weight, 0.0 nearly ignores."""
    imp = imp.astype(np.float32)
    return np.where(imp >= 0.5, 1.0 + (imp - 0.5) * 4.0, 0.1 + (imp / 0.5) * 0.9).astype(np.float32)


def structure_map(gray: np.ndarray, res: int) -> np.ndarray:
    """Where the target has edges (eyes, jaw, hair line), in [0, 1], widened a little so the
    weight covers both sides of each edge."""
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), max(0.8, res / 500.0))
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), max(1.0, res / 160.0))
    top = float(np.percentile(mag, 99.0))
    if top <= 1e-6:
        return np.zeros_like(mag)
    return np.clip(mag / top, 0.0, 1.0).astype(np.float32)


def board_weight(board: BoardConfig, res: int, importance: np.ndarray | None = None,
                 mask_strength: float = 1.0, auto_importance: np.ndarray | None = None,
                 gray: np.ndarray | None = None, structure_weight: float = 0.0) -> np.ndarray:
    """Importance weights at res px: one inside the board, fading to zero in a narrow band at the
    pin ring (threads pile up at the pins on every real piece; the eye forgives it, so the
    optimiser should too), times the painted importance mask, the automatic face map and the
    structure (edge) emphasis when they are given."""
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float32)
    r = res / 2.0
    dist = np.sqrt((xx - r + 0.5) ** 2 + (yy - r + 0.5) ** 2) * board.pixel_mm(res)
    inner = board.pin_radius_mm - 0.07 * board.diameter_mm
    outer = board.pin_radius_mm - 0.015 * board.diameter_mm
    w = np.clip((outer - dist) / max(outer - inner, 1e-3), 0.0, 1.0)
    if importance is not None:
        imp = cv2.resize(importance.astype(np.float32), (res, res), interpolation=cv2.INTER_LINEAR)
        factor = importance_factor(imp)
        factor = 1.0 + (factor - 1.0) * mask_strength
        w = w * factor.astype(np.float32)
    if auto_importance is not None:
        auto = auto_importance
        if auto.shape[0] != res:
            auto = cv2.resize(auto, (res, res), interpolation=cv2.INTER_LINEAR)
        w = w * importance_factor(auto)
    if gray is not None and structure_weight > 0:
        w = w * (1.0 + structure_weight * 1.5 * structure_map(gray, res))
    return w.astype(np.float32)


def decode_mask_png(data: bytes) -> np.ndarray:
    """Painted importance mask (grayscale PNG) to float [0, 1]; 0.5 means neutral."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("mask is not an image")
    if img.ndim == 3:
        if img.shape[2] == 4:
            alpha = img[..., 3].astype(np.float32) / 255.0
            gray = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            return gray * alpha + 0.5 * (1.0 - alpha)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img.astype(np.float32) / 255.0


def preview_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    """Grayscale (h, w) or RGB (h, w, 3) floats in [0, 1] to JPEG."""
    arr = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()

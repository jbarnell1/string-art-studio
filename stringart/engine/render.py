"""Final renders: thread-accurate simulation at any resolution, and the comparison image."""
from __future__ import annotations

import cv2
import numpy as np

from stringart.board import BoardConfig
from stringart.raster import wu_line


def hex_to_rgb(value: str, default=(0, 0, 0)):
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) != 6:
        return default
    try:
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError:
        return default


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(y: np.ndarray) -> np.ndarray:
    y = np.clip(y, 0.0, 1.0)
    return np.where(y <= 0.0031308, y * 12.92, 1.055 * np.power(y, 1 / 2.4) - 0.055).astype(np.float32)


def relative_luminance(hex_color: str, default: float) -> float:
    """Linear luminance (0 black .. 1 white) of a hex colour; default when it does not parse."""
    rgb = hex_to_rgb(hex_color, None)  # type: ignore[arg-type]
    if rgb is None:
        return default
    lin = srgb_to_linear(np.array(rgb, dtype=np.float32) / 255.0)
    return float(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])


def coverage_image(board: BoardConfig, sequence: list[int], size: int) -> np.ndarray:
    """Saturating thread coverage in [0, 1] at `size` px with anti-aliased lines."""
    cov = np.zeros(size * size, dtype=np.float32)
    if len(sequence) < 2:
        return cov.reshape(size, size)
    pins = board.pin_positions_px(size)
    a = float(min(max(board.thread_width_mm / board.pixel_mm(size) * board.thread_opacity, 0.02), 1.0))
    for i, j in zip(sequence[:-1], sequence[1:]):
        idx, w = wu_line(pins[i], pins[j], size)
        dx, dy = abs(pins[j][0] - pins[i][0]), abs(pins[j][1] - pins[i][1])
        a_line = min(a * float(np.hypot(dx, dy) / max(dx, dy, 1e-6)), 1.0)
        c = cov[idx]
        cov[idx] = c + a_line * w * (1.0 - c)
    return cov.reshape(size, size)


def render_rgb(board: BoardConfig, sequence: list[int], size: int, board_color: str | None = None,
               thread_color: str | None = None, draw_pins: bool = True) -> np.ndarray:
    """RGB uint8 picture of the finished single-thread piece."""
    return render_layers_rgb(board, [{"color": thread_color or board.thread_color, "sequence": sequence}], size,
                             board_color=board_color, draw_pins=draw_pins)


def render_layers_rgb(board: BoardConfig, layers: list[dict], size: int, board_color: str | None = None,
                      draw_pins: bool = True) -> np.ndarray:
    """RGB uint8 picture of a piece with one or more thread colours, composited in build order
    (the first layer is laid first and covered by the later ones) in linear light."""
    bg = srgb_to_linear(np.array(hex_to_rgb(board_color or board.board_color, (246, 243, 238)), dtype=np.float32) / 255.0)
    lin = np.broadcast_to(bg, (size, size, 3)).astype(np.float32).copy()
    for layer in layers:
        cov = coverage_image(board, [int(p) for p in layer.get("sequence") or []], size)[..., None]
        fg = srgb_to_linear(np.array(hex_to_rgb(layer.get("color") or board.thread_color, (22, 22, 22)), dtype=np.float32) / 255.0)
        # Light adds linearly: a half-covered pixel reflects half the light, which the screen must
        # show as the encoded value of that luminance, not as the midpoint of the two colours.
        lin = lin * (1.0 - cov) + fg[None, None, :] * cov
    out = (linear_to_srgb(lin) * 255.0 + 0.5).astype(np.uint8)
    fg = fg if layers else np.zeros(3, dtype=np.float32)
    # Outside the board stays a neutral paper tone so the circle reads as a board.
    yy, xx = np.mgrid[0:size, 0:size]
    r = size / 2.0
    outside = (xx - r + 0.5) ** 2 + (yy - r + 0.5) ** 2 > r * r
    out[outside] = (236, 234, 229)
    if draw_pins:
        pins = board.pin_positions_px(size)
        rad = max(1, int(round(board.pin_diameter_mm / board.pixel_mm(size) / 2.0)))
        pin_col = (120, 120, 120)
        for x, y in pins:
            cv2.circle(out, (int(round(x)), int(round(y))), rad, pin_col, -1, lineType=cv2.LINE_AA)
    return out


def gray_to_rgb(gray01: np.ndarray) -> np.ndarray:
    g = (np.clip(gray01, 0, 1) * 255).astype(np.uint8)
    return np.stack([g, g, g], axis=-1)


def comparison_image(target_gray: np.ndarray, render: np.ndarray, blur_sigma_px: float = 0.0,
                     gap: int = 24) -> np.ndarray:
    """Target (as the optimiser saw it) beside the render, optionally both blurred to the
    viewing-distance sigma so they compare the way the eye would."""
    size = render.shape[0]
    t = cv2.resize(target_gray.astype(np.float32), (size, size), interpolation=cv2.INTER_AREA)
    left = gray_to_rgb(t) if t.ndim == 2 else (np.clip(t, 0, 1) * 255).astype(np.uint8)
    right = render
    if blur_sigma_px > 0.3:
        # The eye blurs light, so blur in linear light and encode afterwards.
        def blur_lin(img: np.ndarray) -> np.ndarray:
            lin = srgb_to_linear(img.astype(np.float32) / 255.0)
            lin = cv2.GaussianBlur(lin, (0, 0), blur_sigma_px)
            return (linear_to_srgb(lin) * 255.0 + 0.5).astype(np.uint8)
        left = blur_lin(left)
        right = blur_lin(right)
    canvas = np.full((size, size * 2 + gap, 3), (236, 234, 229), dtype=np.uint8)
    canvas[:, :size] = left
    canvas[:, size + gap:] = right
    return canvas


def encode_png(rgb_or_gray: np.ndarray) -> bytes:
    img = rgb_or_gray
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""


def encode_jpeg(rgb_or_gray: np.ndarray, quality: int = 88) -> bytes:
    img = rgb_or_gray
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""

"""Line rasterisation shared by the engines and the final renderer. All functions are vectorised
over many lines at once, which is what makes scoring a few hundred candidates per step cheap."""
from __future__ import annotations

import numpy as np

# Padded sample slots carry this index: one past the last pixel, where the engine's flat buffers
# keep a spare zero, so gathers need no mask. Callers that want real pixels use the mask.
PAD_INDEX = -1


def sample_lines(p0: np.ndarray, p1: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-pixel samples along many lines.

    p0: (2,) or (m, 2) start points in pixel coordinates (x, y); p1: (m, 2) end points.
    Returns flat pixel indices (m, L) and a boolean mask (m, L); masked-out slots are padding
    and hold the index size * size (see PAD_INDEX). Each line gets max(|dx|, |dy|) + 1 samples,
    so a line visits each pixel once.
    """
    p1 = np.asarray(p1, dtype=np.float32)
    p0 = np.broadcast_to(np.asarray(p0, dtype=np.float32), p1.shape)
    d = p1 - p0
    counts = np.maximum(np.abs(d[:, 0]), np.abs(d[:, 1])).astype(np.int32) + 1
    L = int(counts.max()) if len(counts) else 1
    steps = np.arange(L, dtype=np.float32)[None, :]
    mask = steps < counts[:, None]
    t = steps / np.maximum(counts[:, None] - 1, 1).astype(np.float32)
    xs = p0[:, 0:1] + t * d[:, 0:1]
    ys = p0[:, 1:2] + t * d[:, 1:2]
    xi = np.clip(np.rint(xs).astype(np.int32), 0, size - 1)
    yi = np.clip(np.rint(ys).astype(np.int32), 0, size - 1)
    idx = yi * size + xi
    idx[~mask] = size * size
    return idx, mask


def step_factor(p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
    """Physical length of one sample step: 1 for axis-aligned lines, sqrt(2) for diagonals.
    A thread crossing a pixel diagonally covers that much more of it."""
    p1 = np.asarray(p1, dtype=np.float32)
    p0 = np.broadcast_to(np.asarray(p0, dtype=np.float32), p1.shape)
    d = np.abs(p1 - p0)
    major = np.maximum(d[:, 0], d[:, 1])
    return (np.hypot(d[:, 0], d[:, 1]) / np.maximum(major, 1e-6)).astype(np.float32)


def line_pixels(p0: np.ndarray, p1: np.ndarray, size: int) -> np.ndarray:
    """Flat pixel indices of one line (no padding)."""
    idx, mask = sample_lines(np.asarray(p0)[None, :], np.asarray(p1)[None, :], size)
    return idx[0][mask[0]]


def wu_line(p0: np.ndarray, p1: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Anti-aliased line (Xiaolin Wu style): two pixels per step with complementary weights.

    Returns flat indices and float32 weights in [0, 1]. Used by the final renderer where the
    thread-width coverage model wants fractional pixel coverage rather than nearest pixels.
    """
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    steep = abs(y1 - y0) > abs(x1 - x0)
    if steep:
        x0, y0, x1, y1 = y0, x0, y1, x1
    if x0 > x1:
        x0, x1, y0, y1 = x1, x0, y1, y0
    n = int(np.floor(x1) - np.floor(x0)) + 1
    xs = np.floor(x0) + np.arange(n, dtype=np.float32)
    grad = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
    ys = y0 + (xs - x0) * grad
    yf = np.floor(ys)
    frac = (ys - yf).astype(np.float32)
    ya = yf.astype(np.int32)
    yb = ya + 1
    xi = xs.astype(np.int32)
    if steep:
        rows_a, cols_a, rows_b, cols_b = xi, ya, xi, yb
    else:
        rows_a, cols_a, rows_b, cols_b = ya, xi, yb, xi
    rows = np.concatenate([rows_a, rows_b])
    cols = np.concatenate([cols_a, cols_b])
    w = np.concatenate([1.0 - frac, frac]).astype(np.float32)
    ok = (rows >= 0) & (rows < size) & (cols >= 0) & (cols < size) & (w > 1e-4)
    return (rows[ok] * size + cols[ok]).astype(np.int64), w[ok]


def bbox_of(idx: np.ndarray, size: int, pad: int) -> tuple[int, int, int, int]:
    """Row/col bounding box (y0, y1, x0, x1), exclusive ends, padded and clipped to the image."""
    ys = idx // size
    xs = idx % size
    y0 = max(int(ys.min()) - pad, 0)
    y1 = min(int(ys.max()) + pad + 1, size)
    x0 = max(int(xs.min()) - pad, 0)
    x1 = min(int(xs.max()) + pad + 1, size)
    return y0, y1, x0, x1

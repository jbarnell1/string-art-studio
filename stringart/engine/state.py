"""The shared model both engines optimise against.

Objective: E = sum_x W(x) * (blur(T)(x) - blur(C)(x))^2, where T is the target darkness, C the
simulated thread coverage, W the importance weights and blur a Gaussian sized from the viewing
distance. Adding a thread with per-pixel coverage delta d changes E by

    dE = sum W * blur(d)^2 - 2 * sum d * G,     G = blur(W * (blur(T) - blur(C)))

because the Gaussian is self-adjoint. G is kept up to date after every line (a local update on a
crop around the line), so scoring a candidate costs one gather along its pixels. The first term is
the blurred line's own energy, approximated as k * sum W d^2 with k the squared-kernel integral.

Coverage saturates: a pixel crossed by n threads of per-pass coverage a has C = 1 - (1 - a)^n, so
each new pass adds d = a * (1 - C). Removing one pass takes away d = (1 - C) * a / (1 - a).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Callable

import cv2
import numpy as np

from stringart.board import BoardConfig
from stringart.raster import bbox_of, sample_lines, step_factor

cv2.setNumThreads(2)


@dataclass
class EngineParams:
    engine: str = "a"                 # "a" greedy, "b" set + repair
    refine_seconds: float = 20.0      # engine A refinement budget
    start_pin: int = 0
    batch_add: int = 24               # engine B: chords added per scoring round
    improve_rounds: int = 3           # engine B: add/remove alternations
    time_budget_s: float = 240.0      # hard stop for either engine
    light_protection: float = 0.5     # 1 = over-darkening a light area costs as much as leaving a dark area light
    seed: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EngineParams":
        data = dict(data or {})
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        p = cls(**known)
        p.engine = p.engine if p.engine in ("a", "b", "both") else "a"
        p.refine_seconds = float(min(max(p.refine_seconds, 0.0), 600.0))
        p.batch_add = int(min(max(p.batch_add, 1), 200))
        p.improve_rounds = int(min(max(p.improve_rounds, 0), 10))
        p.time_budget_s = float(min(max(p.time_budget_s, 5.0), 3600.0))
        p.light_protection = float(min(max(p.light_protection, 0.05), 1.0))
        return p

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    sequence: list[int]
    error: float
    error_initial: float
    line_count: int
    thread_length_mm: float
    elapsed_s: float
    engine: str
    repair_lines: int = 0
    repair_error: float = 0.0
    refined_changes: int = 0
    stop_reason: str = ""
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("sequence")
        return d


class Cancelled(Exception):
    pass


ProgressFn = Callable[[dict[str, Any]], None]


class RenderState:
    """Target, weights, coverage and the gradient image G, plus line scoring and application."""

    def __init__(self, target: np.ndarray, weight: np.ndarray, board: BoardConfig,
                 light_protection: float = 1.0):
        assert target.shape == weight.shape and target.shape[0] == target.shape[1]
        self.lam = float(min(max(light_protection, 0.05), 1.0))
        self.size = int(target.shape[0])
        self.board = board
        self.T = np.ascontiguousarray(target, dtype=np.float32)
        self.W = np.ascontiguousarray(weight, dtype=np.float32)
        self.C = np.zeros_like(self.T)
        self.N = np.zeros(self.T.shape, dtype=np.uint16)
        self.pins = board.pin_positions_px(self.size).astype(np.float32)
        pixel_mm = board.pixel_mm(self.size)
        self.a = float(min(max(board.thread_width_mm / pixel_mm * board.thread_opacity, 0.02), 0.95))
        sigma_mm = board.viewing_distance_m * board.blur_mm_per_m
        self.sigma = max(sigma_mm / pixel_mm, 0.5)
        # Energy of a blurred unit line per unit length: a 1px line blurred by sigma has a
        # Gaussian cross-section whose squared integral is 1 / (2 sigma sqrt(pi)).
        self.k = min(1.0, 1.0 / (2.0 * self.sigma * math.sqrt(math.pi)))
        self.pad = int(math.ceil(6.0 * self.sigma)) + 2
        self.Tb = self._blur(self.T)
        self.Cb = np.zeros_like(self.T)
        self.G = self._gradient(self.Tb, self.Cb, self.W)
        self.cancel_check: Callable[[], bool] | None = None

    def _gradient(self, Tb: np.ndarray, Cb: np.ndarray, W: np.ndarray) -> np.ndarray:
        """G = blur(W * R') where R' is the residual with its negative side (too dark already)
        scaled by the light-protection factor. With lam = 1 this is the plain squared error."""
        R = Tb - Cb
        if self.lam < 1.0:
            R = np.where(R >= 0, R, R * self.lam).astype(np.float32)
        return self._blur(W * R)

    # Helpers -------------------------------------------------------------------------------

    def _blur(self, img: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(img, (0, 0), self.sigma, borderType=cv2.BORDER_REFLECT_101)

    def check_cancel(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise Cancelled()

    def error(self) -> float:
        """Weighted RMS of the blurred difference, in [0, 1] (symmetric, for honest reporting)."""
        diff = self.Tb - self._blur(self.C)
        wsum = float(self.W.sum()) or 1.0
        return float(math.sqrt(float((self.W * diff * diff).sum()) / wsum))

    # Scoring -------------------------------------------------------------------------------

    def samples_from(self, i: int, js: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return sample_lines(self.pins[i], self.pins[js], self.size)

    def _score(self, idx: np.ndarray, mask: np.ndarray, removal: bool, factor: np.ndarray) -> np.ndarray:
        flatC = self.C.ravel()
        c = flatC[idx]
        a = np.minimum(self.a * factor, 0.97)[:, None]
        if removal:
            d = (1.0 - c) * (a / (1.0 - a)) * mask
            d = np.minimum(d, c)
            sign = -2.0
        else:
            d = a * (1.0 - c) * mask
            sign = 2.0
        g = self.G.ravel()[idx]
        w = self.W.ravel()[idx]
        return (sign * (d * g).sum(axis=1) - self.k * (w * d * d).sum(axis=1)).astype(np.float32)

    def gains(self, i: int, js: np.ndarray) -> np.ndarray:
        """Error reduction (positive is good) for adding each chord i -> js[k]."""
        idx, mask = self.samples_from(i, js)
        return self._score(idx, mask, False, step_factor(self.pins[i], self.pins[js]))

    def removal_gains(self, i: int, js: np.ndarray) -> np.ndarray:
        """Error reduction for removing one pass of each chord i -> js[k] (which must exist)."""
        idx, mask = self.samples_from(i, js)
        return self._score(idx, mask, True, step_factor(self.pins[i], self.pins[js]))

    def pair_gains(self, I: np.ndarray, J: np.ndarray, removal: bool = False, chunk: int = 6000) -> np.ndarray:
        """Gains for arbitrary chord pairs (I[k], J[k]), chunked to bound memory."""
        out = np.empty(len(I), dtype=np.float32)
        for s in range(0, len(I), chunk):
            e = min(s + chunk, len(I))
            idx, mask = sample_lines(self.pins[I[s:e]], self.pins[J[s:e]], self.size)
            out[s:e] = self._score(idx, mask, removal, step_factor(self.pins[I[s:e]], self.pins[J[s:e]]))
        return out

    def _line_a(self, i: int, j: int) -> float:
        return float(min(self.a * step_factor(self.pins[i], self.pins[[j]])[0], 0.97))

    # Mutation ------------------------------------------------------------------------------

    def _apply_delta(self, idx: np.ndarray, d: np.ndarray, sign: int) -> None:
        flatC = self.C.ravel()
        flatN = self.N.ravel()
        flatC[idx] = np.clip(flatC[idx] + sign * d, 0.0, 1.0)
        if sign > 0:
            flatN[idx] += 1
        else:
            flatN[idx] = np.maximum(flatN[idx].astype(np.int32) - 1, 0).astype(np.uint16)
        # Work on a crop padded twice: the outer pad keeps the blurs exact, the inner pad is
        # the region whose G actually changes.
        y0, y1, x0, x1 = bbox_of(idx, self.size, 2 * self.pad)
        delta = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
        delta[idx // self.size - y0, idx % self.size - x0] = sign * d
        self.Cb[y0:y1, x0:x1] += self._blur(delta)
        g = self._gradient(self.Tb[y0:y1, x0:x1], self.Cb[y0:y1, x0:x1], self.W[y0:y1, x0:x1])
        iy0, iy1, ix0, ix1 = bbox_of(idx, self.size, self.pad)
        self.G[iy0:iy1, ix0:ix1] = g[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]

    def add_line(self, i: int, j: int) -> None:
        idx, mask = self.samples_from(i, np.array([j]))
        idx = idx[0][mask[0]]
        a = self._line_a(i, j)
        d = a * (1.0 - self.C.ravel()[idx])
        self._apply_delta(idx, d, +1)

    def remove_line(self, i: int, j: int) -> None:
        idx, mask = self.samples_from(i, np.array([j]))
        idx = idx[0][mask[0]]
        a = self._line_a(i, j)
        c = self.C.ravel()[idx]
        d = (1.0 - c) * (a / (1.0 - a))
        d = np.minimum(d, c)
        self._apply_delta(idx, d, -1)

    # Previews --------------------------------------------------------------------------------

    def preview(self, size: int = 400) -> np.ndarray:
        """Quick grayscale preview (uint8, white board, dark thread) of the current coverage."""
        img = (1.0 - self.C) if not self.board.invert else self.C
        small = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
        return (np.clip(small, 0, 1) * 255).astype(np.uint8)


class Clock:
    def __init__(self, budget_s: float):
        self.t0 = time.perf_counter()
        self.budget = budget_s

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def expired(self) -> bool:
        return self.elapsed() > self.budget

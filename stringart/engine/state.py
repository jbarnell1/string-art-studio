"""The shared model both engines optimise against.

Two objectives share one machinery (ADR-014):

"linear" (default): light adds linearly, eyes see it nonlinearly. A pixel with thread coverage C
reflects y = Y_board + (Y_thread - Y_board) * C in linear luminance; after the viewing-distance
blur it is seen with lightness p = enc(blur(y)), enc(y) = y^(1/2.2) (close to CIE L*). The
prepared grayscale is already lightness, so the target is P_T = enc(blur(dec(gray))) and

    E = sum_x W(x) * (P_T(x) - p(x))^2.

"coverage": the v1 model, E = sum W (blur(T) - blur(C))^2 with T = 1 - gray, which treats the
photo's encoded gray as if it were coverage and so asks for about half the thread a mid-tone
really needs.

In both, the first-order change from adding per-pixel coverage d is -2 sum d * G with a gradient
image G kept current after every line, plus a second-order term sum K d^2 for the line's own
blurred energy. Coverage saturates: a pixel crossed by n threads of per-pass coverage a has
C = 1 - (1 - a)^n, so each new pass adds d = a * (1 - C). Removing one pass takes away
d = (1 - C) * a / (1 - a).

Speed (ADR-016): every per-pixel image lives in a flat buffer with one spare element at the end,
so padded sample slots point at a zero and need no mask; adding a line scores as two gathers of
precomputed products H1 = (1 - C) G and H2 = K (1 - C)^2; a line's update is done in tiles along
the line so only the band around it is blurred and re-derived, with lookup tables standing in for
the power functions.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict
from typing import Any, Callable

import cv2
import numpy as np

from stringart.board import BoardConfig
from stringart.engine.kernels import score_add
from stringart.engine.render import relative_luminance
from stringart.raster import PAD_INDEX, bbox_of, sample_lines, step_factor

cv2.setNumThreads(2)

GAMMA = 2.2
Y_FLOOR = 0.002
LUT_N = 4096


def enc(y: np.ndarray) -> np.ndarray:
    return np.power(np.maximum(y, Y_FLOOR), 1.0 / GAMMA).astype(np.float32)


def dec(p: np.ndarray) -> np.ndarray:
    return np.power(np.clip(p, 0.0, 1.0), GAMMA).astype(np.float32)


def enc_prime(y: np.ndarray) -> np.ndarray:
    yy = np.maximum(y, Y_FLOOR)
    return ((1.0 / GAMMA) * np.power(yy, 1.0 / GAMMA - 1.0)).astype(np.float32)


@dataclass
class EngineParams:
    engine: str = "a"                 # "a" greedy, "b" set + repair
    refine_seconds: float = 20.0      # engine A local search budget
    start_pin: int = 0
    batch_add: int = 24               # engine B: chords added per scoring round
    improve_rounds: int = 3           # engine B: add/remove alternations
    time_budget_s: float = 240.0      # hard stop for either engine
    light_protection: float = 1.0     # 1 = strict; below 1 over-darkening a light area costs less
    seed: int = 0
    lookahead: int = 1                # engine A: candidates looked one line ahead at each step (1 = plain greedy)
    local_search: str = "polish"      # engine A: "polish" (matrix local search), "refine" (v2 walk) or "none"
    objective: str = "linear"         # "linear" (light adds linearly, error in lightness) or "coverage" (v1)

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
        p.lookahead = int(min(max(p.lookahead, 1), 16))
        p.local_search = p.local_search if p.local_search in ("polish", "refine", "none") else "polish"
        p.objective = p.objective if p.objective in ("linear", "coverage") else "linear"
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
    regrown_lines: int = 0
    polish_rounds: int = 0
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

    def __init__(self, gray: np.ndarray, weight: np.ndarray, board: BoardConfig,
                 light_protection: float = 1.0, objective: str = "linear"):
        assert gray.shape == weight.shape and gray.shape[0] == gray.shape[1]
        self.lam = float(min(max(light_protection, 0.05), 1.0))
        self.objective = objective if objective in ("linear", "coverage") else "linear"
        self.size = n = int(gray.shape[0])
        self.board = board
        self.gray = np.ascontiguousarray(np.clip(gray, 0, 1), dtype=np.float32)
        self.W = np.ascontiguousarray(weight, dtype=np.float32)
        self.pins = board.pin_positions_px(self.size).astype(np.float32)
        pixel_mm = board.pixel_mm(self.size)
        self.a = float(min(max(board.thread_width_mm / pixel_mm * board.thread_opacity, 0.02), 0.95))
        sigma_mm = board.viewing_distance_m * board.blur_mm_per_m
        self.sigma = max(sigma_mm / pixel_mm, 0.5)
        # Energy of a blurred unit line per unit length: a 1px line blurred by sigma has a
        # Gaussian cross-section whose squared integral is 1 / (2 sigma sqrt(pi)).
        self.k = min(1.0, 1.0 / (2.0 * self.sigma * math.sqrt(math.pi)))
        # OpenCV's automatic kernel reaches 4 sigma; beyond that the blur of a delta is zero.
        self.pad = int(math.ceil(4.0 * self.sigma)) + 1
        self.tile = max(48, 5 * self.pad)
        # Board and thread luminance (linear). The thread's colour decides the sign: dark thread
        # on a light board lowers luminance, light thread on a dark board raises it.
        self.y_board = float(relative_luminance(board.board_color, 0.90))
        self.y_thread = float(relative_luminance(board.thread_color, 0.007))
        if board.invert and self.y_thread < self.y_board:
            self.y_board, self.y_thread = self.y_thread, self.y_board
        self.s = self.y_thread - self.y_board
        # Lookup tables over blurred coverage in [0, 1]: lightness and its slope per unit coverage.
        cb = np.linspace(0.0, 1.0, LUT_N + 1, dtype=np.float32)
        yb = self.y_board + self.s * cb
        self.lut_p = enc(yb)
        self.lut_slope = (enc_prime(yb) * abs(self.s)).astype(np.float32)
        if self.objective == "linear":
            self.Pt = enc(self._blur(dec(self.gray)))
        else:
            self.Pt = self._blur(self.gray if board.invert else (1.0 - self.gray))
        # Flat buffers with a spare zero at the end for padded sample slots (PAD_INDEX = n*n).
        self.Cf = np.zeros(n * n + 1, dtype=np.float32)
        self.C = self.Cf[: n * n].reshape(n, n)
        self.N = np.zeros((n, n), dtype=np.uint16)
        self.Cb = np.zeros((n, n), dtype=np.float32)
        self.Gf = np.zeros(n * n + 1, dtype=np.float32)
        self.G = self.Gf[: n * n].reshape(n, n)
        self.Kf = np.zeros(n * n + 1, dtype=np.float32)
        self.K = self.Kf[: n * n].reshape(n, n)
        self.H1f = np.zeros(n * n + 1, dtype=np.float32)   # (1 - C) * G
        self.H1 = self.H1f[: n * n].reshape(n, n)
        self.H2f = np.zeros(n * n + 1, dtype=np.float32)   # K * (1 - C)^2
        self.H2 = self.H2f[: n * n].reshape(n, n)
        g, kk = self._gradient(self.Pt, self.Cb, self.W)
        self.G[...] = g
        self.K[...] = kk
        self._refresh_products(0, n, 0, n)
        self.cancel_check: Callable[[], bool] | None = None
        # Sampled fans (every allowed chord from one pin), filled on first use. About 150 MB at
        # 800 px and 240 pins; skipped at high resolutions where it would not fit comfortably.
        self._fans: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._use_fans = n <= 1100

    # Model ---------------------------------------------------------------------------------------

    def _lightness(self, Cb: np.ndarray) -> np.ndarray:
        return enc(self.y_board + self.s * Cb)

    def _gradient(self, Pt: np.ndarray, Cb: np.ndarray, W: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """G = blur(W * R') and the second-order weights K on the same crop. R' is the signed
        residual in the direction more thread would move the picture, with the side that is
        already overshot scaled by the light-protection factor."""
        if self.objective == "linear":
            li = np.clip((Cb * LUT_N + 0.5).astype(np.int32), 0, LUT_N)
            slope = self.lut_slope[li]
            R = (Pt - self.lut_p[li]) * np.sign(self.s)   # positive where more thread helps
            if self.lam < 1.0:
                R = np.where(R >= 0, R, R * self.lam)
            G = self._blur((W * R * slope).astype(np.float32))
            K = (self.k * W * slope * slope).astype(np.float32)
        else:
            R = Pt - Cb
            if self.lam < 1.0:
                R = np.where(R >= 0, R, R * self.lam).astype(np.float32)
            G = self._blur(W * R)
            K = (self.k * W).astype(np.float32)
        return G, K

    def _refresh_products(self, y0: int, y1: int, x0: int, x1: int) -> None:
        one_minus = 1.0 - self.C[y0:y1, x0:x1]
        self.H1[y0:y1, x0:x1] = one_minus * self.G[y0:y1, x0:x1]
        self.H2[y0:y1, x0:x1] = self.K[y0:y1, x0:x1] * one_minus * one_minus

    # Helpers -------------------------------------------------------------------------------------

    def _blur(self, img: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(img, (0, 0), self.sigma, borderType=cv2.BORDER_REFLECT_101)

    def check_cancel(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise Cancelled()

    def error(self) -> float:
        """Weighted RMS of the blurred difference (symmetric, for honest reporting). In lightness
        units for the linear objective, in coverage units for the v1 objective."""
        Cb = self._blur(self.C)
        diff = self.Pt - (self._lightness(Cb) if self.objective == "linear" else Cb)
        wsum = float(self.W.sum()) or 1.0
        return float(math.sqrt(float((self.W * diff * diff).sum()) / wsum))

    # Scoring -------------------------------------------------------------------------------------

    def samples_from(self, i: int, js: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Sampled pixels of the chords i -> js, from the per-pin fan cache when it applies."""
        js = np.asarray(js)
        if not self._use_fans:
            return sample_lines(self.pins[i], self.pins[js], self.size)
        fan = self._fans.get(i)
        if fan is None:
            targets = self.board.valid_targets(i)
            idx, mask = sample_lines(self.pins[i], self.pins[targets], self.size)
            rowmap = np.full(self.board.pin_count, -1, dtype=np.int32)
            rowmap[targets] = np.arange(len(targets), dtype=np.int32)
            fan = (idx, mask, rowmap)
            self._fans[i] = fan
        idx, mask, rowmap = fan
        rows = rowmap[js]
        if len(rows) == idx.shape[0] and np.array_equal(rows, np.arange(len(rows))):
            return idx, mask
        if (rows < 0).any():
            return sample_lines(self.pins[i], self.pins[js], self.size)
        return idx[rows], mask[rows]

    def _score(self, idx: np.ndarray, mask: np.ndarray, removal: bool, factor: np.ndarray) -> np.ndarray:
        a = np.minimum(self.a * factor, 0.97)[:, None]
        if not removal:
            # d = a (1 - c): 2 sum d G - sum K d^2 = 2a sum H1 - a^2 sum H2, padded slots gather 0.
            return score_add(self.H1f, self.H2f, idx, a[:, 0])
        c = self.Cf[idx]
        d = np.minimum((1.0 - c) * (a / (1.0 - a)), c)
        g = self.Gf[idx]
        kk = self.Kf[idx]
        return (-2.0 * (d * g).sum(axis=1) - (kk * d * d).sum(axis=1)).astype(np.float32)

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

    # Mutation ------------------------------------------------------------------------------------

    def _apply_delta(self, idx: np.ndarray, d: np.ndarray, sign: int) -> None:
        """Apply per-pixel coverage changes d (idx in line order) and bring Cb, G, K, H1, H2 up
        to date in tiles along the line: first every tile's blurred delta lands in Cb, then each
        tile's G and K are re-derived on a crop padded twice (the outer pad keeps the blur exact,
        the inner pad is where G really changes)."""
        n = self.size
        self.Cf[idx] = np.clip(self.Cf[idx] + sign * d, 0.0, 1.0)
        flatN = self.N.ravel()
        if sign > 0:
            flatN[idx] += 1
        else:
            flatN[idx] = np.maximum(flatN[idx].astype(np.int32) - 1, 0).astype(np.uint16)
        pad = self.pad
        tiles = [(idx[s:s + self.tile], d[s:s + self.tile]) for s in range(0, len(idx), self.tile)]
        boxes = []
        for t_idx, t_d in tiles:
            y0, y1, x0, x1 = bbox_of(t_idx, n, pad)
            delta = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
            delta[t_idx // n - y0, t_idx % n - x0] = sign * t_d
            self.Cb[y0:y1, x0:x1] += self._blur(delta)
            boxes.append((y0, y1, x0, x1))
        for (iy0, iy1, ix0, ix1), (t_idx, _) in zip(boxes, tiles):
            y0, y1, x0, x1 = bbox_of(t_idx, n, 2 * pad)
            g, kk = self._gradient(self.Pt[y0:y1, x0:x1], self.Cb[y0:y1, x0:x1], self.W[y0:y1, x0:x1])
            self.G[iy0:iy1, ix0:ix1] = g[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
            self.K[iy0:iy1, ix0:ix1] = kk[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
            self._refresh_products(iy0, iy1, ix0, ix1)

    def add_line(self, i: int, j: int) -> None:
        idx, mask = self.samples_from(i, np.array([j]))
        idx = idx[0][mask[0]]
        a = self._line_a(i, j)
        d = a * (1.0 - self.Cf[idx])
        self._apply_delta(idx, d, +1)

    def remove_line(self, i: int, j: int) -> None:
        idx, mask = self.samples_from(i, np.array([j]))
        idx = idx[0][mask[0]]
        a = self._line_a(i, j)
        c = self.Cf[idx]
        d = (1.0 - c) * (a / (1.0 - a))
        d = np.minimum(d, c)
        self._apply_delta(idx, d, -1)

    # Previews ------------------------------------------------------------------------------------

    def preview(self, size: int = 400) -> np.ndarray:
        """Quick grayscale preview (uint8) of the current coverage as it would look: linear
        light under the thread, encoded for the screen."""
        small = cv2.resize(self.C, (size, size), interpolation=cv2.INTER_AREA)
        img = enc(self.y_board + self.s * np.clip(small, 0, 1))
        return (np.clip(img, 0, 1) * 255).astype(np.uint8)


class Clock:
    def __init__(self, budget_s: float):
        self.t0 = time.perf_counter()
        self.budget = budget_s

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def expired(self) -> bool:
        return self.elapsed() > self.budget

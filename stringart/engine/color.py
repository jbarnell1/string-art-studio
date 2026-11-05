"""Colour: several threads, each one continuous, composited in build order (ADR-017).

Each colour k has its own saturating coverage layer C_k. Layers are laid in a fixed build order
(lightest first, black last on a light board), so a pixel reflects, in linear RGB,

    y = rho_board * prod_k (1 - C_k) + sum_k rho_k * C_k * prod_{m > k} (1 - C_m)

(what is above covers what is below in proportion to its coverage). After the viewing-distance
blur the picture is compared with the photo in CIELAB (scaled by 1/100), with importance weights:

    E = sum_x W(x) * || Lab(blur(y)) - Lab_T ||^2

Adding coverage d of colour k changes y by (rho_k - below_k) * above_k per unit d, where below_k
is the composite under layer k and above_k the transmission of the layers over it, and the chain
rule through the Lab Jacobian gives one gradient image per colour. The same first-order plus
self-energy scoring, tiled band updates and per-pin fan cache as the mono engine apply; the
greedy walk keeps one current pin per colour and takes the best (colour, line) at every step so
all colours share the line budget. Layers are blurred before compositing, which treats threads
of different colours as overlapping at random within the blur radius.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import cv2
import numpy as np

from stringart.board import BoardConfig
from stringart.engine.polish import polish
from stringart.engine.render import hex_to_rgb, srgb_to_linear
from stringart.engine.state import Cancelled, Clock, EngineParams, ProgressFn, RunResult
from stringart.raster import bbox_of, sample_lines, step_factor

# sRGB (D65) linear RGB to XYZ, and the white point.
M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                      [0.2126729, 0.7151522, 0.0721750],
                      [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
T_FLOOR = 1e-4


def palette_order(colors: list[str], invert: bool) -> list[str]:
    """Build order: lightest first and the darkest last on a light board (the dark thread must
    sit on top to stay dark), the reverse on a dark board."""
    def lum(h: str) -> float:
        rgb = srgb_to_linear(np.array(hex_to_rgb(h, (0, 0, 0)), dtype=np.float32) / 255.0)
        return float(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])
    return sorted(colors, key=lum, reverse=not invert)


def rgb_to_lab(lin_rgb: np.ndarray) -> np.ndarray:
    """Linear RGB (..., 3) to Lab / 100 (..., 3)."""
    xyz = lin_rgb @ M_RGB2XYZ.T / WHITE
    f = np.cbrt(np.maximum(xyz, T_FLOOR))
    L = 1.16 * f[..., 1] - 0.16
    a = 5.0 * (f[..., 0] - f[..., 1])
    b = 2.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1).astype(np.float32)


M_NORM = (M_RGB2XYZ / WHITE[:, None]).astype(np.float32)   # rgb -> xyz / white


def _lab_and_jacobian(lin_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lab/100 (..., 3) and d(Lab/100)/d(linear rgb) (..., 3, 3), written out so a crop costs a
    handful of elementwise passes rather than stacked matrix products."""
    xyz = lin_rgb @ M_NORM.T
    f = np.cbrt(np.maximum(xyz, T_FLOOR))
    fp = (1.0 / 3.0) / (f * f)                      # d cbrt / dt
    lab = np.empty(lin_rgb.shape, dtype=np.float32)
    lab[..., 0] = 1.16 * f[..., 1] - 0.16
    lab[..., 1] = 5.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 2.0 * (f[..., 1] - f[..., 2])
    # Rows of d lab / d (xyz/white) are sparse: L depends on y, a on x and y, b on y and z.
    fx, fy, fz = fp[..., 0, None], fp[..., 1, None], fp[..., 2, None]
    J = np.empty(lin_rgb.shape + (3,), dtype=np.float32)
    J[..., 0, :] = 1.16 * fy * M_NORM[1]
    J[..., 1, :] = 5.0 * fx * M_NORM[0] - 5.0 * fy * M_NORM[1]
    J[..., 2, :] = 2.0 * fy * M_NORM[1] - 2.0 * fz * M_NORM[2]
    return lab, J


class LayerView:
    """The mono scoring interface (gains, add_line, ...) bound to one colour layer, so the
    local search and the walk code can treat a layer like a single-thread state."""

    def __init__(self, state: "ColorState", layer: int):
        self.state = state
        self.layer = layer
        self.board = state.board
        self.W = state.W
        self.pins = state.pins
        self.size = state.size

    def gains(self, i: int, js: np.ndarray) -> np.ndarray:
        return self.state.gains(self.layer, i, js)

    def removal_gains(self, i: int, js: np.ndarray) -> np.ndarray:
        return self.state.removal_gains(self.layer, i, js)

    def pair_gains(self, I: np.ndarray, J: np.ndarray, removal: bool = False, chunk: int = 6000) -> np.ndarray:
        return self.state.pair_gains(self.layer, I, J, removal, chunk)

    def add_line(self, i: int, j: int) -> None:
        self.state.add_line(self.layer, i, j)

    def remove_line(self, i: int, j: int) -> None:
        self.state.remove_line(self.layer, i, j)

    def check_cancel(self) -> None:
        self.state.check_cancel()

    def preview(self, size: int = 400) -> np.ndarray:
        return self.state.preview(size)

    def error(self) -> float:
        return self.state.error()


class ColorState:
    def __init__(self, target_rgb: np.ndarray, weight: np.ndarray, board: BoardConfig, colors: list[str]):
        """target_rgb: framed photo as sRGB floats (n, n, 3) in [0, 1] after the tone pipeline.
        colors: thread hex colours already in build order (first laid first)."""
        assert target_rgb.shape[:2] == weight.shape and weight.shape[0] == weight.shape[1]
        self.size = n = int(weight.shape[0])
        self.board = board
        self.colors = list(colors)
        self.K = len(self.colors)
        self.W = np.ascontiguousarray(weight, dtype=np.float32)
        self.pins = board.pin_positions_px(n).astype(np.float32)
        pixel_mm = board.pixel_mm(n)
        self.a = float(min(max(board.thread_width_mm / pixel_mm * board.thread_opacity, 0.02), 0.95))
        sigma_mm = board.viewing_distance_m * board.blur_mm_per_m
        self.sigma = max(sigma_mm / pixel_mm, 0.5)
        self.k = min(1.0, 1.0 / (2.0 * self.sigma * math.sqrt(math.pi)))
        self.pad = int(math.ceil(4.0 * self.sigma)) + 1
        # Colour tiles are longer than the mono engine's: each tile costs K blurs plus the Lab
        # Jacobian, so fewer, larger tiles win.
        self.tile = max(64, 5 * self.pad)
        self.rho_board = srgb_to_linear(np.array(hex_to_rgb(board.board_color, (246, 243, 238)), dtype=np.float32) / 255.0)
        self.rho = np.stack([srgb_to_linear(np.array(hex_to_rgb(c, (22, 22, 22)), dtype=np.float32) / 255.0) for c in self.colors])
        lin_t = srgb_to_linear(np.clip(target_rgb, 0, 1).astype(np.float32))
        self.lab_t = rgb_to_lab(self._blur3(lin_t))
        # Per-layer flat buffers with a spare zero for padded sample slots.
        self.Cf = np.zeros((self.K, n * n + 1), dtype=np.float32)
        self.C = self.Cf[:, : n * n].reshape(self.K, n, n)
        self.Cb = np.zeros((self.K, n, n), dtype=np.float32)
        self.Gf = np.zeros((self.K, n * n + 1), dtype=np.float32)
        self.G = self.Gf[:, : n * n].reshape(self.K, n, n)
        self.Kf = np.zeros((self.K, n * n + 1), dtype=np.float32)
        self.Kk = self.Kf[:, : n * n].reshape(self.K, n, n)
        self.H1f = np.zeros((self.K, n * n + 1), dtype=np.float32)
        self.H1 = self.H1f[:, : n * n].reshape(self.K, n, n)
        self.H2f = np.zeros((self.K, n * n + 1), dtype=np.float32)
        self.H2 = self.H2f[:, : n * n].reshape(self.K, n, n)
        g, kk = self._gradient(self.Cb, self.W, self.lab_t)
        self.G[...] = g
        self.Kk[...] = kk
        self._refresh_products(0, n, 0, n)
        self.cancel_check: Callable[[], bool] | None = None
        self._fans: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._use_fans = n <= 1100
        self.views = [LayerView(self, k) for k in range(self.K)]

    # Model ---------------------------------------------------------------------------------------

    def _blur(self, img: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(img, (0, 0), self.sigma, borderType=cv2.BORDER_REFLECT_101)

    def _blur3(self, img: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(img, (0, 0), self.sigma, borderType=cv2.BORDER_REFLECT_101)

    def _composite(self, Cb: np.ndarray) -> tuple[np.ndarray, list[np.ndarray], np.ndarray]:
        """Linear RGB (h, w, 3) from blurred layers (K, h, w), plus below_k (list of (h, w, 3))
        and above (K, h, w): the transmission of the layers over each one."""
        below: list[np.ndarray] = []
        y = np.empty(Cb.shape[1:] + (3,), dtype=np.float32)
        y[...] = self.rho_board
        for k in range(self.K):
            below.append(y)
            c = Cb[k][..., None]
            y = y + c * (self.rho[k] - y)
        # above[k] = prod_{m > k} (1 - Cb[m]): a reversed cumulative product shifted by one.
        trans = np.cumprod((1.0 - Cb)[::-1], axis=0)[::-1]
        above = np.ones_like(Cb)
        if self.K > 1:
            above[:-1] = trans[1:]
        return y, below, above

    def _gradient(self, Cb: np.ndarray, W: np.ndarray, lab_t: np.ndarray,
                  inner: tuple[slice, slice] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """G (blurred, needs the whole crop) and K (pointwise, so only the `inner` part of the
        crop when given) for every layer."""
        y, below, above = self._composite(Cb)
        lab, J = _lab_and_jacobian(y)
        r = lab - lab_t                                          # (h, w, 3)
        # d E / d y per pixel: J^T r, written as three multiply-adds over the rows of J.
        JTr = J[..., 0, :] * r[..., 0, None] + J[..., 1, :] * r[..., 1, None] + J[..., 2, :] * r[..., 2, None]
        sl = inner if inner is not None else (slice(None), slice(None))
        G = np.empty((self.K,) + W.shape, dtype=np.float32)
        Kk = np.empty((self.K,) + W[sl].shape, dtype=np.float32)
        kW = self.k * W[sl]
        Ji = J[sl]
        for k in range(self.K):
            dydc = (self.rho[k] - below[k]) * above[k][..., None]    # (h, w, 3)
            integrand = -(W * (JTr * dydc).sum(axis=-1))
            G[k] = self._blur(integrand.astype(np.float32))
            di = dydc[sl]
            Jd = Ji[..., :, 0] * di[..., 0, None] + Ji[..., :, 1] * di[..., 1, None] + Ji[..., :, 2] * di[..., 2, None]
            Kk[k] = kW * (Jd * Jd).sum(axis=-1)
        return G, Kk

    def _refresh_products(self, y0: int, y1: int, x0: int, x1: int) -> None:
        one_minus = 1.0 - self.C[:, y0:y1, x0:x1]
        self.H1[:, y0:y1, x0:x1] = one_minus * self.G[:, y0:y1, x0:x1]
        self.H2[:, y0:y1, x0:x1] = self.Kk[:, y0:y1, x0:x1] * one_minus * one_minus

    def check_cancel(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise Cancelled()

    def composite_rgb(self, size: int | None = None) -> np.ndarray:
        """Linear RGB of the current (unblurred) coverage, optionally resized."""
        C = self.C
        if size is not None and size != self.size:
            C = np.stack([cv2.resize(c, (size, size), interpolation=cv2.INTER_AREA) for c in C])
        y, _, _ = self._composite(C)
        return y

    def error(self) -> float:
        Cb = np.stack([self._blur(c) for c in self.C])
        y, _, _ = self._composite(Cb)
        d = rgb_to_lab(y) - self.lab_t
        wsum = float(self.W.sum()) or 1.0
        return float(math.sqrt(float((self.W * (d * d).sum(axis=-1)).sum()) / wsum))

    # Scoring -------------------------------------------------------------------------------------

    def samples_from(self, i: int, js: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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

    def _score(self, layer: int, idx: np.ndarray, removal: bool, factor: np.ndarray) -> np.ndarray:
        a = np.minimum(self.a * factor, 0.97)
        if not removal:
            return (2.0 * a * self.H1f[layer][idx].sum(axis=1) - a * a * self.H2f[layer][idx].sum(axis=1)).astype(np.float32)
        c = self.Cf[layer][idx]
        aa = a[:, None]
        d = np.minimum((1.0 - c) * (aa / (1.0 - aa)), c)
        return (-2.0 * (d * self.Gf[layer][idx]).sum(axis=1) - (self.Kf[layer][idx] * d * d).sum(axis=1)).astype(np.float32)

    def gains(self, layer: int, i: int, js: np.ndarray) -> np.ndarray:
        idx, _ = self.samples_from(i, js)
        return self._score(layer, idx, False, step_factor(self.pins[i], self.pins[js]))

    def removal_gains(self, layer: int, i: int, js: np.ndarray) -> np.ndarray:
        idx, _ = self.samples_from(i, js)
        return self._score(layer, idx, True, step_factor(self.pins[i], self.pins[js]))

    def pair_gains(self, layer: int, I: np.ndarray, J: np.ndarray, removal: bool = False, chunk: int = 6000) -> np.ndarray:
        out = np.empty(len(I), dtype=np.float32)
        for s in range(0, len(I), chunk):
            e = min(s + chunk, len(I))
            idx, _ = sample_lines(self.pins[I[s:e]], self.pins[J[s:e]], self.size)
            out[s:e] = self._score(layer, idx, removal, step_factor(self.pins[I[s:e]], self.pins[J[s:e]]))
        return out

    def _line_a(self, i: int, j: int) -> float:
        return float(min(self.a * step_factor(self.pins[i], self.pins[[j]])[0], 0.97))

    # Mutation ------------------------------------------------------------------------------------

    def _apply_delta(self, layer: int, idx: np.ndarray, d: np.ndarray, sign: int) -> None:
        n = self.size
        self.Cf[layer][idx] = np.clip(self.Cf[layer][idx] + sign * d, 0.0, 1.0)
        pad = self.pad
        tiles = [(idx[s:s + self.tile], d[s:s + self.tile]) for s in range(0, len(idx), self.tile)]
        boxes = []
        for t_idx, t_d in tiles:
            y0, y1, x0, x1 = bbox_of(t_idx, n, pad)
            delta = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
            delta[t_idx // n - y0, t_idx % n - x0] = sign * t_d
            self.Cb[layer, y0:y1, x0:x1] += self._blur(delta)
            boxes.append((y0, y1, x0, x1))
        for (iy0, iy1, ix0, ix1), (t_idx, _) in zip(boxes, tiles):
            y0, y1, x0, x1 = bbox_of(t_idx, n, 2 * pad)
            inner = (slice(iy0 - y0, iy1 - y0), slice(ix0 - x0, ix1 - x0))
            g, kk = self._gradient(self.Cb[:, y0:y1, x0:x1], self.W[y0:y1, x0:x1], self.lab_t[y0:y1, x0:x1], inner)
            self.G[:, iy0:iy1, ix0:ix1] = g[:, inner[0], inner[1]]
            self.Kk[:, iy0:iy1, ix0:ix1] = kk
            self._refresh_products(iy0, iy1, ix0, ix1)

    def add_line(self, layer: int, i: int, j: int) -> None:
        idx, mask = self.samples_from(i, np.array([j]))
        idx = idx[0][mask[0]]
        d = self._line_a(i, j) * (1.0 - self.Cf[layer][idx])
        self._apply_delta(layer, idx, d, +1)

    def remove_line(self, layer: int, i: int, j: int) -> None:
        idx, mask = self.samples_from(i, np.array([j]))
        idx = idx[0][mask[0]]
        a = self._line_a(i, j)
        c = self.Cf[layer][idx]
        d = np.minimum((1.0 - c) * (a / (1.0 - a)), c)
        self._apply_delta(layer, idx, d, -1)

    # Previews ------------------------------------------------------------------------------------

    def preview(self, size: int = 400) -> np.ndarray:
        """RGB uint8 picture of the current coverage, linear light encoded for the screen."""
        from stringart.engine.render import linear_to_srgb
        y = self.composite_rgb(size)
        return (linear_to_srgb(y) * 255.0 + 0.5).astype(np.uint8)


# Engine ------------------------------------------------------------------------------------------

PREVIEW_EVERY = 40


def run_color_greedy(state: ColorState, params: EngineParams, progress: ProgressFn | None = None) -> tuple[list[list[int]], RunResult]:
    """Greedy over (colour, line) pairs with one continuous thread per colour, then per-layer
    local search and regrowth. Returns the per-layer sequences (build order) and the result."""
    board = state.board
    clock = Clock(params.time_budget_s)
    error0 = state.error()
    K = state.K
    seqs: list[list[int]] = [[int(params.start_pin) % board.pin_count] for _ in range(K)]
    eps = 1e-4 * float(state.W.mean()) * state.size
    stop = "no improving line"
    cancelled = False
    changes = 0
    regrown = 0
    rounds = 0
    stuck = [False] * K
    bridges = [0]
    max_bridges = max(10, board.max_lines // 20)

    def total_lines() -> int:
        return sum(len(s) - 1 for s in seqs)

    def emit(phase: str) -> None:
        if progress:
            progress({"phase": phase, "lines": total_lines(), "max_lines": board.max_lines,
                      "elapsed_s": clock.elapsed(), "preview": state.preview()})

    def walk(phase: str) -> str:
        step = 0
        while total_lines() < board.max_lines:
            best = (eps, -1, -1)
            for k in range(K):
                if stuck[k]:
                    continue
                cur = seqs[k][-1]
                js = board.valid_targets(cur)
                g = state.gains(k, cur, js)
                b = int(np.argmax(g))
                if g[b] > best[0]:
                    best = (float(g[b]), k, int(js[b]))
            if best[1] < 0:
                # Nothing single helps anywhere: try one bridge for the colour with the best pair.
                if bridges[0] >= max_bridges:
                    return "stuck at pin"
                bk, bj, bt = -1, -1, eps
                for k in range(K):
                    if stuck[k]:
                        continue
                    cur = seqs[k][-1]
                    js = board.valid_targets(cur)
                    g = state.gains(k, cur, js)
                    for idx in np.argsort(-g)[:12]:
                        j = int(js[idx])
                        g2 = state.gains(k, j, board.valid_targets(j))
                        t = float(g[idx]) + float(g2.max())
                        if t > bt:
                            bt, bk, bj = t, k, j
                if bk < 0:
                    return "no improving line"
                bridges[0] += 1
                best = (bt, bk, bj)
            _, k, j = best
            state.add_line(k, seqs[k][-1], j)
            seqs[k].append(j)
            step += 1
            if progress and step % PREVIEW_EVERY == 0:
                emit(phase)
            if step % 16 == 0:
                state.check_cancel()
                if clock.expired():
                    return "time budget"
        return "max lines"

    try:
        # Each colour starts where its best first line is: a thread that must begin at pin 0
        # when nothing it is good for lies on pin 0's chords would otherwise never get going.
        for k in range(K):
            best_pin, best_gain = seqs[k][0], -np.inf
            for i in range(board.pin_count):
                g = float(state.gains(k, i, board.valid_targets(i)).max())
                if g > best_gain:
                    best_gain, best_pin = g, i
            seqs[k] = [int(best_pin)]
        state.check_cancel()
        stop = walk("greedy")
        if params.refine_seconds > 0 and not clock.expired() and params.local_search != "none":
            search = Clock(min(params.refine_seconds, params.time_budget_s - clock.elapsed()))
            while not search.expired() and not clock.expired():
                applied = 0
                for k in range(K):
                    if len(seqs[k]) < 3 or search.expired():
                        continue
                    share = max(1.0, (search.budget - search.elapsed()) / max(1, K - k))
                    totals = polish(state.views[k], seqs[k], share, eps, None)
                    rounds += totals["rounds"]
                    applied += totals["replace"] + totals["insert"] + totals["delete"] + totals["unfold"]
                emit("polish")
                changes += applied
                grown = 0
                if total_lines() < board.max_lines and stop != "stuck at pin" and not clock.expired():
                    before = total_lines()
                    stop = walk("regrow")
                    grown = total_lines() - before
                    regrown += grown
                if applied == 0 and grown == 0:
                    break
    except Cancelled:
        cancelled = True
        stop = "cancelled"
    flat = [p for s in seqs for p in s]
    result = RunResult(
        sequence=flat, error=state.error(), error_initial=error0, line_count=total_lines(),
        thread_length_mm=sum(board.sequence_length_mm(s) for s in seqs), elapsed_s=clock.elapsed(),
        engine="a", refined_changes=changes, regrown_lines=regrown, polish_rounds=rounds,
        stop_reason=stop, cancelled=cancelled,
    )
    return seqs, result


# Palettes ----------------------------------------------------------------------------------------

NAMED_COLORS = [
    ("black", "#161616"), ("white", "#f3f0ea"), ("grey", "#808080"), ("dark grey", "#404040"),
    ("light grey", "#c0c0c0"), ("red", "#d9261c"), ("dark red", "#7a1410"), ("orange", "#f07f1a"),
    ("yellow", "#f2d21c"), ("gold", "#c9a227"), ("green", "#2e8b3a"), ("dark green", "#1f4d2a"),
    ("lime", "#9acd32"), ("teal", "#1c8a8a"), ("cyan", "#00aeef"), ("blue", "#2457c5"),
    ("navy", "#1b2a5c"), ("purple", "#6e3fa3"), ("magenta", "#ec008c"), ("pink", "#f4a3c0"),
    ("brown", "#6b4423"), ("tan", "#c9a27e"), ("beige", "#e6d3b3"), ("peach", "#f6c9a8"),
    ("olive", "#6b6b2a"), ("cream", "#f4ecd8"),
]

PALETTE_PRESETS = {
    "single": [],
    "cmyk": ["#00aeef", "#ec008c", "#fff200", "#161616"],
    "warm": ["#f6c9a8", "#c9733a", "#6b4423", "#161616"],
    "earth": ["#e6d3b3", "#c9a27e", "#6b4423", "#1b2a5c", "#161616"],
    "primary": ["#f2d21c", "#d9261c", "#2457c5", "#161616"],
}


def color_name(hex_color: str) -> str:
    """Nearest everyday name for a thread colour, by Lab distance."""
    target = rgb_to_lab(srgb_to_linear(np.array(hex_to_rgb(hex_color, (0, 0, 0)), dtype=np.float32) / 255.0)[None, None])[0, 0]
    best, best_d = "thread", np.inf
    for name, h in NAMED_COLORS:
        lab = rgb_to_lab(srgb_to_linear(np.array(hex_to_rgb(h, (0, 0, 0)), dtype=np.float32) / 255.0)[None, None])[0, 0]
        d = float(((lab - target) ** 2).sum())
        if d < best_d:
            best, best_d = name, d
    return best


def auto_palette(target_rgb: np.ndarray, k: int, weight: np.ndarray | None = None, seed: int = 0,
                 board_color: str | None = None) -> list[str]:
    """k thread colours from the framed photo by k-means in Lab over the board's pixels. The
    darkest centre becomes black so the picture always has its darks; the rest keep the photo's
    own (often muted) colours, which is what matching thread looks like."""
    k = int(min(max(k, 2), 6))
    img = np.clip(target_rgb, 0, 1).astype(np.float32)
    n = img.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    r = n / 2.0
    inside = ((xx - r + 0.5) ** 2 + (yy - r + 0.5) ** 2) < (0.95 * r) ** 2
    if weight is not None:
        inside &= weight > 0.05
    px = img[inside]
    rng = np.random.default_rng(seed)
    if len(px) > 20000:
        px = px[rng.choice(len(px), 20000, replace=False)]
    lab = rgb_to_lab(srgb_to_linear(px)[:, None, :])[:, 0, :]
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-4)
    _, _labels, centres = cv2.kmeans(np.ascontiguousarray(lab, dtype=np.float32), k, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
    out = []
    for c in centres:
        # Lab/100 back to sRGB hex through XYZ.
        L, a, b = (float(v) for v in c)
        fy = (L + 0.16) / 1.16
        fx = fy + a / 5.0
        fz = fy - b / 2.0
        xyz = np.array([fx ** 3, fy ** 3, fz ** 3], dtype=np.float32) * WHITE
        lin = np.linalg.solve(M_RGB2XYZ, xyz)
        from stringart.engine.render import linear_to_srgb
        srgb = np.clip(linear_to_srgb(np.clip(lin, 0, 1)), 0, 1)
        out.append("#%02x%02x%02x" % tuple(int(round(v * 255)) for v in srgb))
    out = palette_order(out, False)            # lightest first, darkest last
    out[-1] = "#161616"
    board_lab = rgb_to_lab(srgb_to_linear(np.array(hex_to_rgb(board_color or "#f6f3ee", (246, 243, 238)), dtype=np.float32) / 255.0)[None, None])[0, 0]
    dedup: list[str] = []
    for h in out:
        lab = rgb_to_lab(srgb_to_linear(np.array(hex_to_rgb(h, (0, 0, 0)), dtype=np.float32) / 255.0)[None, None])[0, 0]
        # A thread the colour of the board does nothing; skip it.
        if float(((lab - board_lab) ** 2).sum()) < 0.012 and len(out) > 2:
            continue
        if h not in dedup:
            dedup.append(h)
    return dedup

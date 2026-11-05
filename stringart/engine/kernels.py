"""Fused inner loops (ADR-018). Numba compiles them on first use and caches the result; without
Numba the same functions run as plain numpy, slower but identical in result.

Two kernels carry most of the engines' time:
  score_add      gains of many candidate lines from the precomputed products H1 and H2
  color_terms    per-pixel colour model on a crop: composite, Lab, Jacobian, one gradient
                 integrand and one second-order weight per layer
"""
from __future__ import annotations

import os

import numpy as np

try:  # pragma: no cover - exercised through the engines
    import numba
    from numba import njit, prange

    HAVE_NUMBA = True
    if "NUMBA_NUM_THREADS" not in os.environ:
        try:
            numba.set_num_threads(max(1, min(4, numba.config.NUMBA_NUM_THREADS)))
        except Exception:  # noqa: BLE001
            pass
except Exception:  # noqa: BLE001
    HAVE_NUMBA = False

T_FLOOR = 1e-4


# ---------------------------------------------------------------------------------------------
# Line scoring
# ---------------------------------------------------------------------------------------------

def _score_add_numpy(H1f: np.ndarray, H2f: np.ndarray, idx: np.ndarray, a: np.ndarray) -> np.ndarray:
    s1 = H1f[idx].sum(axis=1)
    s2 = H2f[idx].sum(axis=1)
    return (2.0 * a * s1 - a * a * s2).astype(np.float32)


if HAVE_NUMBA:
    @njit(cache=True, parallel=True, fastmath=True)
    def _score_add_nb(H1f, H2f, idx, a):
        m, L = idx.shape
        out = np.empty(m, dtype=np.float32)
        for i in prange(m):
            s1 = 0.0
            s2 = 0.0
            for j in range(L):
                p = idx[i, j]
                s1 += H1f[p]
                s2 += H2f[p]
            out[i] = 2.0 * a[i] * s1 - a[i] * a[i] * s2
        return out

    def score_add(H1f: np.ndarray, H2f: np.ndarray, idx: np.ndarray, a: np.ndarray) -> np.ndarray:
        return _score_add_nb(H1f, H2f, np.ascontiguousarray(idx), np.ascontiguousarray(a, dtype=np.float32))
else:
    score_add = _score_add_numpy


# ---------------------------------------------------------------------------------------------
# Colour model on a crop
# ---------------------------------------------------------------------------------------------

def _color_terms_numpy(Cb: np.ndarray, W: np.ndarray, lab_t: np.ndarray, rho: np.ndarray, rho_board: np.ndarray,
                       m_norm: np.ndarray, k_blur: float) -> tuple[np.ndarray, np.ndarray]:
    K = Cb.shape[0]
    below = []
    y = np.empty(Cb.shape[1:] + (3,), dtype=np.float32)
    y[...] = rho_board
    for k in range(K):
        below.append(y)
        c = Cb[k][..., None]
        y = y + c * (rho[k] - y)
    trans = np.cumprod((1.0 - Cb)[::-1], axis=0)[::-1]
    above = np.ones_like(Cb)
    if K > 1:
        above[:-1] = trans[1:]
    xyz = y @ m_norm.T
    f = np.cbrt(np.maximum(xyz, T_FLOOR))
    fp = (1.0 / 3.0) / (f * f)
    lab = np.empty_like(y)
    lab[..., 0] = 1.16 * f[..., 1] - 0.16
    lab[..., 1] = 5.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 2.0 * (f[..., 1] - f[..., 2])
    fx, fy, fz = fp[..., 0, None], fp[..., 1, None], fp[..., 2, None]
    J = np.empty(y.shape + (3,), dtype=np.float32)
    J[..., 0, :] = 1.16 * fy * m_norm[1]
    J[..., 1, :] = 5.0 * fx * m_norm[0] - 5.0 * fy * m_norm[1]
    J[..., 2, :] = 2.0 * fy * m_norm[1] - 2.0 * fz * m_norm[2]
    r = lab - lab_t
    JTr = J[..., 0, :] * r[..., 0, None] + J[..., 1, :] * r[..., 1, None] + J[..., 2, :] * r[..., 2, None]
    integrand = np.empty((K,) + W.shape, dtype=np.float32)
    Kk = np.empty_like(integrand)
    kW = k_blur * W
    for k in range(K):
        dydc = (rho[k] - below[k]) * above[k][..., None]
        integrand[k] = -(W * (JTr * dydc).sum(axis=-1))
        Jd = J[..., :, 0] * dydc[..., 0, None] + J[..., :, 1] * dydc[..., 1, None] + J[..., :, 2] * dydc[..., 2, None]
        Kk[k] = kW * (Jd * Jd).sum(axis=-1)
    return integrand, Kk


if HAVE_NUMBA:
    @njit(cache=True, parallel=True, fastmath=True)
    def _color_terms_nb(Cb, W, lab_t, rho, rho_board, m_norm, k_blur):
        K, h, w = Cb.shape
        integrand = np.empty((K, h, w), dtype=np.float32)
        Kk = np.empty((K, h, w), dtype=np.float32)
        for yy in prange(h):
            below = np.empty((K, 3), dtype=np.float32)
            above = np.empty(K, dtype=np.float32)
            dydc = np.empty(3, dtype=np.float32)
            Jd = np.empty(3, dtype=np.float32)
            for xx in range(w):
                # Composite in build order, remembering what lies under each layer.
                y0 = rho_board[0]
                y1 = rho_board[1]
                y2 = rho_board[2]
                for k in range(K):
                    below[k, 0] = y0
                    below[k, 1] = y1
                    below[k, 2] = y2
                    c = Cb[k, yy, xx]
                    y0 = y0 + c * (rho[k, 0] - y0)
                    y1 = y1 + c * (rho[k, 1] - y1)
                    y2 = y2 + c * (rho[k, 2] - y2)
                t = np.float32(1.0)
                for k in range(K - 1, -1, -1):
                    above[k] = t
                    t = t * (1.0 - Cb[k, yy, xx])
                # Lab and its Jacobian with respect to linear rgb.
                X = m_norm[0, 0] * y0 + m_norm[0, 1] * y1 + m_norm[0, 2] * y2
                Y = m_norm[1, 0] * y0 + m_norm[1, 1] * y1 + m_norm[1, 2] * y2
                Z = m_norm[2, 0] * y0 + m_norm[2, 1] * y1 + m_norm[2, 2] * y2
                if X < T_FLOOR:
                    X = T_FLOOR
                if Y < T_FLOOR:
                    Y = T_FLOOR
                if Z < T_FLOOR:
                    Z = T_FLOOR
                fX = X ** (1.0 / 3.0)
                fY = Y ** (1.0 / 3.0)
                fZ = Z ** (1.0 / 3.0)
                px = (1.0 / 3.0) / (fX * fX)
                py = (1.0 / 3.0) / (fY * fY)
                pz = (1.0 / 3.0) / (fZ * fZ)
                rL = (1.16 * fY - 0.16) - lab_t[yy, xx, 0]
                ra = 5.0 * (fX - fY) - lab_t[yy, xx, 1]
                rb = 2.0 * (fY - fZ) - lab_t[yy, xx, 2]
                # Rows of d lab / d rgb.
                j0r = 1.16 * py * m_norm[1, 0]
                j0g = 1.16 * py * m_norm[1, 1]
                j0b = 1.16 * py * m_norm[1, 2]
                j1r = 5.0 * (px * m_norm[0, 0] - py * m_norm[1, 0])
                j1g = 5.0 * (px * m_norm[0, 1] - py * m_norm[1, 1])
                j1b = 5.0 * (px * m_norm[0, 2] - py * m_norm[1, 2])
                j2r = 2.0 * (py * m_norm[1, 0] - pz * m_norm[2, 0])
                j2g = 2.0 * (py * m_norm[1, 1] - pz * m_norm[2, 1])
                j2b = 2.0 * (py * m_norm[1, 2] - pz * m_norm[2, 2])
                # J^T r
                gr = j0r * rL + j1r * ra + j2r * rb
                gg = j0g * rL + j1g * ra + j2g * rb
                gb = j0b * rL + j1b * ra + j2b * rb
                wgt = W[yy, xx]
                for k in range(K):
                    ak = above[k]
                    dydc[0] = (rho[k, 0] - below[k, 0]) * ak
                    dydc[1] = (rho[k, 1] - below[k, 1]) * ak
                    dydc[2] = (rho[k, 2] - below[k, 2]) * ak
                    integrand[k, yy, xx] = -(wgt * (gr * dydc[0] + gg * dydc[1] + gb * dydc[2]))
                    Jd[0] = j0r * dydc[0] + j0g * dydc[1] + j0b * dydc[2]
                    Jd[1] = j1r * dydc[0] + j1g * dydc[1] + j1b * dydc[2]
                    Jd[2] = j2r * dydc[0] + j2g * dydc[1] + j2b * dydc[2]
                    Kk[k, yy, xx] = k_blur * wgt * (Jd[0] * Jd[0] + Jd[1] * Jd[1] + Jd[2] * Jd[2])
        return integrand, Kk

    def color_terms(Cb, W, lab_t, rho, rho_board, m_norm, k_blur):
        return _color_terms_nb(np.ascontiguousarray(Cb, dtype=np.float32), np.ascontiguousarray(W, dtype=np.float32),
                               np.ascontiguousarray(lab_t, dtype=np.float32), np.ascontiguousarray(rho, dtype=np.float32),
                               np.ascontiguousarray(rho_board, dtype=np.float32), np.ascontiguousarray(m_norm, dtype=np.float32),
                               np.float32(k_blur))
else:
    color_terms = _color_terms_numpy

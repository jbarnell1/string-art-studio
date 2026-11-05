"""Local search over a continuous thread path.

The refinement in v1 and v2 walked the sequence position by position and spent four line
operations at every position whether or not anything there could improve. This module turns that
around: one gain matrix for every chord (about 0.3 s at 240 pins) prices every possible move at
every position at once, and only the moves that look worthwhile are then verified exactly, one
line operation at a time, and kept when the exact change lowers the error.

Moves, for a path ... p1 -> p2 -> p3 ...:
  replace   p1 -> q -> p3            (a different middle pin)
  insert    p1 -> q -> p2            (one more line between two consecutive pins)
  delete    p1 -> p3                 (one line fewer, when the direct chord is allowed)
  unfold    drop p2 and p3 when p1 == p3 (a back-and-forth pair)

Estimates come from the matrix in the state before the round; every applied move is re-scored
exactly in the state it lands in, so the error never goes up.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from stringart.engine.state import Clock, RenderState

ProgressFn = Callable[[dict[str, Any]], None]


def chord_ok(board) -> np.ndarray:
    """Boolean n x n: which chords the board allows (minimum skip, not the same pin)."""
    n = board.pin_count
    idx = np.arange(n)
    d = np.abs(idx[:, None] - idx[None, :])
    d = np.minimum(d, n - d)
    return d >= board.min_skip


def gain_matrix(state: RenderState, ok: np.ndarray) -> np.ndarray:
    """Add-gain for every allowed chord in the current state; disallowed chords are -inf."""
    n = state.board.pin_count
    I, J = np.nonzero(np.triu(ok, k=1))
    g = state.pair_gains(I.astype(np.int32), J.astype(np.int32))
    M = np.full((n, n), -np.inf, dtype=np.float32)
    M[I, J] = g
    M[J, I] = g
    return M


def polish_round(state: RenderState, seq: list[int], ok: np.ndarray, eps: float, clock: Clock,
                 max_moves: int) -> dict[str, int]:
    """One round: price every move, verify the best non-overlapping ones exactly. Mutates seq."""
    board = state.board
    stats = {"replace": 0, "insert": 0, "delete": 0, "unfold": 0, "tried": 0}
    L = len(seq)
    if L < 3:
        return stats
    G = gain_matrix(state, ok)
    P = np.asarray(seq, dtype=np.int64)
    R = state.pair_gains(P[:-1].astype(np.int32), P[1:].astype(np.int32), removal=True)   # line k: P[k] -> P[k+1]
    cands: list[tuple[float, int, int]] = []   # (estimate, kind, position); kind 0 replace, 1 insert, 2 delete, 3 unfold

    # Replace the middle pin at positions 1 .. L-2.
    p1, p2, p3 = P[:-2], P[1:-1], P[2:]
    S = G[p1] + G[p3] + (R[:-1] + R[1:])[:, None]
    S[np.arange(L - 2), p2] = -np.inf
    S[~(ok[p1] & ok[p3])] = -np.inf
    best = S.max(axis=1)
    for i in np.nonzero(best > eps)[0]:
        cands.append((float(best[i]), 0, int(i) + 1))

    # Insert a pin between positions k and k+1 (only while the line budget allows it).
    if L - 1 < board.max_lines:
        a, b = P[:-1], P[1:]
        S = G[a] + G[b] + R[:, None]
        S[~(ok[a] & ok[b])] = -np.inf
        best = S.max(axis=1)
        for k in np.nonzero(best > eps)[0]:
            cands.append((float(best[k]), 1, int(k)))

    # Delete the middle pin (direct chord must be allowed) or unfold a back-and-forth pair.
    direct = G[p1, p3]
    est_del = R[:-1] + R[1:] + np.where(ok[p1, p3], direct, -np.inf)
    est_unf = np.where(p1 == p3, R[:-1] + R[1:], -np.inf)
    for i in np.nonzero(est_del > eps)[0]:
        cands.append((float(est_del[i]), 2, int(i) + 1))
    for i in np.nonzero(est_unf > eps)[0]:
        cands.append((float(est_unf[i]), 3, int(i) + 1))

    if not cands:
        return stats
    cands.sort(key=lambda c: -c[0])
    # Non-overlapping windows of touched positions, best estimates first.
    touched = np.zeros(L + 1, dtype=bool)
    chosen: list[tuple[float, int, int]] = []
    for est, kind, pos in cands:
        if kind == 0 or kind == 2:
            lo, hi = pos - 1, pos + 1
        elif kind == 1:
            lo, hi = pos, pos + 1
        else:
            lo, hi = pos - 1, pos + 2
        if touched[lo:hi + 1].any():
            continue
        touched[lo:hi + 1] = True
        chosen.append((est, kind, pos))
        if len(chosen) >= max_moves:
            break
    # Highest positions first so earlier indices stay valid after inserts and deletes.
    chosen.sort(key=lambda c: -c[2])
    for est, kind, pos in chosen:
        if clock.expired():
            break
        stats["tried"] += 1
        if kind == 0:
            if _verify_replace(state, seq, pos, ok, eps):
                stats["replace"] += 1
        elif kind == 1:
            if len(seq) - 1 < board.max_lines and _verify_insert(state, seq, pos, ok, eps):
                stats["insert"] += 1
        elif kind == 2:
            if _verify_delete(state, seq, pos, eps):
                stats["delete"] += 1
        else:
            if _verify_unfold(state, seq, pos, eps):
                stats["unfold"] += 1
        if (stats["tried"] & 15) == 0:
            state.check_cancel()
    return stats


def _g1(state: RenderState, i: int, j: int) -> float:
    return float(state.gains(i, np.array([j]))[0])


def _r1(state: RenderState, i: int, j: int) -> float:
    return float(state.removal_gains(i, np.array([j]))[0])


def _verify_replace(state: RenderState, seq: list[int], i: int, ok: np.ndarray, eps: float) -> bool:
    board = state.board
    p1, p2, p3 = seq[i - 1], seq[i], seq[i + 1]
    r1 = _r1(state, p1, p2)
    state.remove_line(p1, p2)
    r2 = _r1(state, p2, p3)
    state.remove_line(p2, p3)
    cand = np.nonzero(ok[p1] & ok[p3])[0]
    total = state.gains(p1, cand) + state.gains(p3, cand)
    k = int(np.argmax(total))
    q = int(cand[k])
    where = np.nonzero(cand == p2)[0]
    old = float(total[where[0]]) if len(where) else -np.inf
    if q != p2 and r1 + r2 + float(total[k]) > eps and float(total[k]) > old + 1e-6:
        state.add_line(p1, q)
        state.add_line(q, p3)
        seq[i] = q
        return True
    state.add_line(p1, p2)
    state.add_line(p2, p3)
    return False


def _verify_insert(state: RenderState, seq: list[int], k: int, ok: np.ndarray, eps: float) -> bool:
    a, b = seq[k], seq[k + 1]
    r = _r1(state, a, b)
    state.remove_line(a, b)
    cand = np.nonzero(ok[a] & ok[b])[0]
    total = state.gains(a, cand) + state.gains(b, cand)
    j = int(np.argmax(total))
    if r + float(total[j]) > eps:
        q = int(cand[j])
        state.add_line(a, q)
        state.add_line(q, b)
        seq.insert(k + 1, q)
        return True
    state.add_line(a, b)
    return False


def _verify_delete(state: RenderState, seq: list[int], i: int, eps: float) -> bool:
    p1, p2, p3 = seq[i - 1], seq[i], seq[i + 1]
    r1 = _r1(state, p1, p2)
    state.remove_line(p1, p2)
    r2 = _r1(state, p2, p3)
    state.remove_line(p2, p3)
    g = _g1(state, p1, p3)
    if r1 + r2 + g > eps:
        state.add_line(p1, p3)
        del seq[i]
        return True
    state.add_line(p1, p2)
    state.add_line(p2, p3)
    return False


def _verify_unfold(state: RenderState, seq: list[int], i: int, eps: float) -> bool:
    p1, p2 = seq[i - 1], seq[i]
    r1 = _r1(state, p1, p2)
    state.remove_line(p1, p2)
    r2 = _r1(state, p2, p1)
    if r1 + r2 > eps:
        state.remove_line(p2, p1)
        del seq[i:i + 2]
        return True
    state.add_line(p1, p2)
    return False


def polish(state: RenderState, seq: list[int], budget_s: float, eps: float,
           progress: ProgressFn | None = None, max_rounds: int = 40) -> dict[str, int]:
    """Rounds of polish_round until nothing improves or the budget runs out. Returns totals."""
    clock = Clock(budget_s)
    ok = chord_ok(state.board)
    totals = {"replace": 0, "insert": 0, "delete": 0, "unfold": 0, "tried": 0, "rounds": 0}
    for _ in range(max_rounds):
        if clock.expired() or len(seq) < 3:
            break
        stats = polish_round(state, seq, ok, eps, clock, max_moves=120 + len(seq) // 8)
        totals["rounds"] += 1
        for key in ("replace", "insert", "delete", "unfold", "tried"):
            totals[key] += stats[key]
        applied = stats["replace"] + stats["insert"] + stats["delete"] + stats["unfold"]
        if progress:
            progress({"phase": "polish", "lines": len(seq) - 1, "changes": totals["replace"] + totals["insert"] + totals["delete"] + totals["unfold"],
                      "passes": totals["rounds"], "elapsed_s": clock.elapsed(), "preview": state.preview()})
        if applied == 0:
            break
    return totals

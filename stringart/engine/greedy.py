"""Engine A: greedy with error scoring, then refinement passes."""
from __future__ import annotations

import random

import numpy as np

from stringart.engine.state import Cancelled, Clock, EngineParams, ProgressFn, RenderState, RunResult

PREVIEW_EVERY = 40


def run_greedy(state: RenderState, params: EngineParams, progress: ProgressFn | None = None) -> RunResult:
    board = state.board
    clock = Clock(params.time_budget_s)
    error0 = state.error()
    cur = int(params.start_pin) % board.pin_count
    seq = [cur]
    stop = "no improving line"
    cancelled = False
    bridges = 0
    max_bridges = max(10, board.max_lines // 20)
    # A line must buy a real improvement; chords lying in the zero-weight rim score exactly 0.
    eps = 1e-4 * float(state.W.mean()) * state.size
    try:
        for step in range(board.max_lines):
            js = board.valid_targets(cur)
            g = state.gains(cur, js)
            best = int(np.argmax(g))
            if g[best] <= eps:
                # Nothing good leaves this pin. Look one line ahead: take the least harmful
                # exit when the pair of lines still lowers the error overall.
                if bridges >= max_bridges:
                    stop = "stuck at pin"
                    break
                nxt = _bridge(state, cur, js, g, eps)
                if nxt < 0:
                    break
                bridges += 1
            else:
                nxt = int(js[best])
            state.add_line(cur, nxt)
            seq.append(nxt)
            cur = nxt
            if progress and (step % PREVIEW_EVERY == 0):
                progress({"phase": "greedy", "lines": len(seq) - 1, "max_lines": board.max_lines,
                          "elapsed_s": clock.elapsed(), "preview": state.preview()})
            if step % 16 == 0:
                state.check_cancel()
                if clock.expired():
                    stop = "time budget"
                    break
        else:
            stop = "max lines"
        changes = 0
        if params.refine_seconds > 0 and len(seq) > 3 and not clock.expired():
            changes = refine(state, seq, min(params.refine_seconds, params.time_budget_s - clock.elapsed()),
                             params.seed, progress)
    except Cancelled:
        cancelled = True
        stop = "cancelled"
        changes = 0
    return RunResult(
        sequence=seq, error=state.error(), error_initial=error0, line_count=len(seq) - 1,
        thread_length_mm=board.sequence_length_mm(seq), elapsed_s=clock.elapsed(), engine="a",
        refined_changes=changes, stop_reason=stop, cancelled=cancelled,
    )


def _bridge(state: RenderState, cur: int, js: np.ndarray, g: np.ndarray, eps: float) -> int:
    """Best two-line move from a pin with no improving single line, or -1 when none helps."""
    board = state.board
    order = np.argsort(-g)[:24]
    best_total, best_j = eps, -1
    for k in order:
        j = int(js[k])
        ks = board.valid_targets(j)
        g2 = state.gains(j, ks)
        total = float(g[k]) + float(g2.max())
        if total > best_total:
            best_total, best_j = total, j
    return best_j


def refine(state: RenderState, seq: list[int], budget_s: float, seed: int,
           progress: ProgressFn | None = None) -> int:
    """Walk the sequence in random order; at each middle pin try a better replacement, and drop
    a back-and-forth pair (p1 -> p2 -> p1) when the two lines hurt. Keeps every change that lowers
    the error. Returns the number of accepted changes."""
    board = state.board
    clock = Clock(budget_s)
    rng = random.Random(seed)
    changes = 0
    passes = 0
    while not clock.expired() and len(seq) > 3:
        order = list(range(1, len(seq) - 1))
        rng.shuffle(order)
        improved_this_pass = 0
        for i in order:
            if i >= len(seq) - 1:
                continue
            p1, p2, p3 = seq[i - 1], seq[i], seq[i + 1]
            # Drop a back-and-forth pair when removing both lines lowers the error.
            if p1 == p3:
                g1 = float(state.removal_gains(p1, np.array([p2]))[0])
                state.remove_line(p1, p2)
                g2 = float(state.removal_gains(p2, np.array([p3]))[0])
                if g1 + g2 > 0:
                    state.remove_line(p2, p3)
                    del seq[i:i + 2]
                    changes += 1
                    improved_this_pass += 1
                else:
                    state.add_line(p1, p2)
                continue
            # Replace the middle pin: remove both lines, pick the best q, re-add.
            state.remove_line(p1, p2)
            state.remove_line(p2, p3)
            cand = board.valid_targets(p1)
            cand = cand[board.circular_distance(p3, cand) >= board.min_skip]
            if len(cand) == 0:
                state.add_line(p1, p2)
                state.add_line(p2, p3)
                continue
            total = state.gains(p1, cand) + state.gains(p3, cand)
            k = int(np.argmax(total))
            q = int(cand[k])
            if q != p2:
                where = np.nonzero(cand == p2)[0]
                old = float(total[where[0]]) if len(where) else -1e9
                if float(total[k]) > old + 1e-6:
                    seq[i] = q
                    changes += 1
                    improved_this_pass += 1
                else:
                    q = p2
            state.add_line(p1, q)
            state.add_line(q, p3)
            if (changes & 31) == 0:
                state.check_cancel()
            if clock.expired():
                break
        passes += 1
        if progress:
            progress({"phase": "refine", "lines": len(seq) - 1, "changes": changes, "passes": passes,
                      "elapsed_s": clock.elapsed(), "preview": state.preview()})
        if improved_this_pass == 0:
            break
    return changes

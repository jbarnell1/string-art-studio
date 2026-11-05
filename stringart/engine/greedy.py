"""Engine A: greedy with error scoring, then refinement passes, then a regrow from the end."""
from __future__ import annotations

import random

import numpy as np

from stringart.engine.polish import polish
from stringart.engine.state import Cancelled, Clock, EngineParams, ProgressFn, RenderState, RunResult

PREVIEW_EVERY = 40


def run_greedy(state: RenderState, params: EngineParams, progress: ProgressFn | None = None) -> RunResult:
    board = state.board
    clock = Clock(params.time_budget_s)
    error0 = state.error()
    seq = [int(params.start_pin) % board.pin_count]
    cancelled = False
    changes = 0
    regrown = 0
    rounds = 0
    # A line must buy a real improvement; chords lying in the zero-weight rim score exactly 0.
    eps = 1e-4 * float(state.W.mean()) * state.size
    bridges = [0]
    max_bridges = max(10, board.max_lines // 20)

    def walk(phase: str) -> str:
        """Extend seq greedily from its last pin until nothing helps, the line budget or the clock."""
        cur = seq[-1]
        step = 0
        while len(seq) - 1 < board.max_lines:
            js = board.valid_targets(cur)
            g = state.gains(cur, js)
            best = int(np.argmax(g))
            if params.lookahead > 1 and g[best] > eps:
                best = _lookahead(state, js, g, params.lookahead)
            if g[best] <= eps:
                # Nothing good leaves this pin. Look one line ahead: take the least harmful
                # exit when the pair of lines still lowers the error overall.
                if bridges[0] >= max_bridges:
                    return "stuck at pin"
                nxt = _bridge(state, cur, js, g, eps)
                if nxt < 0:
                    return "no improving line"
                bridges[0] += 1
            else:
                nxt = int(js[best])
            state.add_line(cur, nxt)
            seq.append(nxt)
            cur = nxt
            step += 1
            if progress and (step % PREVIEW_EVERY == 0):
                progress({"phase": phase, "lines": len(seq) - 1, "max_lines": board.max_lines,
                          "elapsed_s": clock.elapsed(), "preview": state.preview()})
            if step % 16 == 0:
                state.check_cancel()
                if clock.expired():
                    return "time budget"
        return "max lines"

    try:
        stop = walk("greedy")
        if params.refine_seconds > 0 and len(seq) > 3 and not clock.expired() and params.local_search != "none":
            search = Clock(min(params.refine_seconds, params.time_budget_s - clock.elapsed()))
            if params.local_search == "refine":
                changes = refine(state, seq, search.budget, params.seed, progress)
                if len(seq) - 1 < board.max_lines and not clock.expired() and stop != "stuck at pin":
                    before = len(seq)
                    stop = walk("regrow")
                    regrown = len(seq) - before
            else:
                # Alternate: local search over the whole path, then grow again from the end,
                # until neither changes anything or the search budget is spent.
                while not search.expired() and not clock.expired():
                    totals = polish(state, seq, search.budget - search.elapsed(), eps, progress)
                    rounds += totals["rounds"]
                    applied = totals["replace"] + totals["insert"] + totals["delete"] + totals["unfold"]
                    changes += applied
                    grown = 0
                    if len(seq) - 1 < board.max_lines and stop != "stuck at pin" and not clock.expired():
                        before = len(seq)
                        stop = walk("regrow")
                        grown = len(seq) - before
                        regrown += grown
                    if applied == 0 and grown == 0:
                        break
    except Cancelled:
        cancelled = True
        stop = "cancelled"
        changes = 0
    return RunResult(
        sequence=seq, error=state.error(), error_initial=error0, line_count=len(seq) - 1,
        thread_length_mm=board.sequence_length_mm(seq), elapsed_s=clock.elapsed(), engine="a",
        refined_changes=changes, regrown_lines=regrown, polish_rounds=rounds, stop_reason=stop, cancelled=cancelled,
    )


def _lookahead(state: RenderState, js: np.ndarray, g: np.ndarray, k: int) -> int:
    """Among the k best next lines, pick the one whose best follow-up line adds the most."""
    board = state.board
    order = np.argsort(-g)[:k]
    best_k, best_total = int(order[0]), -np.inf
    for idx in order:
        j = int(js[idx])
        g2 = state.gains(j, board.valid_targets(j))
        total = float(g[idx]) + max(float(g2.max()), 0.0)
        if total > best_total:
            best_total, best_k = total, int(idx)
    return best_k


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


def _worst_first(state: RenderState, seq: list[int]) -> list[int]:
    """Middle indices ordered by how much the error would drop if the two lines meeting there
    were removed: the lines doing the most harm get the first chance at a better pin."""
    I = np.array(seq[:-1], dtype=np.int32)
    J = np.array(seq[1:], dtype=np.int32)
    g = state.pair_gains(I, J, removal=True)
    score = g[:-1] + g[1:]            # score[i - 1] belongs to middle index i
    return [int(i) + 1 for i in np.argsort(-score)]


def refine(state: RenderState, seq: list[int], budget_s: float, seed: int,
           progress: ProgressFn | None = None) -> int:
    """Visit the sequence's middle pins, worst lines first and then in random order; at each one
    try a better replacement pin, and drop a back-and-forth pair (p1 -> p2 -> p1) when the two
    lines hurt. Keeps every change that lowers the error. Returns the number of accepted changes."""
    board = state.board
    clock = Clock(budget_s)
    rng = random.Random(seed)
    changes = 0
    passes = 0
    while not clock.expired() and len(seq) > 3:
        if passes == 0:
            order = _worst_first(state, seq)
        else:
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

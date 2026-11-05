"""Engine B: pick the best multiset of chords without a continuity constraint, then repair the
graph so one continuous thread can trace it (even degrees, one component) and read the path off
with Hierholzer's algorithm. The repair cost is reported so engine B can be judged honestly."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from stringart.board import BoardConfig
from stringart.engine.state import Cancelled, Clock, EngineParams, ProgressFn, RenderState, RunResult


def all_chords(board: BoardConfig) -> tuple[np.ndarray, np.ndarray]:
    """Every valid chord once (i < j)."""
    n = board.pin_count
    I, J = np.triu_indices(n, k=1)
    d = np.minimum((J - I) % n, (I - J) % n)
    keep = d >= board.min_skip
    return I[keep].astype(np.int32), J[keep].astype(np.int32)


def run_setcover(state: RenderState, params: EngineParams, progress: ProgressFn | None = None) -> RunResult:
    board = state.board
    clock = Clock(params.time_budget_s)
    error0 = state.error()
    I, J = all_chords(board)
    counts = np.zeros(len(I), dtype=np.int32)   # multiset: how many passes of each chord
    budget = int(board.max_lines * 0.95)
    stop = "no improving chord"
    cancelled = False
    repair_lines = 0
    repair_error = 0.0
    eps = 1e-4 * float(state.W.mean()) * state.size

    def total() -> int:
        return int(counts.sum())

    def emit(phase: str) -> None:
        if progress:
            progress({"phase": phase, "lines": total(), "max_lines": board.max_lines,
                      "elapsed_s": clock.elapsed(), "preview": state.preview()})

    def add_phase() -> bool:
        """Rounds of: score every chord, add the best batch. Returns False when nothing helps."""
        nonlocal stop
        added_any = False
        while total() < budget:
            state.check_cancel()
            if clock.expired():
                stop = "time budget"
                return added_any
            remaining = budget - total()
            batch = int(min(max(params.batch_add, remaining // 30), 120, remaining))
            g = state.pair_gains(I, J)
            top = np.argpartition(-g, min(batch, len(g) - 1))[:batch]
            top = top[g[top] > eps]
            if len(top) == 0:
                return added_any
            for k in top[np.argsort(-g[top])]:
                state.add_line(int(I[k]), int(J[k]))
                counts[k] += 1
                added_any = True
            emit("set: adding")
        stop = "max lines"
        return added_any

    def remove_phase() -> int:
        removed = 0
        for _ in range(8):
            state.check_cancel()
            present = np.nonzero(counts > 0)[0]
            if len(present) == 0:
                break
            g = state.pair_gains(I[present], J[present], removal=True)
            order = np.argsort(-g)[:params.batch_add * 2]
            order = order[g[order] > eps]
            if len(order) == 0:
                break
            for o in order:
                k = int(present[o])
                state.remove_line(int(I[k]), int(J[k]))
                counts[k] -= 1
                removed += 1
            emit("set: pruning")
        return removed

    try:
        add_phase()
        for _ in range(params.improve_rounds):
            if clock.expired():
                break
            if remove_phase() == 0:
                break
            if not add_phase():
                break
        err_before = state.error()
        multiset: dict[tuple[int, int], int] = {
            (int(I[k]), int(J[k])): int(counts[k]) for k in np.nonzero(counts > 0)[0]
        }
        repair_lines = _repair(state, board, multiset)
        repair_error = state.error() - err_before
        emit("repair")
        seq = eulerian_sequence(multiset, board.pin_count)
    except Cancelled:
        cancelled = True
        stop = "cancelled"
        seq = []
    return RunResult(
        sequence=seq, error=state.error(), error_initial=error0, line_count=max(len(seq) - 1, 0),
        thread_length_mm=board.sequence_length_mm(seq), elapsed_s=clock.elapsed(), engine="b",
        repair_lines=repair_lines, repair_error=float(repair_error), stop_reason=stop, cancelled=cancelled,
    )


def _chord_key(i: int, j: int) -> tuple[int, int]:
    return (i, j) if i < j else (j, i)


def _degrees(multiset: dict[tuple[int, int], int], n_pins: int) -> np.ndarray:
    deg = np.zeros(n_pins, dtype=np.int64)
    for (i, j), n in multiset.items():
        deg[i] += n
        deg[j] += n
    return deg


def _pair_cost_row(state: RenderState, board: BoardConfig, i: int, others: np.ndarray) -> np.ndarray:
    """Error added by joining pin i to each of `others` (negative gain); a chord shorter than the
    minimum skip is strongly discouraged but never impossible, and i to itself is impossible."""
    g = state.gains(i, others)
    cost = -g.astype(np.float64)
    cost[board.circular_distance(i, others) < board.min_skip] += 1e6
    cost[others == i] = np.inf
    return cost


def _repair(state: RenderState, board: BoardConfig, multiset: dict[tuple[int, int], int]) -> int:
    """Pair odd-degree pins with the cheapest connecting chords, then join components with
    doubled chords. Mutates the state and the multiset; returns the number of chords added."""
    added = 0
    deg = _degrees(multiset, board.pin_count)
    odd = [int(p) for p in np.nonzero(deg % 2 == 1)[0]]
    if odd:
        odd_arr = np.array(odd)
        cost = np.vstack([_pair_cost_row(state, board, i, odd_arr) for i in odd])
        cost = np.minimum(cost, cost.T)
        remaining = set(range(len(odd)))
        while len(remaining) > 1:
            rem = sorted(remaining)
            sub = cost[np.ix_(rem, rem)]
            a, b = divmod(int(np.argmin(sub)), len(rem))
            i, j = odd[rem[a]], odd[rem[b]]
            state.add_line(i, j)
            multiset[_chord_key(i, j)] = multiset.get(_chord_key(i, j), 0) + 1
            added += 1
            remaining -= {rem[a], rem[b]}
            for k in remaining:  # costs move as lines land
                row = _pair_cost_row(state, board, odd[k], odd_arr)
                cost[k] = row
                cost[:, k] = row
    comps = _components(multiset)
    while len(comps) > 1:
        comps.sort(key=len, reverse=True)
        main, other = comps[0], comps[1]
        other_arr = np.array(sorted(other))
        best = (np.inf, -1, -1)
        for i in sorted(main):
            g = state.gains(i, other_arr)
            k = int(np.argmax(g))
            if -float(g[k]) < best[0]:
                best = (-float(g[k]), i, int(other_arr[k]))
        _, i, j = best
        for _ in range(2):  # doubled so every degree stays even
            state.add_line(i, j)
            multiset[_chord_key(i, j)] = multiset.get(_chord_key(i, j), 0) + 1
            added += 1
        comps = _components(multiset)
    return added


def _components(multiset: dict[tuple[int, int], int]) -> list[set[int]]:
    adj: dict[int, set[int]] = defaultdict(set)
    for (i, j), n in multiset.items():
        if n > 0:
            adj[i].add(j)
            adj[j].add(i)
    seen: set[int] = set()
    comps: list[set[int]] = []
    for start in adj:
        if start in seen:
            continue
        stack = [start]
        comp: set[int] = set()
        while stack:
            v = stack.pop()
            if v in comp:
                continue
            comp.add(v)
            stack.extend(adj[v] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def eulerian_sequence(multiset: dict[tuple[int, int], int], n_pins: int) -> list[int]:
    """Hierholzer's algorithm on the chord multigraph (all degrees even, one component)."""
    edges: list[tuple[int, int]] = []
    for (i, j), n in multiset.items():
        edges.extend([(i, j)] * n)
    if not edges:
        return []
    adj: dict[int, list[int]] = defaultdict(list)
    for eid, (i, j) in enumerate(edges):
        adj[i].append(eid)
        adj[j].append(eid)
    used = [False] * len(edges)
    ptr: dict[int, int] = defaultdict(int)
    deg = _degrees(multiset, n_pins)
    odd = [int(p) for p in np.nonzero(deg % 2 == 1)[0]]
    start = odd[0] if odd else edges[0][0]
    stack = [start]
    path: list[int] = []
    while stack:
        v = stack[-1]
        lst = adj[v]
        while ptr[v] < len(lst) and used[lst[ptr[v]]]:
            ptr[v] += 1
        if ptr[v] == len(lst):
            path.append(stack.pop())
        else:
            eid = lst[ptr[v]]
            used[eid] = True
            i, j = edges[eid]
            stack.append(j if i == v else i)
    path.reverse()
    return path

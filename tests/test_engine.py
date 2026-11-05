"""Engine model and local search on a small synthetic target."""
from __future__ import annotations

import numpy as np

from stringart.board import BoardConfig
from stringart.engine.greedy import run_greedy
from stringart.engine.polish import chord_ok, gain_matrix, polish
from stringart.engine.state import EngineParams, RenderState, dec, enc
from stringart.prep import board_weight


def _target(res: int = 240) -> np.ndarray:
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float32) / res
    gray = np.ones((res, res), dtype=np.float32)
    gray[((xx - 0.5) ** 2 + (yy - 0.5) ** 2) < 0.08] = 0.55
    gray[((xx - 0.4) ** 2 + (yy - 0.45) ** 2) < 0.004] = 0.05
    gray[((xx - 0.6) ** 2 + (yy - 0.45) ** 2) < 0.004] = 0.05
    return gray


def _board() -> BoardConfig:
    return BoardConfig(pin_count=72, min_skip=6, max_lines=300, work_res=240)


def test_encoding_round_trip():
    p = np.linspace(0.1, 1.0, 19, dtype=np.float32)  # below the luminance floor the encoding clamps
    assert np.allclose(enc(dec(p)), p, atol=1e-4)


def test_linear_objective_wants_more_thread_than_coverage():
    """A mid-grey needs well over half coverage once light adds linearly."""
    board = _board()
    gray = np.full((240, 240), 0.5, dtype=np.float32)
    w = board_weight(board, 240)
    lin = RenderState(gray, w, board, objective="linear")
    cov = RenderState(gray, w, board, objective="coverage")
    # Coverage that makes the linear model happy at the centre, versus the coverage model's 0.5.
    y_needed = dec(np.float32(0.5))
    c_needed = (lin.y_board - y_needed) / (lin.y_board - lin.y_thread)
    assert 0.7 < c_needed < 0.85
    assert abs(float(cov.Pt[120, 120]) - 0.5) < 1e-3


def test_gains_predict_error_change_and_removal_restores():
    board = _board()
    gray = _target()
    state = RenderState(gray, board_weight(board, 240), board, light_protection=1.0, objective="linear")
    e0 = state.error()
    js = board.valid_targets(0)
    g = state.gains(0, js)
    j = int(js[int(np.argmax(g))])
    assert g.max() > 0
    state.add_line(0, j)
    e1 = state.error()
    assert e1 < e0
    r = float(state.removal_gains(0, np.array([j]))[0])
    assert r < 0  # removing the only helpful line would hurt
    state.remove_line(0, j)
    assert abs(state.error() - e0) < 1e-5
    assert int(state.N.max()) == 0


def test_greedy_with_polish_beats_greedy_alone():
    board = _board()
    gray = _target()
    w = board_weight(board, 240)
    plain = run_greedy(RenderState(gray, w, board, 1.0), EngineParams(refine_seconds=0, time_budget_s=60))
    polished = run_greedy(RenderState(gray, w, board, 1.0), EngineParams(refine_seconds=4, time_budget_s=60))
    assert plain.error < plain.error_initial
    assert polished.error <= plain.error + 1e-6
    assert polished.polish_rounds >= 1
    seq = polished.sequence
    assert all(board.chord_valid(a, b) for a, b in zip(seq[:-1], seq[1:]))
    assert len(seq) - 1 <= board.max_lines


def test_polish_only_applies_verified_improvements():
    board = _board()
    gray = _target()
    state = RenderState(gray, board_weight(board, 240), board, 1.0)
    rng = np.random.default_rng(3)
    seq = [0]
    for _ in range(80):  # a deliberately bad random path
        js = board.valid_targets(seq[-1])
        seq.append(int(rng.choice(js)))
    for a, b in zip(seq[:-1], seq[1:]):
        state.add_line(a, b)
    before = state.error()
    ok = chord_ok(board)
    assert gain_matrix(state, ok).shape == (72, 72)
    totals = polish(state, seq, budget_s=5, eps=1e-6)
    assert totals["replace"] + totals["insert"] + totals["delete"] + totals["unfold"] > 0
    assert state.error() < before
    assert all(board.chord_valid(a, b) for a, b in zip(seq[:-1], seq[1:]))
    # The state's coverage still matches the sequence exactly.
    rebuilt = RenderState(gray, board_weight(board, 240), board, 1.0)
    for a, b in zip(seq[:-1], seq[1:]):
        rebuilt.add_line(a, b)
    assert np.allclose(rebuilt.C, state.C, atol=1e-4)

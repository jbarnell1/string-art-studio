"""Colour engine: palettes, the layered model, and a small end-to-end run."""
from __future__ import annotations

import numpy as np

from stringart.board import BoardConfig
from stringart.engine.color import (ColorState, auto_palette, color_name, palette_order, rgb_to_lab,
                                    run_color_greedy)
from stringart.engine.render import render_layers_rgb, render_rgb
from stringart.engine.state import EngineParams
from stringart.prep import board_weight


def _target(res: int = 200) -> np.ndarray:
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float32) / res
    rgb = np.ones((res, res, 3), dtype=np.float32)
    rgb[((xx - 0.5) ** 2 + (yy - 0.32) ** 2) < 0.02] = (0.85, 0.15, 0.1)      # red disc, top
    rgb[((xx - 0.5) ** 2 + (yy - 0.7) ** 2) < 0.02] = (0.08, 0.08, 0.08)      # black disc, bottom
    return rgb


def test_palette_order_and_names():
    assert palette_order(["#161616", "#fff200", "#2457c5"], False) == ["#fff200", "#2457c5", "#161616"]
    assert palette_order(["#161616", "#fff200"], True) == ["#161616", "#fff200"]
    assert color_name("#d9261c") == "red" and color_name("#000000") == "black" and color_name("#00aeef") == "cyan"


def test_auto_palette_skips_the_board_colour_and_keeps_black():
    rgb = _target()
    pal = auto_palette(rgb, 3, board_color="#ffffff")
    assert pal[-1] == "#161616"
    assert all(c != "#ffffff" for c in pal)
    assert 2 <= len(pal) <= 3


def test_colour_gains_match_error_change():
    board = BoardConfig(pin_count=60, min_skip=5, max_lines=100, work_res=200)
    colors = palette_order(["#161616", "#d9261c"], False)
    state = ColorState(_target(), board_weight(board, 200), board, colors)
    e0 = state.error() ** 2 * float(state.W.sum())
    for layer in range(2):
        js = board.valid_targets(0)
        g = state.gains(layer, 0, js)
        j = int(js[int(np.argmax(g))])
        state.add_line(layer, 0, j)
        e1 = state.error() ** 2 * float(state.W.sum())
        assert abs((e0 - e1) - float(g.max())) < 0.05 * abs(float(g.max())) + 1e-3
        state.remove_line(layer, 0, j)
        assert abs(state.error() ** 2 * float(state.W.sum()) - e0) < 1e-3


def test_colour_run_uses_the_right_thread_for_each_disc():
    board = BoardConfig(pin_count=60, min_skip=5, max_lines=240, work_res=200)
    colors = palette_order(["#161616", "#d9261c"], False)
    state = ColorState(_target(), board_weight(board, 200), board, colors)
    seqs, result = run_color_greedy(state, EngineParams(refine_seconds=2, time_budget_s=60))
    assert len(seqs) == 2 and result.error < result.error_initial
    assert all(len(s) >= 2 for s in seqs)
    # The line budget is shared by all colours, through the local search's inserts as well.
    assert sum(len(s) - 1 for s in seqs) <= board.max_lines
    res = 200
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float32) / res
    red = ((xx - 0.5) ** 2 + (yy - 0.32) ** 2) < 0.012
    black = ((xx - 0.5) ** 2 + (yy - 0.7) ** 2) < 0.012
    red_layer, black_layer = 0, 1
    assert state.C[red_layer][red].mean() > state.C[red_layer][black].mean()
    assert state.C[black_layer][black].mean() > state.C[black_layer][red].mean()
    for s in seqs:
        assert all(board.chord_valid(a, b) for a, b in zip(s[:-1], s[1:]))


def test_layered_render_matches_single_render_for_one_layer():
    board = BoardConfig(pin_count=60, min_skip=5)
    seq = [0, 20, 41, 7, 33]
    a = render_rgb(board, seq, 240)
    b = render_layers_rgb(board, [{"color": board.thread_color, "sequence": seq}], 240)
    assert np.array_equal(a, b)
    two = render_layers_rgb(board, [{"color": "#d9261c", "sequence": seq}, {"color": "#161616", "sequence": [3, 30]}], 240)
    assert two.shape == (240, 240, 3)
    lab_white = rgb_to_lab(np.ones((1, 1, 3), dtype=np.float32))[0, 0]
    assert abs(lab_white[0] - 1.0) < 1e-3

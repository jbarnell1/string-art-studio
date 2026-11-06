"""Build estimate (ADR-020): wraps, brad length, time and spools from a plan."""
from __future__ import annotations

from stringart.board import BoardConfig
from stringart.estimate import build_estimate


def test_wraps_count_middle_visits_and_the_two_knots():
    board = BoardConfig.from_dict({"pin_count": 100, "min_skip": 10})
    seq = [0, 50, 0, 50, 0, 30]          # pin 0 visited twice in the middle plus the start knot
    e = build_estimate(board, [{"name": "black", "color": "#161616", "sequence": seq}], 400)
    assert e["lines"] == 5
    assert e["wraps"]["max"] == 3        # pin 0: start knot + 2 middle visits; pin 50: 2 visits
    assert e["thread_changes"] == 0


def test_brad_length_grows_with_wraps_and_respects_the_board():
    board = BoardConfig.from_dict({"pin_count": 100, "min_skip": 10, "wrap_height_mm": 0.35, "board_thickness_mm": 19})
    light = build_estimate(board, [{"sequence": [0, 50] * 10}], 400)
    heavy = build_estimate(board, [{"sequence": [0, 50] * 100}], 400)
    assert light["brad"]["length_in"] < heavy["brad"]["length_in"]
    assert light["brad"]["gauge"] == 18 and heavy["brad"]["gauge"] == 16
    assert heavy["notes"], "a pin with 100 wraps should come with a warning"
    # exposed = wraps * wrap height + headroom; embed = thickness - 5 within 8..15 mm
    assert abs(heavy["brad"]["exposed_mm"] - (heavy["wraps"]["max"] * 0.35 + 3.0)) < 1e-9
    assert heavy["brad"]["embed_mm"] == 14.0
    assert heavy["brad"]["length_in"] * 25.4 >= heavy["brad"]["length_mm"]


def test_time_scales_with_pace_and_colour_changes_count():
    board = BoardConfig.from_dict({"pin_count": 100, "min_skip": 10})
    layers = [{"name": "red", "color": "#d9261c", "sequence": [0, 40, 80]},
              {"name": "none", "color": "#2457c5", "sequence": [5]},          # no lines: not a change
              {"name": "black", "color": "#161616", "sequence": [10, 60, 20, 70]}]
    slow = build_estimate(board, layers, 200)
    fast = build_estimate(board, layers, 400)
    assert slow["lines"] == 5 and slow["thread_changes"] == 1
    assert abs(slow["hours"]["stringing"] - 2 * fast["hours"]["stringing"]) < 1e-9
    assert [l["name"] for l in slow["layers"]] == ["red", "black"]
    assert slow["spools"] == sum(l["spools"] for l in slow["layers"])

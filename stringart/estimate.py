"""Build estimate for a plan: time, pin wraps, brad length and spools (ADR-020).

Everything comes from the plan itself (the wraps each pin really gets) and three numbers a
household can change: the board thickness, the height one wrap of thread takes on a brad, and the
building pace. The defaults are guesses until a pilot build measures them; the results page says so.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from stringart.board import BoardConfig, thread_estimate

# Common brad lengths in inches, with millimetres for the label.
BRAD_LENGTHS_IN = (0.625, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5)
# Brad gauge follows the load: 18 gauge (1.0 mm) for most plans, 16 gauge (1.3 mm) when the busiest
# pin carries enough wraps to bend a thin brad. The template prints marks, so any gauge fits it.
GAUGE_NORMAL = (18, 1.02)
GAUGE_HEAVY = (16, 1.29)
HEADROOM_MM = 3.0            # clear brad above the top wrap so the last wraps cannot ride off
HAMMER_S_PER_PIN = 10.0      # placing and driving one brad through the paper template
CHANGE_MIN = 5.0             # tie off, swap spools, tie on
HEAVY_WRAPS = 60             # above this, a thin brad under full tension starts to lean


def _inch_label(inches: float) -> str:
    whole = int(inches)
    frac = inches - whole
    fracs = {0.0: "", 0.25: "¼", 0.5: "½", 0.75: "¾", 0.625: "⅝"}
    part = fracs.get(round(frac, 3), f"{frac:.2f}")
    return f"{whole if whole else ''}{part}\"" if (whole or part) else f"{inches}\""


def build_estimate(board: BoardConfig, layers: list[dict[str, Any]], lines_per_hour: float = 400.0) -> dict[str, Any]:
    """Estimate for a plan given as layers [{name, color, sequence}] in build order."""
    lines_per_hour = float(min(max(lines_per_hour, 50.0), 3000.0))
    n = board.pin_count
    wraps = np.zeros(n, dtype=np.int64)
    lines = 0
    for layer in layers:
        seq = [int(p) for p in layer.get("sequence") or []]
        if len(seq) < 2:
            continue
        lines += len(seq) - 1
        wraps += np.bincount(seq[1:-1], minlength=n)[:n]   # every middle visit is one wrap
        wraps[seq[0]] += 1                                 # the knot where the colour starts
        wraps[seq[-1]] += 1                                # and where it is tied off
    max_wraps = int(wraps.max()) if lines else 0
    exposed_mm = max_wraps * board.wrap_height_mm + HEADROOM_MM
    embed_mm = float(min(max(board.board_thickness_mm - 5.0, 8.0), 15.0))
    need_mm = exposed_mm + embed_mm
    brad_in = next((L for L in BRAD_LENGTHS_IN if L * 25.4 >= need_mm), None)
    gauge = GAUGE_HEAVY if max_wraps > HEAVY_WRAPS else GAUGE_NORMAL
    used = [layer for layer in layers if len(layer.get("sequence") or []) >= 2]
    changes = max(len(used) - 1, 0)
    string_h = lines / lines_per_hour
    hammer_h = n * HAMMER_S_PER_PIN / 3600.0
    change_h = changes * CHANGE_MIN / 60.0
    per_layer = []
    for layer in used:
        est = thread_estimate(board, layer["sequence"])
        per_layer.append({"name": layer.get("name") or "thread", "color": layer.get("color") or board.thread_color,
                          "lines": len(layer["sequence"]) - 1, "length_mm": est.length_mm,
                          "with_margin_mm": est.with_margin_mm, "spools": est.spools})
    notes = []
    if max_wraps > HEAVY_WRAPS:
        notes.append(f"The busiest pin takes {max_wraps} wraps, so 16 gauge brads are suggested: a thin brad under "
                     f"full tension can lean. Pine or plywood holds brads better than MDF.")
    if brad_in is None:
        notes.append("No common brad is long enough for this plan on this board; lower Maximum lines or use a thicker board.")
    if board.board_thickness_mm - embed_mm < 3.0:
        notes.append("The board is thin for this load; the brads would need to go almost through it.")
    return {
        "lines": lines,
        "lines_per_hour": lines_per_hour,
        "hours": {"stringing": string_h, "hammering": hammer_h, "thread_changes": change_h,
                  "total": string_h + hammer_h + change_h},
        "thread_changes": changes,
        "pins": n,
        "wraps": {"max": max_wraps, "mean": float(wraps.mean()), "p90": float(np.percentile(wraps, 90)),
                  "over_60": int((wraps > HEAVY_WRAPS).sum())},
        "brad": {"exposed_mm": exposed_mm, "embed_mm": embed_mm, "length_mm": need_mm,
                 "length_in": brad_in, "label": (_inch_label(brad_in) + f" ({brad_in * 25.4:.0f} mm)") if brad_in else None,
                 "gauge": gauge[0], "gauge_mm": gauge[1]},
        "layers": per_layer,
        "spools": sum(layer["spools"] for layer in per_layer),
        "assumptions": {"wrap_height_mm": board.wrap_height_mm, "board_thickness_mm": board.board_thickness_mm,
                        "headroom_mm": HEADROOM_MM, "hammer_s_per_pin": HAMMER_S_PER_PIN, "change_min": CHANGE_MIN},
        "notes": notes,
    }

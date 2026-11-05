"""Threading sequence exports: plain text, CSV and JSON.

All three are built from `steps_table`, so they agree on step numbering,
labels and per-step thread lengths.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from stringart.board import BoardConfig, thread_estimate


def as_layers(board: BoardConfig, sequence_or_layers: Any) -> list[dict[str, Any]]:
    """Accept a flat pin list (one thread in the board's colour) or a list of layer dicts
    ({name, color, sequence}) and return layer dicts, in build order."""
    if sequence_or_layers and isinstance(sequence_or_layers[0], dict):
        return [{"name": str(l.get("name") or "thread"), "color": str(l.get("color") or board.thread_color),
                 "sequence": [int(p) for p in l.get("sequence") or []]} for l in sequence_or_layers]
    from stringart.engine.color import color_name
    return [{"name": color_name(board.thread_color), "color": board.thread_color, "sequence": [int(p) for p in sequence_or_layers or []]}]


def steps_table(board: BoardConfig, sequence_or_layers: Any) -> list[dict[str, Any]]:
    """One dict per step: step (1-based across all colours), layer (0-based), color, pin, label,
    from_pin (None at the start of a colour), length_mm."""
    rows: list[dict[str, Any]] = []
    step = 0
    for layer_index, layer in enumerate(as_layers(board, sequence_or_layers)):
        previous: int | None = None
        for pin in layer["sequence"]:
            pin = int(pin)
            length = board.chord_length_mm(previous, pin) if previous is not None else 0.0
            step += 1
            rows.append({
                "step": step,
                "layer": layer_index,
                "color": layer["color"],
                "color_name": layer["name"],
                "pin": pin,
                "label": board.sector_label(pin),
                "from_pin": previous,
                "length_mm": round(length, 3),
            })
            previous = pin
    return rows


def sequence_txt(board: BoardConfig, sequence_or_layers: Any) -> bytes:
    """One line per step, like "step 1: pin 17 (A17)", with a heading per colour when there are several."""
    layers = as_layers(board, sequence_or_layers)
    lines: list[str] = []
    current = None
    for row in steps_table(board, layers):
        if len(layers) > 1 and row["layer"] != current:
            current = row["layer"]
            lines.append(f"== {row['color_name']} thread ({row['color']}) ==")
        lines.append(f"step {row['step']}: pin {row['pin']} ({row['label']})")
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def sequence_csv(board: BoardConfig, sequence_or_layers: Any) -> bytes:
    """CSV with header step,layer,color,pin,label,from_pin,length_mm. from_pin is blank where a colour starts."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["step", "layer", "color", "pin", "label", "from_pin", "length_mm"])
    for row in steps_table(board, sequence_or_layers):
        from_pin = "" if row["from_pin"] is None else row["from_pin"]
        writer.writerow([row["step"], row["layer"], row["color_name"], row["pin"], row["label"], from_pin, row["length_mm"]])
    return buffer.getvalue().encode("utf-8")


def sequence_json(board: BoardConfig, sequence_or_layers: Any) -> bytes:
    """JSON document with the board, the pin table, the layers (name, colour, sequence), the flat
    sequence across layers, the steps and thread estimates per layer and in total."""
    layers = as_layers(board, sequence_or_layers)
    flat = [p for layer in layers for p in layer["sequence"]]
    payload = {
        "board": board.to_dict(),
        "pins": board.pin_table(),
        "layers": [dict(layer, thread=thread_estimate(board, layer["sequence"]).to_dict()) for layer in layers],
        "sequence": flat,
        "step_count": len(flat),
        "steps": steps_table(board, layers),
        "thread": layers_thread_estimate(board, layers),
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def layers_thread_estimate(board: BoardConfig, layers: list[dict[str, Any]]) -> dict[str, Any]:
    """Total thread across layers (spools are counted per colour, since each is its own spool)."""
    per = [thread_estimate(board, layer["sequence"]) for layer in layers]
    total = dict(thread_estimate(board, []).to_dict())
    total["length_mm"] = sum(e.length_mm for e in per)
    total["with_margin_mm"] = sum(e.with_margin_mm for e in per)
    total["spools"] = sum(e.spools for e in per)
    return total

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


def steps_table(board: BoardConfig, sequence: list[int]) -> list[dict[str, Any]]:
    """One dict per step: step (1-based), pin, label, from_pin (None on the first step), length_mm."""
    rows: list[dict[str, Any]] = []
    previous: int | None = None
    for index, pin in enumerate(sequence):
        pin = int(pin)
        length = board.chord_length_mm(previous, pin) if previous is not None else 0.0
        rows.append({
            "step": index + 1,
            "pin": pin,
            "label": board.sector_label(pin),
            "from_pin": previous,
            "length_mm": round(length, 3),
        })
        previous = pin
    return rows


def sequence_txt(board: BoardConfig, sequence: list[int]) -> bytes:
    """One line per step, like "step 1: pin 17 (A17)"."""
    lines = [f"step {row['step']}: pin {row['pin']} ({row['label']})" for row in steps_table(board, sequence)]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def sequence_csv(board: BoardConfig, sequence: list[int]) -> bytes:
    """CSV with header step,pin,label,from_pin,length_mm. from_pin is blank on the first step."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["step", "pin", "label", "from_pin", "length_mm"])
    for row in steps_table(board, sequence):
        from_pin = "" if row["from_pin"] is None else row["from_pin"]
        writer.writerow([row["step"], row["pin"], row["label"], from_pin, row["length_mm"]])
    return buffer.getvalue().encode("utf-8")


def sequence_json(board: BoardConfig, sequence: list[int]) -> bytes:
    """JSON document with the board, the pin table, the sequence, its steps and a thread estimate."""
    sequence = [int(p) for p in sequence]
    payload = {
        "board": board.to_dict(),
        "pins": board.pin_table(),
        "sequence": sequence,
        "step_count": len(sequence),
        "steps": steps_table(board, sequence),
        "thread": thread_estimate(board, sequence).to_dict(),
    }
    return json.dumps(payload, indent=2).encode("utf-8")

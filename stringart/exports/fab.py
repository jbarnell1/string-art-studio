"""Fabrication files for the board: SVG and DXF for laser cutters and CNC.

Both formats use millimetres, put the board centre at the origin of the pin
geometry and draw strokes only. The SVG keeps the board's y-down convention
(centre at R, R inside the viewBox). DXF has y pointing up, so pin y values are
negated there.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Iterable

from stringart.board import BoardConfig

SVG_NS = "http://www.w3.org/2000/svg"
STROKE_MM = 0.2
RING_WIDTH_INSIDE_PINS_MM = 15.0   # inner edge of the ring sits this far inside the pin ring
NOTCH_W_MM = 2.0                   # tangential
NOTCH_H_MM = 4.0                   # radial
VARIANTS = ("holes", "ring")


def _check_variant(variant: str) -> str:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}, expected one of {VARIANTS}")
    return variant


def _hole_radius(board: BoardConfig, hole_diameter_mm: float | None) -> float:
    d = board.pin_diameter_mm if hole_diameter_mm is None else float(hole_diameter_mm)
    if d <= 0:
        raise ValueError("hole diameter must be positive")
    return d / 2.0


def _num(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


# SVG --------------------------------------------------------------------------------------------


def fab_svg(board: BoardConfig, hole_diameter_mm: float | None = None, variant: str = "holes") -> bytes:
    """SVG of the board cut file. "holes" drills a hole per pin, "ring" cuts an annulus with notches."""
    variant = _check_variant(variant)
    size = board.diameter_mm
    cx = cy = board.radius_mm
    positions = board.pin_positions_mm()
    angles = board.pin_angles()

    root = ET.Element("svg", {
        "xmlns": SVG_NS,
        "width": f"{_num(size)}mm",
        "height": f"{_num(size)}mm",
        "viewBox": f"0 0 {_num(size)} {_num(size)}",
    })
    ET.SubElement(root, "title").text = (
        f"String Art Studio board {_num(board.diameter_mm)} mm, {board.pin_count} pins, {variant}"
    )
    cut = ET.SubElement(root, "g", {
        "id": "cut",
        "fill": "none",
        "stroke": "#000000",
        "stroke-width": _num(STROKE_MM),
    })

    outline = ET.SubElement(cut, "g", {"id": "outline"})
    ET.SubElement(outline, "circle", {"cx": _num(cx), "cy": _num(cy), "r": _num(board.radius_mm)})

    centre = ET.SubElement(cut, "g", {"id": "centre"})
    arm = 10.0
    ET.SubElement(centre, "line", {"x1": _num(cx - arm), "y1": _num(cy), "x2": _num(cx + arm), "y2": _num(cy)})
    ET.SubElement(centre, "line", {"x1": _num(cx), "y1": _num(cy - arm), "x2": _num(cx), "y2": _num(cy + arm)})

    if variant == "holes":
        r = _hole_radius(board, hole_diameter_mm)
        pins = ET.SubElement(cut, "g", {"id": "pins"})
        for x, y in positions:
            ET.SubElement(pins, "circle", {"cx": _num(cx + float(x)), "cy": _num(cy + float(y)), "r": _num(r)})
    else:
        ET.SubElement(outline, "circle", {
            "cx": _num(cx), "cy": _num(cy), "r": _num(board.pin_radius_mm - RING_WIDTH_INSIDE_PINS_MM),
        })
        notches = ET.SubElement(cut, "g", {"id": "notches"})
        for (x, y), a in zip(positions, angles):
            ET.SubElement(notches, "rect", {
                "x": _num(-NOTCH_W_MM / 2.0),
                "y": _num(-NOTCH_H_MM / 2.0),
                "width": _num(NOTCH_W_MM),
                "height": _num(NOTCH_H_MM),
                "transform": f"translate({_num(cx + float(x))} {_num(cy + float(y))}) rotate({_num(math.degrees(float(a)))})",
            })

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# DXF --------------------------------------------------------------------------------------------


def _dxf_pairs(pairs: Iterable[tuple[int, object]]) -> str:
    lines = []
    for code, value in pairs:
        lines.append(f"{code:3d}")
        lines.append(_num(value) if isinstance(value, float) else str(value))
    return "\n".join(lines) + "\n"


def _dxf_circle(layer: str, x: float, y: float, r: float) -> str:
    return _dxf_pairs([
        (0, "CIRCLE"), (8, layer), (10, x), (20, y), (30, 0.0), (40, r),
    ])


def _dxf_line(layer: str, x1: float, y1: float, x2: float, y2: float) -> str:
    return _dxf_pairs([
        (0, "LINE"), (8, layer), (10, x1), (20, y1), (30, 0.0), (11, x2), (21, y2), (31, 0.0),
    ])


def _dxf_header(half: float) -> str:
    """HEADER section: R12 version, mm units and the drawing extents (a square of side 2 * half)."""
    return _dxf_pairs([
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1009"),
        (9, "$INSUNITS"), (70, 4),
        (9, "$EXTMIN"), (10, -half), (20, -half), (30, 0.0),
        (9, "$EXTMAX"), (10, half), (20, half), (30, 0.0),
        (0, "ENDSEC"),
    ])


def _dxf_tables(layers: list[tuple[str, int]]) -> str:
    pairs: list[tuple[int, object]] = [
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "LTYPE"), (70, 1),
        (0, "LTYPE"), (2, "CONTINUOUS"), (70, 0), (3, "Solid line"), (72, 65), (73, 0), (40, 0.0),
        (0, "ENDTAB"),
        (0, "TABLE"), (2, "LAYER"), (70, len(layers)),
    ]
    for name, colour in layers:
        pairs += [(0, "LAYER"), (2, name), (70, 0), (62, colour), (6, "CONTINUOUS")]
    pairs += [(0, "ENDTAB"), (0, "ENDSEC")]
    return _dxf_pairs(pairs)


def fab_dxf(board: BoardConfig, hole_diameter_mm: float | None = None, variant: str = "holes") -> bytes:
    """Minimal ASCII DXF R12 with the outline on layer OUTLINE and holes or notches on layer PINS."""
    variant = _check_variant(variant)
    positions = board.pin_positions_mm()
    angles = board.pin_angles()
    half = board.radius_mm

    entities = [_dxf_circle("OUTLINE", 0.0, 0.0, board.radius_mm)]
    arm = 10.0
    entities.append(_dxf_line("OUTLINE", -arm, 0.0, arm, 0.0))
    entities.append(_dxf_line("OUTLINE", 0.0, -arm, 0.0, arm))

    if variant == "holes":
        r = _hole_radius(board, hole_diameter_mm)
        for x, y in positions:
            entities.append(_dxf_circle("PINS", float(x), -float(y), r))
    else:
        entities.append(_dxf_circle("OUTLINE", 0.0, 0.0, board.pin_radius_mm - RING_WIDTH_INSIDE_PINS_MM))
        for (x, y), a in zip(positions, angles):
            entities.append(_dxf_notch("PINS", float(x), -float(y), float(a)))

    body = (
        _dxf_header(half)
        + _dxf_tables([("OUTLINE", 7), ("PINS", 1)])
        + _dxf_pairs([(0, "SECTION"), (2, "ENTITIES")])
        + "".join(entities)
        + _dxf_pairs([(0, "ENDSEC"), (0, "EOF")])
    )
    return body.encode("ascii")


def _dxf_notch(layer: str, x: float, y: float, angle: float) -> str:
    """Four LINE entities forming a NOTCH_W x NOTCH_H rectangle centred on (x, y), long side radial.

    angle is the pin angle in radians, clockwise from twelve o'clock. In DXF
    (y up) the radial unit vector of that pin is (sin a, cos a).
    """
    ux, uy = math.sin(angle), math.cos(angle)      # radial
    vx, vy = -uy, ux                                 # tangential
    hw, hh = NOTCH_W_MM / 2.0, NOTCH_H_MM / 2.0
    corners = [
        (x + vx * hw + ux * hh, y + vy * hw + uy * hh),
        (x - vx * hw + ux * hh, y - vy * hw + uy * hh),
        (x - vx * hw - ux * hh, y - vy * hw - uy * hh),
        (x + vx * hw - ux * hh, y + vy * hw - uy * hh),
    ]
    out = []
    for i in range(4):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % 4]
        out.append(_dxf_line(layer, x1, y1, x2, y2))
    return "".join(out)

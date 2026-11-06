"""3D-printable drilling jig for the pin ring (ADR-021).

A ring of keyed segments, one per sector (split further if a sector will not fit the printer's
bed), lies on the board's face with a skirt over its edge. Every pin has a guide hole for the drill
bit, so the pilot holes land exactly where the plan expects the pins and go in straight; pins are
then pressed and glued in rather than hammered. A height block sets every pin to the same height,
and a small test strip checks the guide-hole fit before the ring is printed.

Pin positions come from BoardConfig.pin_positions_mm (uneven spacing included), the same source
the template PDF, the fab files and the engines use. The jig depends only on the board settings,
so it fits every plan made with them.

Coordinates while modelling ("installed" frame): x to the right and y up as seen from the front
of the board, pin 0 near the top, z = 0 on the board's face and the plate above it. Each part is
then turned over for printing (the face that touches nothing, the top, goes on the bed), which is
a rotation, so the hole pattern is never mirrored.
"""
from __future__ import annotations

import io
import math
import struct
import zipfile
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from manifold3d import CrossSection, FillRule, Manifold

from stringart.board import BoardConfig


@dataclass
class JigSettings:
    bed_mm: float = 225.0          # square bed side (Elegoo Neptune 4 Pro: 225)
    bit_mm: float = 1.0            # drill bit for the pilot holes
    hole_clearance_mm: float = 0.2 # guide hole = bit + this (printed holes come out small)
    plate_mm: float = 6.0          # plate thickness: the length of drill guidance
    skirt_mm: float = 10.0         # how far the skirt reaches down the board's edge
    wall_mm: float = 3.0           # skirt wall thickness
    edge_clearance_mm: float = 0.4 # gap between the skirt and the board's edge
    inner_width_mm: float = 20.0   # plate extends this far inside the pin circle
    margin_mm: float = 5.0         # kept clear at the edges of the bed
    gauge_height_mm: float = 25.0  # pin height above the board for the height block
    skirt: bool = True             # off for a board that is not quite round: centre the ring by eye

    def validate(self) -> None:
        self.bed_mm = float(min(max(self.bed_mm, 120.0), 600.0))
        self.bit_mm = float(min(max(self.bit_mm, 0.5), 4.0))
        self.hole_clearance_mm = float(min(max(self.hole_clearance_mm, -0.2), 1.0))
        self.gauge_height_mm = float(min(max(self.gauge_height_mm, 3.0), 60.0))
        self.edge_clearance_mm = float(min(max(self.edge_clearance_mm, 0.0), 5.0))


def _polar(r: float, phi: float) -> np.ndarray:
    return np.array([r * math.sin(phi), r * math.cos(phi)])


def _tangent(phi: float) -> np.ndarray:
    """Unit vector in the direction of increasing angle (clockwise on the board's face)."""
    return np.array([math.cos(phi), -math.sin(phi)])


def _cs(polys: list[np.ndarray]) -> CrossSection:
    return CrossSection([np.asarray(p, dtype=np.float64) for p in polys], FillRule.EvenOdd)


def _annular_sector(r0: float, r1: float, phi0: float, phi1: float) -> CrossSection:
    steps = max(8, int(math.ceil((phi1 - phi0) / math.radians(0.5))))
    phis = np.linspace(phi0, phi1, steps + 1)
    outer = [_polar(r1, p) for p in phis]
    inner = [_polar(r0, p) for p in phis[::-1]]
    return _cs([np.array(outer + inner)])


def _dovetail(phi: float, rc: float, neck: float, head: float, length: float) -> CrossSection:
    """A dovetail that starts 0.6 mm behind the radial line at phi and widens as it reaches
    `length` mm past it, in the direction of increasing angle."""
    t = _tangent(phi)

    def q(r: float, s: float) -> np.ndarray:
        return _polar(r, phi) + s * t

    pts = [q(rc - neck, -0.6), q(rc + neck, -0.6), q(rc + neck, 0.0), q(rc + head, length),
           q(rc - head, length), q(rc - neck, 0.0)]
    return _cs([np.array(pts)])


def _text(label: str, height_mm: float) -> list[np.ndarray]:
    """Polygons of `label` in mm, centred on the origin, x to the right and y up."""
    scale, thick = 3.0, 7
    (w, h), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    pad = 20
    img = np.zeros((h + base + 2 * pad, w + 2 * pad), dtype=np.uint8)
    cv2.putText(img, label, (pad, pad + h), cv2.FONT_HERSHEY_SIMPLEX, scale, 255, thick, cv2.LINE_AA)
    _, img = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(img, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    mm_per_px = height_mm / max(h, 1)
    cx, cy = pad + w / 2.0, pad + h / 2.0
    polys = []
    for c in contours:
        c = cv2.approxPolyDP(c, 0.8, True).reshape(-1, 2).astype(np.float64)
        if len(c) < 3:
            continue
        polys.append(np.stack([(c[:, 0] - cx) * mm_per_px, -(c[:, 1] - cy) * mm_per_px], axis=1))
    return polys


def _place(polys: list[np.ndarray], centre: np.ndarray, xaxis: np.ndarray, yaxis: np.ndarray) -> list[np.ndarray]:
    return [centre + p[:, :1] * xaxis + p[:, 1:2] * yaxis for p in polys]


def segment_groups(board: BoardConfig, bed_mm: float, settings: JigSettings) -> list[dict[str, Any]]:
    """Contiguous pin groups, one per sector, split evenly until every segment fits the bed."""
    n = board.pin_count
    per = n / board.sector_count
    sectors: list[list[int]] = [[] for _ in range(board.sector_count)]
    for i in range(n):
        sectors[min(int(i // per), board.sector_count - 1)].append(i)
    for parts in range(1, 9):
        groups = []
        for s, pins in enumerate(sectors):
            letter = chr(ord("A") + s)
            chunks = np.array_split(np.array(pins), parts)
            for k, chunk in enumerate(chunks):
                if len(chunk):
                    groups.append({"label": letter if parts == 1 else f"{letter}{k + 1}", "pins": [int(p) for p in chunk]})
        spans = _spans(board, groups)
        if all(_fits(board, span, bed_mm, settings) for span in spans):
            for g, span in zip(groups, spans):
                g["phi0"], g["phi1"] = span
            return groups
    raise ValueError("the pin ring does not fit this bed even in 64 segments")


def _spans(board: BoardConfig, groups: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Boundary angles halfway between the last pin of one group and the first of the next."""
    a = board.pin_angles()
    two_pi = 2.0 * math.pi
    bounds = []
    for k, g in enumerate(groups):
        prev_last = groups[k - 1]["pins"][-1]
        first = g["pins"][0]
        gap = (a[first] - a[prev_last]) % two_pi
        bounds.append(a[prev_last] + gap / 2.0)
    spans = []
    for k in range(len(groups)):
        p0 = bounds[k]
        p1 = bounds[(k + 1) % len(groups)]
        p1 = p0 + ((p1 - p0) % two_pi)
        spans.append((p0, p1))
    return spans


def _radii(board: BoardConfig, s: JigSettings) -> dict[str, float]:
    rb = board.diameter_mm / 2.0
    rp = board.pin_radius_mm
    return {"rb": rb, "rp": rp, "ri": rp - s.inner_width_mm, "skirt_in": rb + s.edge_clearance_mm,
            "ro": rb + s.edge_clearance_mm + s.wall_mm, "key_rc": (rp - s.inner_width_mm + rp - 6.0) / 2.0}


def _fits(board: BoardConfig, span: tuple[float, float], bed_mm: float, s: JigSettings) -> bool:
    rad = _radii(board, s)
    phis = np.linspace(span[0] - math.radians(1.5), span[1] + math.radians(1.5), 60)
    pts = np.array([_polar(rad["ro"], p) for p in phis] + [_polar(rad["ri"], p) for p in phis])
    return _min_extent(pts)[0] <= bed_mm - 2.0 * s.margin_mm


def _min_extent(pts: np.ndarray) -> tuple[float, float]:
    """Smallest square side that holds pts over in-plane rotations (1 degree steps), and the angle."""
    best = (float("inf"), 0.0)
    for deg in range(0, 180):
        th = math.radians(deg)
        c, s_ = math.cos(th), math.sin(th)
        x = pts[:, 0] * c - pts[:, 1] * s_
        y = pts[:, 0] * s_ + pts[:, 1] * c
        side = max(x.max() - x.min(), y.max() - y.min())
        if side < best[0]:
            best = (side, float(deg))
    return best


def segment_solid(board: BoardConfig, group: dict[str, Any], s: JigSettings) -> Manifold:
    """One segment in the installed frame."""
    rad = _radii(board, s)
    phi0, phi1 = group["phi0"], group["phi1"]
    pos = board.pin_positions_mm()
    plate = _annular_sector(rad["ri"], rad["ro"], phi0, phi1)
    neck, head, length, gap = 2.8, 4.5, 7.0, 0.15
    plate = plate + _dovetail(phi1, rad["key_rc"], neck, head, length)
    plate = plate - _dovetail(phi0, rad["key_rc"], neck + gap, head + gap, length + gap)
    hole_r = (s.bit_mm + s.hole_clearance_mm) / 2.0
    for p in group["pins"]:
        x, y = pos[p][0], -pos[p][1]           # board.py has y pointing down; here y is up
        plate = plate - CrossSection.circle(hole_r, 28).translate([float(x), float(y)])
    solid = plate.extrude(s.plate_mm)
    if s.skirt:
        skirt = _annular_sector(rad["skirt_in"], rad["ro"], phi0, phi1).extrude(s.skirt_mm + 0.2).translate([0, 0, -s.skirt_mm])
        solid = solid + skirt
    # Label debossed into the top face: letter and pin range, upright when read from outside.
    phim = (phi0 + phi1) / 2.0
    label = f"{group['label']} {group['pins'][0]}-{group['pins'][-1]}"
    xaxis, yaxis = -_tangent(phim), -_polar(1.0, phim)
    centre = _polar(rad["key_rc"], phim)
    text = _cs(_place(_text(label, 5.0), centre, xaxis, yaxis))
    solid = solid - text.extrude(1.2).translate([0, 0, s.plate_mm - 0.7])
    return solid


def for_print(solid: Manifold, bed_mm: float) -> Manifold:
    """Turn a part over (top face on the bed), rotate it to fit the bed and centre it on the bed."""
    m = solid.rotate([180.0, 0.0, 0.0])
    mesh = m.to_mesh()
    pts = np.asarray(mesh.vert_properties)[:, :2]
    _, deg = _min_extent(pts)
    m = m.rotate([0.0, 0.0, deg])
    x0, y0, z0, x1, y1, _ = m.bounding_box()
    return m.translate([bed_mm / 2.0 - (x0 + x1) / 2.0, bed_mm / 2.0 - (y0 + y1) / 2.0, -z0])


def height_gauge(gauge_height_mm: float, slot_mm: float = 1.9) -> Manifold:
    """A block as tall as the pins should stand; slide its slot round a pin and press the pin
    down until its top is flush with the block."""
    body = CrossSection.square([34.0, 20.0]) - CrossSection.square([slot_mm, 11.0]).translate([17.0 - slot_mm / 2.0, 0.0])
    m = body.extrude(gauge_height_mm)
    label = _cs(_place(_text(f"{gauge_height_mm:.0f}", 6.0), np.array([17.0, 14.5]), np.array([1.0, 0.0]), np.array([0.0, 1.0])))
    return m - label.extrude(1.2).translate([0, 0, gauge_height_mm - 0.7])


def hole_test(bit_mm: float) -> tuple[Manifold, list[float]]:
    """A strip with guide holes at bit + 0.0, 0.1, ... 0.4 mm, numbered 0 to 4 beneath."""
    clearances = [0.0, 0.1, 0.2, 0.3, 0.4]
    strip = CrossSection.square([80.0, 22.0])
    for k, c in enumerate(clearances):
        strip = strip - CrossSection.circle((bit_mm + c) / 2.0, 28).translate([10.0 + 15.0 * k, 15.0])
    m = strip.extrude(6.0)
    for k in range(len(clearances)):
        digit = _cs(_place(_text(str(k), 5.0), np.array([10.0 + 15.0 * k, 6.0]), np.array([1.0, 0.0]), np.array([0.0, 1.0])))
        m = m - digit.extrude(1.2).translate([0, 0, 5.3])
    return m, clearances


def stl_bytes(m: Manifold) -> bytes:
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float32)[:, :3]
    t = np.asarray(mesh.tri_verts, dtype=np.int64)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    nrm = np.cross(b - a, c - a)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    out = io.BytesIO()
    out.write(b"String Art Studio jig".ljust(80, b" "))
    out.write(struct.pack("<I", len(t)))
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3), ("c", "<f4", 3), ("attr", "<u2")])
    rec["n"], rec["a"], rec["b"], rec["c"] = nrm, a, b, c
    out.write(rec.tobytes())
    return out.getvalue()


README = """String Art Studio drilling jig
==============================

Board: {diameter:.1f} mm, {pins} pins, inset {inset:.0f} mm, uneven spacing {jitter:.2f}. These parts fit
every plan made with exactly these board settings. Change the board and you need a new jig.

Print (PLA is fine: the jig only guides the drill, it never carries thread):
  0.2 mm layers, 4 walls, 20% infill, no supports. Each file is already placed on a {bed:.0f} mm bed
  with the face that will sit on the board facing up.

1. Print hole_test.stl first. Push your {bit:.1f} mm drill bit through the five holes, numbered 0 to 4.
   Pick the smallest hole the bit turns in freely without wobble. Its number is the clearance in
   tenths of a millimetre. If it is not 2, change "Guide hole clearance" on the results page to that
   number divided by 10 and download the jig again. This guide was made with {clear:.1f} mm.
2. Print the segments: {segments}.
   Each is debossed with its letter and the pins it holds.
3. Lay them on the board's face clockwise as seen from the front, starting with A at twelve o'clock
   (pin 0). {skirt_line} Each segment's dovetail locks into the next. Tape or clamp the ring
   down so it cannot move.
4. Drill every pilot hole through the guide, straight down, to {embed:.0f} mm deep (a stop collar or a tape
   flag on the bit). Drill slightly under the pin diameter so the pins press in tight.
5. Lift the jig off. Press each pin in with a drop of thin CA glue or epoxy. Slide the slot of
   height_gauge_{height:.0f}mm.stl round each pin and press it down until its top is flush with the block.
6. Let the glue cure for a day. Before the real build, test one offcut pin: wrap it 80 times under
   normal tension and leave it overnight.
"""


def jig_zip(board: BoardConfig, settings: JigSettings | None = None, embed_mm: float = 14.0) -> bytes:
    s = settings or JigSettings()
    s.validate()
    groups = segment_groups(board, s.bed_mm, s)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for g in groups:
            zf.writestr(f"guide_{g['label']}.stl", stl_bytes(for_print(segment_solid(board, g, s), s.bed_mm)))
        zf.writestr(f"height_gauge_{s.gauge_height_mm:.0f}mm.stl", stl_bytes(for_print_upright(height_gauge(s.gauge_height_mm), s.bed_mm)))
        test, _ = hole_test(s.bit_mm)
        zf.writestr("hole_test.stl", stl_bytes(for_print_upright(test, s.bed_mm)))
        zf.writestr("README.txt", README.format(
            diameter=board.diameter_mm, pins=board.pin_count, inset=board.pin_inset_mm, jitter=board.pin_jitter,
            bed=s.bed_mm, bit=s.bit_mm, clear=s.hole_clearance_mm, embed=embed_mm, height=s.gauge_height_mm,
            skirt_line=(f"The skirt goes over the board's edge ({s.edge_clearance_mm:.1f} mm clearance for a "
                        f"{board.diameter_mm:.1f} mm board)." if s.skirt else
                        "There is no skirt: centre the ring by eye, its outer edge flush with the board's edge."),
            segments=", ".join(f"guide_{g['label']}.stl" for g in groups)))
    return buf.getvalue()


def for_print_upright(m: Manifold, bed_mm: float) -> Manifold:
    """Small parts print the way they were modelled: bottom face on the bed, centred."""
    x0, y0, z0, x1, y1, _ = m.bounding_box()
    return m.translate([bed_mm / 2.0 - (x0 + x1) / 2.0, bed_mm / 2.0 - (y0 + y1) / 2.0, -z0])

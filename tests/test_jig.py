"""3D-print drilling jig (ADR-021): one guide hole exactly over every pin, parts that fit the bed,
dovetails that mate, and a ZIP with everything the README promises."""
from __future__ import annotations

import io
import math
import struct
import zipfile

import pytest

from stringart.board import BoardConfig
from stringart.exports import jig


@pytest.fixture(scope="module")
def board() -> BoardConfig:
    return BoardConfig.from_dict(BoardConfig().to_dict())      # 610 mm, 240 pins, uneven spacing


def test_every_pin_has_a_guide_hole_and_nothing_else_is_a_hole(board):
    s = jig.JigSettings(bed_mm=225.0)
    groups = jig.segment_groups(board, s.bed_mm, s)
    assert [g["label"] for g in groups] == list("ABCDEFGH")
    assert sorted(p for g in groups for p in g["pins"]) == list(range(board.pin_count))
    pos = board.pin_positions_mm()
    for g in groups[:3]:
        m = jig.segment_solid(board, g, s)
        assert str(m.status()).endswith("NoError")
        assert m.genus() == len(g["pins"])          # each through hole adds one to the genus
        for p in g["pins"]:
            x, y = pos[p][0], -pos[p][1]
            assert not m.ray_cast([x, y, -0.5], [x, y, s.plate_mm + 0.5]), f"pin {p} is not over a hole"
            r = math.hypot(x, y)
            x2, y2 = x * (r + 2.0) / r, y * (r + 2.0) / r
            assert m.ray_cast([x2, y2, -0.5], [x2, y2, s.plate_mm + 0.5]), f"no plate beside pin {p}"


def test_segments_fit_the_bed_and_mate(board):
    s = jig.JigSettings(bed_mm=225.0)
    groups = jig.segment_groups(board, s.bed_mm, s)
    a = jig.segment_solid(board, groups[0], s)
    b = jig.segment_solid(board, groups[1], s)
    assert (a ^ b).volume() < 1e-3                   # tongue sits in the socket without overlap
    x0, y0, z0, x1, y1, z1 = jig.for_print(a, s.bed_mm).bounding_box()
    assert x0 >= 0 and y0 >= 0 and z0 == pytest.approx(0.0) and x1 <= 225 and y1 <= 225
    # A small bed splits sectors further rather than failing.
    small = jig.segment_groups(board, 150.0, jig.JigSettings(bed_mm=150.0))
    assert len(small) > 8 and small[0]["label"] == "A1"


def test_zip_contents_and_stl_header(board):
    data = jig.jig_zip(board, jig.JigSettings(bed_mm=225.0, gauge_height_mm=27, skirt=False), embed_mm=14)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "README.txt" in names and "hole_test.stl" in names and "height_gauge_27mm.stl" in names
    assert sum(n.startswith("guide_") for n in names) == 8
    stl = zf.read("guide_A.stl")
    (count,) = struct.unpack("<I", stl[80:84])
    assert len(stl) == 84 + 50 * count and count > 1000
    readme = zf.read("README.txt").decode("utf-8")
    assert "no skirt" in readme and "14 mm deep" in readme

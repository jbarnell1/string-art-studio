"""Tests for stringart.exports: PDF template, fabrication files, sequence exports and bundles."""
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from stringart.board import BoardConfig
from stringart.exports import (
    fab_dxf,
    fab_svg,
    pin_template_pdf,
    read_bundle,
    sequence_csv,
    sequence_json,
    sequence_txt,
    steps_table,
    write_bundle,
)
from stringart.exports.bundle import BundleError

MM_TO_PT = 72.0 / 25.4
SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture
def board() -> BoardConfig:
    return BoardConfig.from_dict({"diameter_mm": 610.0, "pin_count": 240, "sector_count": 8})


@pytest.fixture
def small_board() -> BoardConfig:
    return BoardConfig.from_dict({"diameter_mm": 300.0, "pin_count": 120, "sector_count": 4})


@pytest.fixture
def sequence() -> list[int]:
    return [0, 60, 125, 17, 203, 90, 150, 33]


# PDF ----------------------------------------------------------------------------------------------


def _page_count(pdf: bytes) -> int:
    # Page objects are "/Type /Page"; the page tree root is "/Type /Pages".
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf))


def _media_boxes(pdf: bytes) -> list[tuple[float, float]]:
    boxes = []
    for match in re.finditer(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", pdf):
        x0, y0, x1, y1 = (float(v) for v in match.groups())
        boxes.append((x1 - x0, y1 - y0))
    return boxes


def test_pdf_letter_tiles_into_many_pages(board):
    pdf = pin_template_pdf(board, paper="letter")
    assert pdf.startswith(b"%PDF")
    assert _page_count(pdf) > 4
    widths = {round(w, 1) for w, _ in _media_boxes(pdf)}
    assert widths == {round(215.9 * MM_TO_PT, 1)}


def test_pdf_a4_tiles(board):
    pdf = pin_template_pdf(board, paper="a4")
    assert pdf.startswith(b"%PDF")
    assert _page_count(pdf) > 4


def test_pdf_single_page_is_true_size(board):
    pdf = pin_template_pdf(board, paper="single")
    assert pdf.startswith(b"%PDF")
    assert _page_count(pdf) == 1
    boxes = _media_boxes(pdf)
    assert boxes, "no MediaBox found"
    width_pt, height_pt = boxes[0]
    assert width_pt >= 640.0 * MM_TO_PT - 0.01
    assert height_pt >= 640.0 * MM_TO_PT - 0.01


def test_pdf_without_labels_and_small_board(small_board):
    pdf = pin_template_pdf(small_board, paper="single", pin_labels=False)
    assert pdf.startswith(b"%PDF")


def test_pdf_rejects_unknown_paper(board):
    with pytest.raises(ValueError):
        pin_template_pdf(board, paper="tabloid")


# SVG and DXF --------------------------------------------------------------------------------------


def test_svg_has_a_hole_per_pin(board):
    svg = fab_svg(board)
    root = ET.fromstring(svg)
    assert root.tag == SVG_NS + "svg"
    assert root.get("width") == "610mm"
    assert root.get("viewBox") == "0 0 610 610"
    pins = root.find(f".//{SVG_NS}g[@id='pins']")
    assert pins is not None
    circles = pins.findall(SVG_NS + "circle")
    assert len(circles) == board.pin_count
    assert circles[0].get("r") == "0.6"
    cut = root.find(f".//{SVG_NS}g[@id='cut']")
    assert cut.get("fill") == "none"


def test_svg_custom_hole_and_ring_variant(board):
    svg = fab_svg(board, hole_diameter_mm=3.0)
    root = ET.fromstring(svg)
    circles = root.find(f".//{SVG_NS}g[@id='pins']").findall(SVG_NS + "circle")
    assert circles[0].get("r") == "1.5"

    ring = ET.fromstring(fab_svg(board, variant="ring"))
    notches = ring.find(f".//{SVG_NS}g[@id='notches']")
    assert notches is not None
    assert len(notches.findall(SVG_NS + "rect")) == board.pin_count
    outline = ring.find(f".//{SVG_NS}g[@id='outline']")
    assert len(outline.findall(SVG_NS + "circle")) == 2

    with pytest.raises(ValueError):
        fab_svg(board, variant="square")


def _dxf_entities(dxf: bytes) -> list[tuple[str, str]]:
    """(entity type, layer) for each entity in the ENTITIES section."""
    lines = dxf.decode("ascii").splitlines()
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    entities = []
    in_entities = False
    current = None
    for code, value in pairs:
        if code == "2" and value == "ENTITIES":
            in_entities = True
            continue
        if not in_entities:
            continue
        if code == "0":
            if value == "ENDSEC":
                break
            current = [value, None]
            entities.append(current)
        elif code == "8" and current is not None:
            current[1] = value
    return [tuple(e) for e in entities]


def test_dxf_has_a_circle_per_pin_and_ends_with_eof(board):
    dxf = fab_dxf(board)
    text = dxf.decode("ascii")
    assert text.rstrip().endswith("EOF")
    assert "AC1009" in text
    entities = _dxf_entities(dxf)
    pin_circles = [e for e in entities if e == ("CIRCLE", "PINS")]
    assert len(pin_circles) == board.pin_count
    assert ("CIRCLE", "OUTLINE") in entities
    assert sum(1 for e in entities if e == ("LINE", "OUTLINE")) == 2


def test_dxf_ring_variant_has_four_lines_per_notch(board):
    entities = _dxf_entities(fab_dxf(board, variant="ring"))
    assert sum(1 for e in entities if e == ("LINE", "PINS")) == 4 * board.pin_count
    assert sum(1 for e in entities if e == ("CIRCLE", "OUTLINE")) == 2


# Sequence -----------------------------------------------------------------------------------------


def test_steps_table(board, sequence):
    rows = steps_table(board, sequence)
    assert len(rows) == len(sequence)
    assert rows[0] == {"step": 1, "layer": 0, "color": board.thread_color, "color_name": "black", "pin": 0,
                       "label": "A1", "from_pin": None, "length_mm": 0.0}
    assert rows[1]["from_pin"] == 0
    assert rows[1]["length_mm"] == pytest.approx(board.chord_length_mm(0, 60), abs=0.001)


def test_sequence_txt(board, sequence):
    lines = sequence_txt(board, sequence).decode("utf-8").splitlines()
    assert len(lines) == len(sequence)
    assert lines[0] == "step 1: pin 0 (A1)"
    assert lines[3] == f"step 4: pin 17 ({board.sector_label(17)})"


def test_sequence_csv(board, sequence):
    rows = list(csv.reader(io.StringIO(sequence_csv(board, sequence).decode("utf-8"))))
    assert rows[0] == ["step", "layer", "color", "pin", "label", "from_pin", "length_mm"]
    assert len(rows) - 1 == len(sequence)
    assert rows[1][5] == ""
    assert rows[2][5] == "0"


def test_layers_exports(board, sequence):
    layers = [{"name": "red", "color": "#d9261c", "sequence": sequence[:3]},
              {"name": "black", "color": "#161616", "sequence": sequence[3:]}]
    lines = sequence_txt(board, layers).decode("utf-8").splitlines()
    assert lines[0].startswith("== red thread") and sum(1 for l in lines if l.startswith("==")) == 2
    rows = list(csv.reader(io.StringIO(sequence_csv(board, layers).decode("utf-8"))))
    assert rows[4][1] == "1" and rows[4][2] == "black" and rows[4][5] == ""
    data = json.loads(sequence_json(board, layers).decode("utf-8"))
    assert [l["name"] for l in data["layers"]] == ["red", "black"]
    assert data["sequence"] == sequence
    assert data["thread"]["length_mm"] == pytest.approx(board.sequence_length_mm(sequence[:3]) + board.sequence_length_mm(sequence[3:]))


def test_sequence_json_round_trips(board, sequence):
    data = json.loads(sequence_json(board, sequence).decode("utf-8"))
    assert data["sequence"] == sequence
    assert data["step_count"] == len(sequence)
    assert len(data["pins"]) == board.pin_count
    assert BoardConfig.from_dict(data["board"]).to_dict() == board.to_dict()
    assert data["thread"]["length_mm"] == pytest.approx(board.sequence_length_mm(sequence))
    assert len(data["steps"]) == len(sequence)


# Bundle -------------------------------------------------------------------------------------------


def test_bundle_round_trip(board, sequence):
    project = {
        "id": "proj-1",
        "name": "Test project",
        "prep": {"contrast": 1.2},
        "board": board.to_dict(),
        "engine": {"kind": "greedy"},
    }
    runs = [
        {"id": "run-a", "params": {"max_lines": 100}, "metrics": {"score": 0.5}, "sequence": sequence},
        {"params": {}, "metrics": {}, "sequence": [1, 2, 3]},
    ]
    source = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    mask = b"\x89PNG\r\n\x1a\nfake-mask-bytes"

    data = write_bundle(project, runs, source, mask, "png")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
    assert "project.json" in names
    assert "source.png" in names
    assert "mask.png" in names
    assert "sequences/run-a.json" in names
    assert "sequences/run2.json" in names

    result = read_bundle(data)
    assert result["project"]["name"] == "Test project"
    assert result["project"]["board"] == board.to_dict()
    assert result["source"] == source
    assert result["source_ext"] == "png"
    assert result["mask"] == mask
    assert len(result["runs"]) == 2
    assert result["runs"][0]["sequence"] == sequence
    assert result["runs"][0]["params"] == {"max_lines": 100}
    assert result["runs"][1]["sequence"] == [1, 2, 3]


def test_bundle_without_images(board):
    data = write_bundle({"name": "bare"}, [], None, None, "jpg")
    result = read_bundle(data)
    assert result["source"] is None
    assert result["mask"] is None
    assert result["runs"] == []
    assert result["source_ext"] == ""


def test_bundle_rejects_missing_project_json():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("notes.txt", "hello")
    with pytest.raises(BundleError):
        read_bundle(buffer.getvalue())


def test_bundle_rejects_path_traversal():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("project.json", "{}")
        zf.writestr("../x", "escape")
    with pytest.raises(BundleError):
        read_bundle(buffer.getvalue())


def test_bundle_rejects_absolute_path():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("project.json", "{}")
        zf.writestr("/etc/passwd", "root")
    with pytest.raises(BundleError):
        read_bundle(buffer.getvalue())


def test_bundle_rejects_non_zip():
    with pytest.raises(BundleError):
        read_bundle(b"this is not a zip")

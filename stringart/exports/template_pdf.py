"""True-size pin template as a PDF.

The whole template is drawn once by `_draw_template`, in millimetre units with
the origin at the bottom-left corner of the drawing. The single-page variant
puts that drawing on one large page. The letter and A4 variants tile the same
drawing across a grid of pages with an overlap, by translating and clipping the
canvas per tile, and add an index page in front that shows the page grid.
"""
from __future__ import annotations

import io
import math

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from stringart.board import BoardConfig

# Drawing layout, all in mm.
MARGIN_MM = 15.0          # white space around the board circle inside the drawing
RULER_LENGTH_MM = 100.0
RULER_TEXT = "Check: this bar is exactly 100 mm. Print at 100%, no scaling."

# Tiling layout, all in mm.
OVERLAP_MM = 12.0         # neighbouring tiles share this much drawing
PAGE_MARGIN_MM = 10.0     # unprintable border kept clear on every tiled page
PAPER_SIZES_MM = {
    "letter": (215.9, 279.4),
    "a4": (210.0, 297.0),
}

LABEL_EVERY_PIN_UP_TO = 150
LABEL_STRIDE = 5


def pin_template_pdf(board: BoardConfig, paper: str = "letter", pin_labels: bool = True) -> bytes:
    """Render the pin template for `board` and return the PDF bytes.

    paper is "letter", "a4" or "single". "single" produces one page sized to the
    board plus 30 mm of margin. The others tile the full-size drawing across
    pages with a 12 mm overlap and put an index page first.
    """
    paper = paper.lower()
    if paper == "single":
        return _single_page(board, pin_labels)
    if paper not in PAPER_SIZES_MM:
        raise ValueError(f"unknown paper {paper!r}, expected letter, a4 or single")
    return _tiled_pages(board, PAPER_SIZES_MM[paper], pin_labels)


# Drawing ---------------------------------------------------------------------------------------


def drawing_size_mm(board: BoardConfig) -> tuple[float, float]:
    """Width and height of the full template drawing."""
    side = board.diameter_mm + 2.0 * MARGIN_MM
    return side, side


def _fmt_mm(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def template_title(board: BoardConfig) -> str:
    return f"String Art Studio pin template, {_fmt_mm(board.diameter_mm)} mm, {board.pin_count} pins"


def _sector_starts(board: BoardConfig) -> set[int]:
    per = board.pin_count / board.sector_count
    return {int(round(s * per)) for s in range(board.sector_count)}


def _labelled_pins(board: BoardConfig) -> set[int]:
    if board.pin_count <= LABEL_EVERY_PIN_UP_TO:
        return set(range(board.pin_count))
    return set(range(0, board.pin_count, LABEL_STRIDE)) | _sector_starts(board)


def _draw_template(c: canvas.Canvas, board: BoardConfig, pin_labels: bool) -> None:
    """Draw the full template with the canvas already scaled to mm units.

    The drawing occupies [0, W] x [0, H] with the PDF convention of y pointing up.
    Board coordinates have y pointing down, so pin y values are negated.
    """
    width, height = drawing_size_mm(board)
    cx, cy = width / 2.0, height / 2.0
    radius = board.radius_mm
    pin_radius = board.pin_radius_mm
    positions = board.pin_positions_mm()

    # Title.
    c.setFont("Helvetica-Bold", 5)
    c.drawString(MARGIN_MM, height - 9.0, template_title(board))

    # Board outline.
    c.setLineWidth(0.3)
    c.circle(cx, cy, radius, stroke=1, fill=0)

    # Centre crosshair.
    c.setLineWidth(0.25)
    arm = 15.0
    c.line(cx - arm, cy, cx + arm, cy)
    c.line(cx, cy - arm, cx, cy + arm)
    c.circle(cx, cy, 3.0, stroke=1, fill=0)

    # Pins: a circle at the real pin diameter plus a dot marking the exact centre.
    pin_r = max(board.pin_diameter_mm / 2.0, 0.75)
    c.setLineWidth(0.2)
    for x, y in positions:
        px, py = cx + float(x), cy - float(y)
        c.circle(px, py, pin_r, stroke=1, fill=0)
        c.circle(px, py, 0.15, stroke=0, fill=1)

    if pin_labels:
        _draw_pin_numbers(c, board, cx, cy, pin_radius)
    _draw_sector_letters(c, board, cx, cy, pin_radius)

    # Calibration ruler in the bottom margin.
    _draw_ruler(c, MARGIN_MM, 8.0)


def _draw_pin_numbers(c: canvas.Canvas, board: BoardConfig, cx: float, cy: float, pin_radius: float) -> None:
    """Pin numbers inside the pin ring, each rotated to run along its radius."""
    font_size = 2.2
    inner = pin_radius - 2.5
    c.setFont("Helvetica", font_size)
    angles = board.pin_angles()
    for pin in sorted(_labelled_pins(board)):
        a = float(angles[pin])
        # Rotation of the local x axis so it points from the centre to the pin.
        theta = 90.0 - math.degrees(a)
        flipped = math.cos(math.radians(theta)) < 0
        c.saveState()
        c.translate(cx, cy)
        if flipped:
            c.rotate(theta + 180.0)
            c.drawString(-inner, -font_size * 0.35, str(pin))
        else:
            c.rotate(theta)
            c.drawRightString(inner, -font_size * 0.35, str(pin))
        c.restoreState()


def _draw_sector_letters(c: canvas.Canvas, board: BoardConfig, cx: float, cy: float, pin_radius: float) -> None:
    """Sector letter just outside the pin ring at each sector start."""
    font_size = 4.0
    outer = pin_radius + board.pin_inset_mm / 2.0
    c.setFont("Helvetica-Bold", font_size)
    angles = board.pin_angles()
    for pin in sorted(_sector_starts(board)):
        a = float(angles[pin])
        x = cx + outer * math.sin(a)
        y = cy + outer * math.cos(a)
        letter = board.sector_label(pin)[0]
        c.drawCentredString(x, y - font_size * 0.35, letter)


def _draw_ruler(c: canvas.Canvas, x0: float, y0: float) -> None:
    """A 100 mm bar with 10 mm ticks and the calibration note under it."""
    c.setLineWidth(0.4)
    c.line(x0, y0, x0 + RULER_LENGTH_MM, y0)
    for i in range(0, int(RULER_LENGTH_MM) + 1, 10):
        tick = 5.0 if i in (0, RULER_LENGTH_MM) else 3.0
        c.line(x0 + i, y0, x0 + i, y0 + tick)
    c.setFont("Helvetica", 2.5)
    c.drawString(x0, y0 + 6.0, "0")
    c.drawRightString(x0 + RULER_LENGTH_MM, y0 + 6.0, "100 mm")
    c.setFont("Helvetica", 3.0)
    c.drawString(x0, y0 - 4.0, RULER_TEXT)


# Pages -------------------------------------------------------------------------------------------


def _new_canvas(buffer: io.BytesIO, page_w_mm: float, page_h_mm: float, title: str) -> canvas.Canvas:
    c = canvas.Canvas(buffer, pagesize=(page_w_mm * mm, page_h_mm * mm))
    c.setTitle(title)
    c.setAuthor("String Art Studio")
    return c


def _single_page(board: BoardConfig, pin_labels: bool) -> bytes:
    width, height = drawing_size_mm(board)
    buffer = io.BytesIO()
    c = _new_canvas(buffer, width, height, template_title(board))
    c.scale(mm, mm)
    _draw_template(c, board, pin_labels)
    c.showPage()
    c.save()
    return buffer.getvalue()


def tile_grid(drawing_w: float, drawing_h: float, paper_mm: tuple[float, float]) -> dict:
    """Describe how the drawing tiles onto the given paper.

    Returns usable page area, the step between tile origins and the row and
    column counts. Tiles share OVERLAP_MM of drawing with their neighbours.
    """
    page_w, page_h = paper_mm
    usable_w = page_w - 2.0 * PAGE_MARGIN_MM
    usable_h = page_h - 2.0 * PAGE_MARGIN_MM
    step_x = usable_w - OVERLAP_MM
    step_y = usable_h - OVERLAP_MM
    cols = max(1, math.ceil((drawing_w - OVERLAP_MM) / step_x))
    rows = max(1, math.ceil((drawing_h - OVERLAP_MM) / step_y))
    return {
        "usable_w": usable_w,
        "usable_h": usable_h,
        "step_x": step_x,
        "step_y": step_y,
        "cols": cols,
        "rows": rows,
    }


def _tile_origin(grid: dict, drawing_h: float, row: int, col: int) -> tuple[float, float]:
    """Drawing-space coordinates of the bottom-left corner of a tile. Rows count from the top."""
    x = col * grid["step_x"]
    y = drawing_h - row * grid["step_y"] - grid["usable_h"]
    return x, y


def _tiled_pages(board: BoardConfig, paper_mm: tuple[float, float], pin_labels: bool) -> bytes:
    drawing_w, drawing_h = drawing_size_mm(board)
    grid = tile_grid(drawing_w, drawing_h, paper_mm)
    page_w, page_h = paper_mm
    title = template_title(board)

    buffer = io.BytesIO()
    c = _new_canvas(buffer, page_w, page_h, title)

    _draw_index_page(c, board, paper_mm, grid, pin_labels)
    c.showPage()

    for row in range(grid["rows"]):
        for col in range(grid["cols"]):
            _draw_tile_page(c, board, paper_mm, grid, row, col, pin_labels)
            c.showPage()

    c.save()
    return buffer.getvalue()


def _draw_tile_page(c: canvas.Canvas, board: BoardConfig, paper_mm: tuple[float, float], grid: dict,
                    row: int, col: int, pin_labels: bool) -> None:
    drawing_w, drawing_h = drawing_size_mm(board)
    origin_x, origin_y = _tile_origin(grid, drawing_h, row, col)
    usable_w, usable_h = grid["usable_w"], grid["usable_h"]

    c.saveState()
    c.scale(mm, mm)

    # Clip to the usable area and shift the drawing so this tile lands inside it.
    c.saveState()
    clip = c.beginPath()
    clip.rect(PAGE_MARGIN_MM, PAGE_MARGIN_MM, usable_w, usable_h)
    c.clipPath(clip, stroke=0, fill=0)
    c.translate(PAGE_MARGIN_MM - origin_x, PAGE_MARGIN_MM - origin_y)
    _draw_template(c, board, pin_labels)
    c.restoreState()

    _draw_tile_marks(c, grid, row, col)

    # Page label in the bottom margin.
    c.setFont("Helvetica", 3.0)
    label = f"Row {row + 1} of {grid['rows']}, column {col + 1} of {grid['cols']}"
    c.drawString(PAGE_MARGIN_MM, PAGE_MARGIN_MM / 2.0 - 1.0, label)
    c.drawRightString(PAGE_MARGIN_MM + usable_w, PAGE_MARGIN_MM / 2.0 - 1.0, template_title(board))
    c.restoreState()


def _draw_tile_marks(c: canvas.Canvas, grid: dict, row: int, col: int) -> None:
    """Registration circles at the usable corners and crop ticks at the overlap edges."""
    usable_w, usable_h = grid["usable_w"], grid["usable_h"]
    left, bottom = PAGE_MARGIN_MM, PAGE_MARGIN_MM
    right, top = left + usable_w, bottom + usable_h

    c.setLineWidth(0.2)
    for x, y in ((left, bottom), (right, bottom), (left, top), (right, top)):
        c.circle(x, y, 2.0, stroke=1, fill=0)
        c.line(x - 3.0, y, x + 3.0, y)
        c.line(x, y - 3.0, x, y + 3.0)

    # Each neighbouring tile shares OVERLAP_MM of drawing along the touching edge.
    # Short ticks at the inner boundary of that strip show where to align the next sheet.
    tick = 4.0
    c.setDash(1.0, 1.0)
    if col > 0:
        x = left + OVERLAP_MM
        c.line(x, bottom - tick, x, bottom)
        c.line(x, top, x, top + tick)
    if col < grid["cols"] - 1:
        x = right - OVERLAP_MM
        c.line(x, bottom - tick, x, bottom)
        c.line(x, top, x, top + tick)
    if row > 0:
        y = top - OVERLAP_MM
        c.line(left - tick, y, left, y)
        c.line(right, y, right + tick, y)
    if row < grid["rows"] - 1:
        y = bottom + OVERLAP_MM
        c.line(left - tick, y, left, y)
        c.line(right, y, right + tick, y)
    c.setDash()


def _draw_index_page(c: canvas.Canvas, board: BoardConfig, paper_mm: tuple[float, float], grid: dict,
                     pin_labels: bool) -> None:
    """Whole template scaled to fit one page, with the tile grid drawn over it."""
    drawing_w, drawing_h = drawing_size_mm(board)
    page_w, page_h = paper_mm
    usable_w, usable_h = grid["usable_w"], grid["usable_h"]

    c.saveState()
    c.scale(mm, mm)

    c.setFont("Helvetica-Bold", 5)
    c.drawString(PAGE_MARGIN_MM, page_h - PAGE_MARGIN_MM - 5.0, "Index page: " + template_title(board))
    c.setFont("Helvetica", 3.2)
    c.drawString(
        PAGE_MARGIN_MM,
        page_h - PAGE_MARGIN_MM - 11.0,
        f"{grid['rows']} rows x {grid['cols']} columns of pages, {_fmt_mm(OVERLAP_MM)} mm overlap. "
        "Print every page at 100%, then align the registration marks.",
    )

    header_h = 16.0
    box_w = usable_w
    box_h = usable_h - header_h
    scale = min(box_w / drawing_w, box_h / drawing_h)
    offset_x = PAGE_MARGIN_MM + (box_w - drawing_w * scale) / 2.0
    offset_y = PAGE_MARGIN_MM + (box_h - drawing_h * scale) / 2.0

    c.saveState()
    c.translate(offset_x, offset_y)
    c.scale(scale, scale)
    _draw_template(c, board, pin_labels)

    # Tile rectangles and labels, drawn in the same scaled space as the template.
    c.setLineWidth(0.4 / scale)
    c.setStrokeColorRGB(0.8, 0.1, 0.1)
    c.setFillColorRGB(0.8, 0.1, 0.1)
    c.setFont("Helvetica-Bold", 6.0 / scale)
    for row in range(grid["rows"]):
        for col in range(grid["cols"]):
            x, y = _tile_origin(grid, drawing_h, row, col)
            c.rect(x, y, grid["usable_w"], grid["usable_h"], stroke=1, fill=0)
            c.drawCentredString(x + grid["usable_w"] / 2.0, y + grid["usable_h"] / 2.0,
                                f"R{row + 1} C{col + 1}")
    c.restoreState()
    c.restoreState()

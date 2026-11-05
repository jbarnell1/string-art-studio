"""Board geometry: pins, chords, sectors and thread length. Everything is in millimetres."""
from __future__ import annotations

import math
import string
from dataclasses import dataclass, asdict, field
from typing import Any

import numpy as np

MM_PER_INCH = 25.4


@dataclass
class BoardConfig:
    shape: str = "circle"
    diameter_mm: float = 610.0
    pin_count: int = 240
    pin_inset_mm: float = 10.0
    pin_diameter_mm: float = 1.2
    thread_width_mm: float = 0.25
    thread_opacity: float = 1.0
    thread_color: str = "#161616"
    board_color: str = "#f6f3ee"
    invert: bool = False
    min_skip: int = 15
    max_lines: int = 8000        # a ceiling: the engines stop earlier when no line still helps
    viewing_distance_m: float = 1.5
    blur_mm_per_m: float = 1.5
    sector_count: int = 8
    work_res: int = 800
    # Colour (v3): thread colours as hex. Empty or one entry means a single thread in thread_color.
    # palette_auto >= 2 picks that many colours from the photo (always including the darkest).
    palette: list = field(default_factory=list)
    palette_auto: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BoardConfig":
        data = dict(data or {})
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        cfg = cls(**known)
        cfg.validate()
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        self.shape = "circle"  # square and rectangle come later
        self.diameter_mm = float(min(max(self.diameter_mm, 200.0), 1000.0))
        self.pin_count = int(min(max(self.pin_count, 100), 400))
        self.pin_inset_mm = float(min(max(self.pin_inset_mm, 5.0), 30.0))
        self.pin_diameter_mm = float(min(max(self.pin_diameter_mm, 0.5), 5.0))
        self.thread_width_mm = float(min(max(self.thread_width_mm, 0.05), 2.0))
        self.thread_opacity = float(min(max(self.thread_opacity, 0.2), 3.0))
        self.min_skip = int(min(max(self.min_skip, 1), self.pin_count // 2 - 1))
        self.max_lines = int(min(max(self.max_lines, 10), 20000))
        self.viewing_distance_m = float(min(max(self.viewing_distance_m, 0.3), 10.0))
        self.blur_mm_per_m = float(min(max(self.blur_mm_per_m, 0.2), 6.0))
        self.sector_count = int(min(max(self.sector_count, 1), 26))
        self.work_res = int(min(max(self.work_res, 300), 2400))
        self.thread_color = normalize_hex(self.thread_color, "#161616")
        self.board_color = normalize_hex(self.board_color, "#f6f3ee")
        clean = []
        for c in list(self.palette or [])[:6]:
            h = normalize_hex(str(c), "")
            if h and h not in clean:
                clean.append(h)
        self.palette = clean
        self.palette_auto = int(min(max(int(self.palette_auto or 0), 0), 6))
        if self.palette_auto == 1:
            self.palette_auto = 0

    @property
    def is_color(self) -> bool:
        return self.palette_auto >= 2 or len(self.palette) >= 2

    # Geometry -------------------------------------------------------------------------------

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm / 2.0

    @property
    def pin_radius_mm(self) -> float:
        return self.radius_mm - self.pin_inset_mm

    def pin_angles(self) -> np.ndarray:
        """Angle of each pin in radians, pin 0 at twelve o'clock, increasing clockwise."""
        return np.arange(self.pin_count) * (2.0 * math.pi / self.pin_count)

    def pin_positions_mm(self) -> np.ndarray:
        """(n, 2) array of x, y in mm with the board centre at the origin and y pointing down."""
        a = self.pin_angles()
        r = self.pin_radius_mm
        return np.stack([r * np.sin(a), -r * np.cos(a)], axis=1)

    def pin_positions_px(self, size_px: int) -> np.ndarray:
        """Pins scaled onto a size_px square canvas where the board diameter fills the canvas."""
        scale = size_px / self.diameter_mm
        return self.pin_positions_mm() * scale + (size_px / 2.0)

    def pixel_mm(self, size_px: int) -> float:
        return self.diameter_mm / size_px

    def circular_distance(self, i: int, j: np.ndarray | int) -> np.ndarray | int:
        d = np.abs(np.asarray(j) - i) % self.pin_count
        return np.minimum(d, self.pin_count - d)

    def valid_targets(self, i: int) -> np.ndarray:
        """Pins a thread may go to from pin i (respecting the minimum skip)."""
        js = np.arange(self.pin_count)
        return js[self.circular_distance(i, js) >= self.min_skip]

    def chord_valid(self, i: int, j: int) -> bool:
        return i != j and int(self.circular_distance(i, j)) >= self.min_skip

    def chord_length_mm(self, i: int, j: int) -> float:
        p = self.pin_positions_mm()
        return float(np.hypot(*(p[i] - p[j])))

    def sequence_length_mm(self, sequence: list[int]) -> float:
        if len(sequence) < 2:
            return 0.0
        p = self.pin_positions_mm()
        seq = np.asarray(sequence)
        return float(np.hypot(*(p[seq[1:]] - p[seq[:-1]]).T).sum())

    # Labels ----------------------------------------------------------------------------------

    def sector_label(self, pin: int) -> str:
        """Human label like "C14": sector letter plus 1-based index within the sector."""
        per = self.pin_count / self.sector_count
        sector = min(int(pin // per), self.sector_count - 1)
        start = int(round(sector * per))
        return f"{string.ascii_uppercase[sector]}{pin - start + 1}"

    def pin_table(self) -> list[dict[str, Any]]:
        pos = self.pin_positions_mm()
        return [
            {"pin": i, "label": self.sector_label(i), "x_mm": round(float(x), 3), "y_mm": round(float(y), 3)}
            for i, (x, y) in enumerate(pos)
        ]


@dataclass
class ThreadEstimate:
    length_mm: float
    with_margin_mm: float
    spools: int
    spool_length_mm: float
    margin: float = 0.2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def thread_estimate(board: BoardConfig, sequence: list[int], spool_yards: float = 1000.0,
                    margin: float = 0.2) -> ThreadEstimate:
    length = board.sequence_length_mm(sequence)
    with_margin = length * (1.0 + margin)
    spool_mm = spool_yards * 914.4
    spools = int(math.ceil(with_margin / spool_mm)) if with_margin > 0 else 0
    return ThreadEstimate(length, with_margin, spools, spool_mm, margin)


def normalize_hex(value: str, default: str) -> str:
    """'#rgb' or '#rrggbb' in any case to lower-case '#rrggbb'; default when it does not parse."""
    v = (value or "").strip().lstrip("#").lower()
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) != 6 or any(ch not in "0123456789abcdef" for ch in v):
        return default
    return "#" + v


def mm_to_in(value_mm: float) -> float:
    return value_mm / MM_PER_INCH


def in_to_mm(value_in: float) -> float:
    return value_in * MM_PER_INCH

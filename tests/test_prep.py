"""Auto tone and the brightest white (ADR-022, ADR-023)."""
from __future__ import annotations

import numpy as np

from stringart.board import BoardConfig
from stringart.prep import AUTO_WHITE_LEVEL, PrepSettings, auto_tone, tone_pipeline


def _framed(values: np.ndarray, res: int = 200) -> np.ndarray:
    """A framed square whose grey levels are drawn, shuffled, from values."""
    rng = np.random.default_rng(0)
    g = rng.choice(values, size=(res, res)).astype(np.float32)
    return np.repeat(g[..., None], 3, axis=2)


def test_levels_follow_the_percentiles():
    framed = _framed(np.linspace(0.1, 0.9, 1001))
    tone = auto_tone(framed, PrepSettings(), BoardConfig())
    assert tone["gamma"] == 1.0 and tone["clahe_clip"] == 0.0 and tone["edge_strength"] == 0.0
    assert abs(tone["black_point"] - (0.1 + 0.8 * 0.01)) < 0.01
    assert abs(tone["white_point"] - (0.1 + 0.8 * 0.995)) < 0.01
    out = tone_pipeline(framed, PrepSettings(**tone))
    assert (out <= 0.0).mean() > 0.003


def test_brightest_white_is_a_light_grey():
    """Nothing in the target is lighter than white_level, so highlights still get thread."""
    framed = _framed(np.linspace(0.0, 1.0, 1001))
    out = tone_pipeline(framed, PrepSettings(white_level=0.8))
    assert abs(float(out.max()) - 0.8) < 1e-5
    assert abs(float(tone_pipeline(framed, PrepSettings(white_level=1.0)).max()) - 1.0) < 1e-5
    tone = auto_tone(framed, PrepSettings(), BoardConfig())
    assert tone["white_level"] == AUTO_WHITE_LEVEL
    assert float(tone_pipeline(framed, PrepSettings(**tone)).max()) <= AUTO_WHITE_LEVEL + 1e-5
    assert PrepSettings.from_dict({"white_level": 0.1}).white_level == 0.5

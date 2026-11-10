"""Auto tone (ADR-022): levels from the photo's own percentiles, with a light subject kept in greys."""
from __future__ import annotations

import numpy as np

from stringart.board import BoardConfig
from stringart.prep import PrepSettings, auto_tone, tone_pipeline


def _framed(values: np.ndarray, res: int = 200) -> np.ndarray:
    """A framed square whose grey levels are drawn, shuffled, from values."""
    rng = np.random.default_rng(0)
    g = rng.choice(values, size=(res, res)).astype(np.float32)
    return np.repeat(g[..., None], 3, axis=2)


def test_levels_follow_the_percentiles():
    board = BoardConfig()
    framed = _framed(np.linspace(0.1, 0.9, 1001))
    tone = auto_tone(framed, PrepSettings(), board)
    assert tone["gamma"] == 1.0 and tone["clahe_clip"] == 0.0 and tone["edge_strength"] == 0.0
    assert abs(tone["black_point"] - (0.1 + 0.8 * 0.01)) < 0.01
    # The middle tone (0.5) would land at 0.53 after a 95th-percentile white point: under the cap.
    assert abs(tone["white_point"] - (0.1 + 0.8 * 0.95)) < 0.01
    out = tone_pipeline(framed, PrepSettings(**tone))
    assert (out <= 0.0).mean() > 0.003 and (out >= 1.0).mean() > 0.03


def test_light_subject_stays_grey():
    """A pale face on a dark background: the percentile white point would push the face to near
    white, so the white point rises until the face's middle tone sits at 70% lightness."""
    board = BoardConfig()
    res = 200
    framed = np.full((res, res, 3), 0.1, np.float32)
    subject = np.zeros((res, res), bool)
    subject[60:140, 60:140] = True
    framed[subject] = 0.6
    tone = auto_tone(framed, PrepSettings(), board, subject)
    out = tone_pipeline(framed, PrepSettings(**tone))
    assert abs(float(np.median(out[subject])) - 0.70) < 0.02
    without = auto_tone(framed, PrepSettings(), board)
    assert without["white_point"] < tone["white_point"]

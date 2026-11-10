"""The brightest white (ADR-023): the target never asks for lighter than white_level."""
from __future__ import annotations

import numpy as np

from stringart.prep import PrepSettings, tone_pipeline


def test_brightest_white_is_a_light_grey():
    rng = np.random.default_rng(0)
    g = rng.choice(np.linspace(0.0, 1.0, 1001), size=(200, 200)).astype(np.float32)
    framed = np.repeat(g[..., None], 3, axis=2)
    assert PrepSettings().white_level == 0.8
    out = tone_pipeline(framed, PrepSettings())
    assert abs(float(out.max()) - 0.8) < 1e-5 and float(out.min()) == 0.0
    assert abs(float(tone_pipeline(framed, PrepSettings(white_level=1.0)).max()) - 1.0) < 1e-5
    assert PrepSettings.from_dict({"white_level": 0.1}).white_level == 0.5

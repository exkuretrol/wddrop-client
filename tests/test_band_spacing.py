"""The band's letter spacing is fitted on ONE line, and its error is per character.

Calibration fits the spacing against whatever name the player's drop shot happened to show,
and a short name cannot measure it: the error accumulates along the line, so six characters
hide what seventeen expose. Measured on a real 1920x1080 fit whose own self-check scored
0.9241 on 「100バイン紙幣」 (6 characters), reading 「北穿の幽霊城の常なる冥刻のガラクタ」 (17):

    the calibrated +0.7   0.7026, margin 0.0159 over its own family   refused
    +0.5                  0.8613, margin 0.0274                       still thin
    +0.5, tie-broken      margin 0.4386 at fit 0.9467                 read

That chest was recorded one item short. So a refused line is re-scored at neighbouring
spacings — over the shortlist only, and only on a refusal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

ATLAS = ROOT / "data" / "atlas.ja.json"
pytestmark = pytest.mark.skipif(not ATLAS.exists(), reason="atlas not built")

TRUE = "北穿の幽霊城の常なる冥刻のガラクタ"
FAMILY = ["北穿の幽霊城の常なる冥刻のガラクタ", "北穿の幽霊城の妖なる冥刻のガラクタ",
          "北穿の幽霊城の常なる四鱗のガラクタ", "北穿の幽霊城の常なる一縷のガラクタ"]


def _runner(spacing: float):
    from types import SimpleNamespace

    from wddrop_client.capture.glyph import make_renderer
    from wddrop_client.runner import CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.profile = SimpleNamespace(letter_spacing=spacing, offset=(0, 0))
    runner.renderer = make_renderer(str(ATLAS), 22, (740, 45), spacing)
    runner.prefix = ""
    runner._spacing_renderers = {}
    runner._spacing_votes = {}
    runner._profile_path = None
    runner.stats = {}
    return runner


def _observation(spacing: float):
    """The line as the GAME drew it — i.e. at the spacing the client did not fit."""
    from wddrop_client.capture.glyph import make_renderer

    return make_renderer(str(ATLAS), 22, (740, 45), spacing).render(TRUE)


def test_a_line_refused_at_the_fitted_spacing_is_read_at_the_right_one():
    from wddrop_client.capture.glyph import RenderMatch

    drawn_at, fitted_at = 0.5, 0.7
    runner = _runner(fitted_at)
    observed = _observation(drawn_at)
    match = runner.recognizer.recognize(observed) if hasattr(runner, "recognizer") else None
    # Built by hand rather than through an index: this is about the SPACING pass, and the
    # first pass's own refusal is the input to it.
    refused = RenderMatch(None, 0.70, 0.02, False, runner_up=FAMILY[1], best=TRUE,
                          template_width=0, shortlist=tuple(FAMILY))
    got = runner._reread_at_other_spacings(observed, refused)
    assert got is not None, "the line was left refused at a spacing that cannot read it"
    assert got.name == TRUE
    assert got.score > refused.score


def test_it_does_not_fire_when_the_calibrated_spacing_is_already_right():
    """A spacing must EARN the reading: a material gain, not a rounding difference."""
    from wddrop_client.capture.glyph import RenderMatch

    runner = _runner(0.5)
    observed = _observation(0.5)
    # Score it honestly at the fitted spacing first, then offer that as the refusal.
    from wddrop_client.capture.glyph import zncc

    score = zncc(observed, runner.renderer.render(TRUE))
    refused = RenderMatch(None, score, 0.02, False, runner_up=FAMILY[1], best=TRUE,
                          template_width=0, shortlist=tuple(FAMILY))
    assert runner._reread_at_other_spacings(observed, refused) is None


def test_the_measurement_is_written_to_the_profile_only_after_a_second_line():
    """One line is a reading; two is the spacing. Until then nothing is written, because a
    profile is what the NEXT session builds its whole index from."""
    from wddrop_client.capture.glyph import RenderMatch
    from wddrop_client.runner import BAND_SPACING_VOTES

    runner = _runner(0.7)
    observed = _observation(0.5)
    refused = RenderMatch(None, 0.70, 0.02, False, runner_up=FAMILY[1], best=TRUE,
                          template_width=0, shortlist=tuple(FAMILY))
    saved = []
    runner._remember_band_spacing = saved.append
    for _ in range(BAND_SPACING_VOTES):
        assert runner._reread_at_other_spacings(observed, refused) is not None
    assert saved == [0.5], f"expected one save of the measured spacing, got {saved}"

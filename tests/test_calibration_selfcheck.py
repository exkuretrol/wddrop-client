"""Calibration's self-check must not be stricter than the reader it is checking.

A profile is refused unless recognition returns the name the player confirmed — which is the
right rule, and it was being applied with LESS evidence than the runner uses. `recognize`
alone refuses a thin margin; the runner hands the top two to `break_tie`, which compares only
the columns where they differ.

Junk families are exactly where that matters. 「北穿の幽霊城の妖なる四鱗のガラクタ」 differs
from 「…冥刻…」 in one word of seventeen characters, so a player whose calibration chest
happened to hold one could not calibrate AT ALL — at any resolution — while the client would
have read that same line correctly every time.

Measured on the 1600x900 shot this was written for: the fit scored 0.823 and the self-check
returned None at margin 0.0183, where the tie-break separates the same two candidates by
0.3065 at fit 0.8432.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

ATLAS = ROOT / "data" / "atlas.ja.json"
pytestmark = pytest.mark.skipif(not ATLAS.exists(), reason="atlas not built")

TRUE = "北穿の幽霊城の妖なる四鱗のガラクタ"
FAMILY = [TRUE, "北穿の幽霊城の妖なる冥刻のガラクタ", "北穿の幽霊城の常なる四鱗のガラクタ",
          "透明な小石", "下級鉄鉱石", "モニヨン銀貨"]
SUFFIX = "を手に入れた!!"


def _shot(size: int = 18, spacing: float = 0.0, text: str | None = None):
    """A frame with one drop line on it, drawn in the game's own face."""
    import numpy as np
    from PIL import Image

    from wddrop_client.capture.glyph import make_renderer

    line = make_renderer(str(ATLAS), size, (900, size * 2 + 8), spacing).render(
        text if text is not None else f"{TRUE}×2{SUFFIX}")
    frame = np.zeros((900, 1600), dtype=np.uint8)
    frame[720:720 + line.shape[0], 300:300 + line.shape[1]] = line
    return Image.fromarray(frame, "L")


def _fit(shot):
    from wddrop_client.calibration import fit_message_profile

    return fit_message_profile(shot, TRUE, "", [str(ATLAS)], FAMILY,
                               locale="ja", suffix=SUFFIX, separator="×")


def test_a_name_from_a_close_family_still_calibrates():
    profile = _fit(_shot())
    assert profile.notes["self_check_name"] == TRUE
    assert profile.font_size == 18


def test_the_check_still_refuses_a_name_that_is_not_on_the_shot():
    """The gate is not merely loosened: a fit that reads something else is still refused, or
    a profile that cannot read its own frame would be saved and every later drop lost."""
    import pytest as _pytest

    from wddrop_client.calibration import fit_message_profile

    shot = _shot(text=f"透明な小石×2{SUFFIX}")
    with _pytest.raises(ValueError, match="failed its own check"):
        fit_message_profile(shot, TRUE, "", [str(ATLAS)], FAMILY,
                            locale="ja", suffix=SUFFIX, separator="×")


def test_a_wrapped_shot_is_recorded_as_one():
    """The line the player photographs may wrap — the game wraps rather than clips — and then
    the tail is on a row the fit cannot see, so the geometry is fitted against ink no
    candidate covers. It still calibrates; `name_ends_at: None` is how the profile says the
    fit passed the harder way."""
    profile = _fit(_shot(text=f"{TRUE}×2を"))
    assert profile.notes["name_ends_at"] is None
    assert profile.notes["self_check_name"] == TRUE


def test_the_fit_is_sharpened_when_the_check_needed_the_tie_break():
    """A geometry that READS the calibration name is not yet one that tells it from its
    family — and the first pass cannot see the difference, because a smaller size with more
    letter spacing fits a CJK-only name just as well as the right size does.

    Measured on the 1600x900 shot this was written for, same face, same window:

        18px +0.9   the calibration name 0.7872   「10,000バイン紙幣」 0.6646   <- first pass
        19px -0.2   the calibration name 0.8133   「10,000バイン紙幣」 0.8536   <- sharpened

    0.6646 is under the 0.60... it is over it, and 0.5982 at the fitted spacing was not: that
    chest was in the recording, and the session recorded nothing for it. Names written in
    ASCII digits are where the difference shows, because their advances differ most from the
    atlas's — and a calibration shot is almost never one of them.
    """
    profile = _fit(_shot(size=19, spacing=-0.2))
    assert profile.notes["self_check_name"] == TRUE
    # The neighbourhood is searched at every plausible SPACING, not around the incumbent:
    # the right spacing at another size is nowhere near this one — +0.9 against -0.2 here.
    from wddrop_client.calibration import SHARPEN_SPACINGS

    assert min(SHARPEN_SPACINGS) <= -0.2 and max(SHARPEN_SPACINGS) >= 1.5


def test_sharpening_never_adopts_a_geometry_that_cannot_read_the_shot():
    """It replaces a fit that passed its own check. A replacement that does not pass is not
    an improvement, however much better it separates."""
    import inspect

    from wddrop_client import calibration

    source = inspect.getsource(calibration.fit_message_profile)
    assert "candidate.best == confirmed_name and candidate.margin > match.margin" in source

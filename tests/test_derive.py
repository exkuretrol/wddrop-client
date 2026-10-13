"""Geometry computed from the frame size, for sizes nobody calibrated."""
import json
from pathlib import Path

import pytest

from wddrop_client.calibration import Profile, read_columns
from wddrop_client.capture.derive import band_geometry

SHIPPED = json.loads((Path(__file__).resolve().parents[1] / "profiles.shipped.json")
                     .read_text(encoding="utf-8"))


@pytest.mark.parametrize("size,pen,em,wrap", [
    ((704, 1241), (87.91, 996.50), 24.958, 528.19),
    ((1920, 1080), (730.17, 867.22), 21.720, 459.66),
    ((1600, 900), (608.47, 722.68), 18.100, 383.05),
    ((1280, 720), (486.78, 578.14), 14.480, 306.44),
])
def test_the_computed_geometry_is_what_it_was_verified_as(size, pen, em, wrap):
    """Fixed values, because this is the thing a later edit must not move quietly.

    These four were checked against the layout independently of this code. A change here
    that shifts them is either a better derivation or a broken one, and either way it has to
    be argued for rather than noticed later by a player whose lines stopped being read.
    """
    g = band_geometry(size)
    assert g.pen[0] == pytest.approx(pen[0], abs=0.01)
    assert g.pen[1] == pytest.approx(pen[1], abs=0.01)
    assert g.seed_em == pytest.approx(em, abs=0.01)
    assert g.wrap_width == pytest.approx(wrap, abs=0.01)


def test_it_lands_on_the_shipped_fits_without_having_seen_them():
    """The three calibrated sizes are the only check available that the computation is
    RIGHT rather than merely consistent: each was fitted by hand to a real screen, long
    before anything was computed. The band must contain the fitted one and the left edge
    must sit just left of the fitted ink, which is where a glyph's bearing puts it."""
    for key, prof in SHIPPED.items():
        size = tuple(prof["frame_size"])
        g = band_geometry(size)
        top, bottom = prof["message_band"]
        assert g.pen[1] <= top, f"{key}: computed band starts below the fitted one"
        assert g.pen[1] + g.seed_em >= bottom - 1, f"{key}: computed band ends above it"
        # The pen is the box's edge; ink starts a glyph's own bearing to the right of it.
        assert 0 < prof["text_x0"] - g.pen[0] < 8, f"{key}: left edge is not a bearing away"


def test_the_computed_size_equals_every_measured_one():
    """EQUAL, at all three, and only because the rounding goes up.

    The computed values are 24.958 / 21.720 / 18.100 against measured 25 / 22 / 19. To
    nearest, the third becomes 18 — and 18 is not a near miss: twelve real lines scored 0.37
    there and not one was accepted, where 19 took all twelve at 0.87. A size that is wrong by
    one pixel reads NOTHING, so this is the assertion that says the feature works at all.

    Three points is not a law, and the test exists to catch the rounding being changed back
    rather than to prove the rule."""
    for key, prof in SHIPPED.items():
        p = Profile.derived(tuple(prof["frame_size"]), font_path="x")
        assert p.font_size == prof["font_size"], (
            f"{key}: computed {p.font_size}px against a measured {prof['font_size']}px")


def test_a_frame_too_narrow_to_speak_for_is_refused():
    """A phone's usable area is inset by more than its reported edges, by an amount this
    module does not carry. Guessing would return a plausible answer tens of pixels out, so
    it raises — and accepts the answer when the caller supplies it."""
    with pytest.raises(ValueError, match="too narrow"):
        band_geometry((1080, 2424))
    g = band_geometry((1080, 2424), windows=False,
                      safe_area=(0, 252.21, 1080, 2344.79 - 252.21))
    assert g.pen[1] == pytest.approx(1935.99, abs=0.01)


def test_a_derived_profile_is_marked_unscored():
    """`calibration_score` 0.0 is load-bearing: it says nothing has measured this, which is
    what stops a computed profile being mistaken for a tested one."""
    p = Profile.derived((1366, 768), font_path="atlas.ja.scenario.json", locale="ja")
    assert p.calibration_score == 0.0
    assert p.frame_size == (1366, 768)
    assert p.message_band[0] < p.message_band[1]
    assert read_columns(p) is not None, "the read columns must still be derivable"


def test_a_derived_profile_covers_a_size_that_ships_no_fit():
    """The point of the exercise: 1366x768 has no calibration and never had one."""
    assert not any(tuple(p["frame_size"]) == (1366, 768) for p in SHIPPED.values())
    p = Profile.derived((1366, 768), font_path="atlas.ja.scenario.json")
    assert p.font_size > 0 and p.text_x0 > 0


def test_a_wrapped_line_is_judged_against_its_own_frame():
    """A real wrapped line was rejected by 0.043 on a threshold measured elsewhere.

    `_cut_at_a_wrapped_separator` asks two questions: is the 「×」 unmistakable, and does the
    wording's first character follow it. Both were fitted on Windows captures, where this text
    reads 0.88-0.92; the same game on a phone reads 0.75-0.80 throughout. On a real frame —
    「…のガラクタ×3を手」 wrapping before 「に入れた!!」 — the 「×」 scored 0.946 and the 「を」 0.808,
    both plainly there, and the 「を」 fell under an absolute 0.85 that belonged to another
    platform. Judging it against the separator beside it, measured by the same instrument on
    the same pixels, survives that; the floor stops a weak pair passing on ratio alone.
    """
    from wddrop_client.capture.glyph import (SEPARATOR_MIN_SCORE, SEPARATOR_STRONG,
                                             TAIL_FOLLOWS_SEPARATOR)

    assert TAIL_FOLLOWS_SEPARATOR < 1.0, "the tail may read a little under the separator"
    # The frame that prompted this: a separator at 0.946 must admit a 「を」 at 0.808 ...
    assert 0.808 >= max(SEPARATOR_MIN_SCORE, 0.946 * TAIL_FOLLOWS_SEPARATOR)
    # ... while a pair that is weak in absolute terms is still refused.
    assert 0.55 < max(SEPARATOR_MIN_SCORE, 0.62 * TAIL_FOLLOWS_SEPARATOR)
    assert SEPARATOR_STRONG == 0.85, "the separator's own bar is unchanged"

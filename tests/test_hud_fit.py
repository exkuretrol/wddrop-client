"""Choosing the band the HUD template is cut from.

WHAT WENT WRONG, AND WHY NOTHING CAUGHT IT
------------------------------------------
The band used to be chosen by edge density from ONE screenshot, with a straightness check to
keep it off dungeon walls. The minimap's INTERIOR satisfies both beautifully — it is a dense
ruled grid — and it is the one part of the panel that must never be matched, because it
redraws as the floor is explored and scrolls with the player.

At 1920x1080 that is exactly what was chosen: a 153x38 crop of map interior. Measured on the
session it was fitted for, it matched **0 of 135 frames**, so the HUD was never seen, episodes
never closed, and a two-minute dive came back as one 2,700-frame episode. Every check passed:
the crop looked like a panel, and it separated the two calibration shots perfectly.

The property that separates chrome from map is not in one picture:

    stability     the same band on ANOTHER walking frame still correlates
    leak          the same band on a frame with no HUD does not
    straightness  it looks like a panel edge rather than scenery

Measured over two real walking frames at 1920x1080 (different corridors) and the drop shot:

    icon bar under the map    stability 0.694   leak -0.03   <- chosen
    map interior              stability 0.221   leak +0.04
    the map's top edge        stability 0.750   leak +0.26   <- rejected on leak
    flat panel below the bar  stability 0.823   straightness 0.08, rejected

and the fitted threshold then reads the four walking frames as HUD and the four others as no
HUD, where the old fit read none of them.
"""
from __future__ import annotations


import pytest


pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from wddrop_client.calibration import (HUD_THRESHOLD_FLOOR, Profile,  # noqa: E402
                                       choose_hud_region, fit_hud)
from wddrop_client.capture.hud import HudDetector  # noqa: E402

W, H = 960, 540
# Where the pretend panel is: a corner, like both real layouts.
# High in the corner, like both real layouts: the search only looks at the top third, because
# that is where both the portrait and the landscape game put the minimap.
PANEL = (W - 160, 10, W - 20, 170)
CHROME_TOP = 125                       # the bar under the map: fixed furniture
MAP_BOTTOM = 120


def _frame(seed: int, *, hud: bool = True) -> Image.Image:
    """A frame with moving scenery, and optionally a panel whose MAP half moves too."""
    rng = np.random.default_rng(seed)
    a = (rng.random((H, W)) * 90).astype(np.uint8)
    if not hud:
        return Image.fromarray(a, "L")
    x0, y0, x1, y1 = PANEL
    # The map: a ruled grid whose lines move with the player. Dense, straight, and different
    # every frame — everything the old rule looked for, and useless as a template.
    a[y0:MAP_BOTTOM, x0:x1] = 20
    shift = seed * 7 % 20
    for gx in range(x0 + shift % 20, x1, 20):
        a[y0:MAP_BOTTOM, gx:gx + 2] = 200
    for gy in range(y0 + shift % 20, MAP_BOTTOM, 20):
        a[gy:gy + 2, x0:x1] = 200
    # The chrome: a bar with a border and two round-ish buttons, identical in every frame.
    a[CHROME_TOP:y1, x0:x1] = 60
    a[CHROME_TOP:CHROME_TOP + 3, x0:x1] = 220           # its top edge, full width
    a[CHROME_TOP:y1, x0:x0 + 3] = 220                   # and its left edge, full height
    for bx in (x0 + 30, x0 + 90):
        a[CHROME_TOP + 15:CHROME_TOP + 35, bx:bx + 20] = 230
    return Image.fromarray(a, "L")


def test_the_band_chosen_is_the_one_that_holds_still():
    walking = [_frame(i) for i in (1, 2, 3)]
    region, stability, leak = choose_hud_region(walking, _frame(9, hud=False))
    top, bottom = region[1] * H, region[3] * H
    assert top >= MAP_BOTTOM - 8, f"the band reaches into the map interior: {region}"
    assert bottom <= PANEL[3] + 8, f"the band runs off the panel: {region}"
    assert stability > 0.9 and leak < 0.15, (stability, leak)


def test_one_frame_alone_cannot_measure_it_and_says_so_by_falling_back():
    """Not an error: it is what every profile fitted before this used, and it still fits."""
    region, stability, leak = choose_hud_region([_frame(1)])
    assert region is not None
    assert (stability, leak) == (None, None)


def test_frames_taken_standing_still_fall_back_rather_than_pick_the_map():
    """The instruction is KEEP WALKING. If the player does not, every band is stable —
    including the interior — and stability has stopped being evidence."""
    same = _frame(4)
    region, stability, _leak = choose_hud_region([same, same, same], _frame(9, hud=False))
    assert region is not None
    # It went back to the density search, which returns a region but no measurements.
    assert stability is None or region[1] * H >= MAP_BOTTOM - 8


def test_the_profile_carries_a_threshold_between_the_two_measurements():
    profile = Profile(frame_size=(W, H), message_band=(400, 420), font_path="", font_size=20,
                      offset=(0, 0), calibration_score=0.9, text_x0=100)
    walking = [_frame(i) for i in (1, 2, 3)]
    fit_hud(profile, walking, absent=_frame(9, hud=False))
    assert profile.hud_threshold is not None
    assert profile.hud_threshold >= HUD_THRESHOLD_FLOOR
    assert profile.notes["hud_stability"] > profile.hud_threshold > profile.notes["hud_leak"]


def test_the_detector_built_from_that_profile_uses_the_fitted_threshold():
    """It was stored and then ignored — every detector ran at the built-in 0.60, which is
    above every score a correct band reached at 1920x1080."""
    profile = Profile(frame_size=(W, H), message_band=(400, 420), font_path="", font_size=20,
                      offset=(0, 0), calibration_score=0.9, text_x0=100)
    fit_hud(profile, [_frame(i) for i in (1, 2, 3)], absent=_frame(9, hud=False))
    detector = HudDetector.from_profile(profile)
    assert detector.threshold == profile.hud_threshold


def test_walking_frames_read_as_hud_and_the_others_do_not():
    profile = Profile(frame_size=(W, H), message_band=(400, 420), font_path="", font_size=20,
                      offset=(0, 0), calibration_score=0.9, text_x0=100)
    fit_hud(profile, [_frame(i) for i in (1, 2, 3)], absent=_frame(9, hud=False))
    detector = HudDetector.from_profile(profile)
    assert all(detector.present(_frame(i).convert("L")) for i in (4, 5, 6))
    assert not any(detector.present(_frame(i, hud=False).convert("L")) for i in (7, 8, 9))

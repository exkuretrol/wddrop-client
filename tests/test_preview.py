"""Seeing what the client can see.

Both pictures exist for failures that do not look like failures. A HUD template that is a
photograph of a rock face is a perfectly good crop of a wall — it merely scores low, episodes
quietly never close, and four chests are recorded as one. A band capture does not grab reads
nothing at all while sitting in exactly the right place.
"""
from __future__ import annotations


import pytest


pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

from PIL import Image  # noqa: E402

from wddrop_client.calibration import Profile  # noqa: E402
from wddrop_client.preview import (annotate, as_capture_sees,  # noqa: E402
                                   named_regions, strips)


def a_profile(**kw):
    base = dict(frame_size=(704, 1241), message_band=(999, 1020), font_path="",
                font_size=25, offset=(-2, 1), calibration_score=0.91,
                hud_region=(0.89, 0.16, 0.97, 0.20))
    base.update(kw)
    return Profile(**base)


def a_frame(profile):
    """Mid-grey, so anything blacked out is unmistakably blacked out."""
    return Image.new("L", tuple(profile.frame_size), 128)


def test_the_preview_cannot_disagree_with_capture():
    """THE PROPERTY THAT MAKES IT WORTH LOOKING AT. A preview drawing its own idea of the
    regions is a picture that can be wrong while looking authoritative — which is worse than
    having none, because it would be believed. Capture takes its strips from here."""
    from wddrop_client.__main__ import _capture_strips

    profile = a_profile()
    for mining in (True, False):
        assert _capture_strips(profile, record=False, mining=mining) == \
            strips(profile, record=False, mining=mining)
    # Recording keeps whole frames, and says so the same way in both places.
    assert _capture_strips(profile, record=True) is None
    assert strips(profile, record=True) is None


def test_every_region_capture_grabs_is_one_the_picture_names():
    """A region drawn but not grabbed would be a lie in the reassuring direction."""
    profile = a_profile()
    grabbed = strips(profile, record=False)
    named = [box for _label, box in named_regions(profile)]
    assert named == grabbed
    assert [label for label, _ in named_regions(profile)] == \
        ["message band", "HUD", "mining panel"]


def test_each_strip_is_as_wide_as_the_thing_it_reads_and_no_wider():
    """THREE boxes, not one. The message band is measured on this machine; the mining panel
    is the game's own layout scaled to this screen and is WIDER — 644 columns against 504 at
    1920x1080 — and the ▼ that says the panel is finished lives in the difference."""
    from wddrop_client.calibration import panel_columns, read_columns

    profile = a_profile(frame_size=(1920, 1080), text_x0=732, message_band=(870, 887))
    boxes = dict(named_regions(profile))
    for label, expected in (("message band", read_columns(profile)),
                            ("mining panel", panel_columns(profile))):
        x, _y, w, _h = boxes[label]
        assert (x, x + w) == expected, f"{label} is {w}px wide, expected {expected}"
    assert boxes["mining panel"][2] > boxes["message band"][2], \
        "the panel is the wider of the two, and reading it in the band's columns loses the ▼"
    assert boxes["HUD"][0] > boxes["mining panel"][0] + boxes["mining panel"][2], \
        "the minimap is not inside either box"


def test_an_uncalibrated_left_edge_leaves_the_strips_full_width():
    profile = a_profile(text_x0=None)
    x, _y, w, _h = dict(named_regions(profile))["message band"]
    assert (x, w) == (0, profile.frame_size[0])


def test_a_profile_with_no_hud_does_not_claim_one():
    profile = a_profile(hud_region=None)
    assert [label for label, _ in named_regions(profile)] == ["message band", "mining panel"]


def test_what_it_gets_is_black_outside_the_strips():
    """The picture that shows a region MISSING rather than misplaced. Mining reported in a
    band that was not in the list, and it read nothing live while working perfectly on a
    recording — because a recording has whole frames and live capture does not."""
    import numpy as np

    profile = a_profile()
    seen = np.asarray(as_capture_sees(a_frame(profile), profile).convert("L"))

    for _label, (x, y, w, h) in named_regions(profile):
        assert seen[y + h // 2, x + w // 2] == 128, "a region capture grabs came out black"

    # A row between the panel band and the message band belongs to no strip.
    _, (_, panel_y, _, panel_h) = named_regions(profile)[2]
    gap = panel_y + panel_h + 20
    assert seen[gap, 10] == 0, "something outside every strip survived"


def test_leaving_mining_out_shows_up_as_a_black_band():
    """The failure this view exists for, reproduced: turn the panel band off and the picture
    goes black exactly where mining is announced."""
    import numpy as np

    profile = a_profile()
    with_mining = np.asarray(as_capture_sees(a_frame(profile), profile, mining=True).convert("L"))
    without = np.asarray(as_capture_sees(a_frame(profile), profile, mining=False).convert("L"))

    _, (_, y, _, h) = named_regions(profile)[2]
    assert with_mining[y + h // 2, 300] == 128
    assert without[y + h // 2, 300] == 0


def test_where_it_looks_draws_on_a_copy():
    """It is called on frames that are also being recognised. A debugging aid that draws on
    the evidence is not one."""
    profile = a_profile()
    frame = a_frame(profile)
    before = frame.tobytes()
    drawn = annotate(frame, profile)
    assert frame.tobytes() == before
    assert drawn is not frame
    assert drawn.mode == "RGB"
    # It did draw something: the outline colours are not the grey it started from.
    assert drawn.convert("L").tobytes() != before


def test_a_region_at_the_very_top_still_gets_its_label():
    """Drawn above the box where there is room and inside it where there is not — a label
    off the top of the frame is not a label."""
    profile = a_profile(hud_region=(0.89, 0.0, 0.97, 0.03))
    annotate(a_frame(profile), profile)     # must not raise, and must not clip off-frame

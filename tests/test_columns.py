"""The columns the game writes in, and what reading outside them cost.

The dialogue box is centred and the text is left-aligned inside it, so ONE calibrated number
— `text_x0` — describes both of its edges. Both fitted resolutions agree:

    704x1241   text_x0  93   wraps at 611   (704 - 93)
    1920x1080  text_x0 732   wraps at 1188  (1920 - 732)

Everything either side is dungeon. It was read anyway, and at 1920x1080 that is not a cost
in time alone:

  * `panel_rows` calls a row text at 8 lit pixels. Over 1920 columns that is 0.4% of the row,
    so rock highlights joined the mining panel's rows into blocks taller than a line, which
    the line-height filter then dropped — rows missing from a panel plainly on screen;
  * `advance_marker` looked for the ▼ at 0.85 of the FRAME width. At 704x1241 that is x598
    and the panel's right edge is x611, so it worked; at 1920x1080 it is x1632 and the panel
    ends at x1281, so the marker was never found and every panel had to settle by similarity
    — which a swing dismissed inside two frames never does;
  * the message band's key covered the whole row, so on a whole frame (a REPLAY) the scenery
    changed it every frame and the line never counted as held still.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from wddrop_client.calibration import (READ_MARGIN, Profile,  # noqa: E402
                                       panel_columns, read_columns, ui_scale)
from wddrop_client.capture.panel import (INK_LEVEL, SEARCH_TOP,  # noqa: E402
                                         advance_marker, panel_rows)


def a_profile(**kw):
    base = dict(frame_size=(704, 1241), message_band=(999, 1020), font_path="",
                font_size=25, offset=(0, 0), calibration_score=0.9, text_x0=93)
    base.update(kw)
    return Profile(**base)


def test_the_box_is_as_far_right_of_centre_as_its_left_edge_is_left_of_it():
    assert read_columns(a_profile()) == (93 - READ_MARGIN, 704 - 93 + READ_MARGIN)
    landscape = a_profile(frame_size=(1920, 1080), text_x0=732)
    assert read_columns(landscape) == (732 - READ_MARGIN, 1920 - 732 + READ_MARGIN)


def test_a_profile_that_never_measured_the_left_edge_reads_the_whole_width():
    """Which is what it did before, so an old profile is not made worse by this existing."""
    assert read_columns(a_profile(text_x0=None)) is None


def test_a_left_edge_past_the_centre_is_refused_rather_than_mirrored():
    """It would mirror to a right edge LEFT of the left one — an empty span, i.e. a client
    that reads nothing at all and says nothing about why."""
    assert read_columns(a_profile(text_x0=400)) is None


def _panel_frame(size=(1920, 1080), *, scenery=True):
    """A frame with two panel-ish text rows in the centre, and glare out at the edges.

    The glare is what a 1920-wide dungeon supplies for free: a handful of lit pixels on every
    row, which is all MIN_ROW_INK asks for.
    """
    w, h = size
    frame = np.zeros((h, w), dtype=np.uint8)
    top = int(h * SEARCH_TOP) + 40
    for row in (top, top + 30):
        frame[row:row + 18, w // 2 - 120:w // 2 + 120] = INK_LEVEL + 40
    if scenery:
        # A continuous bright column at each edge: every row in the search band now clears
        # MIN_ROW_INK on its own, so the rows run together into one tall block.
        frame[:, 60:76] = INK_LEVEL + 60
        frame[:, w - 76:w - 60] = INK_LEVEL + 60
    return frame


def test_the_scenery_either_side_swallows_the_panels_rows():
    """The failure being fixed, stated as the thing that happens without the columns."""
    rows = panel_rows(_panel_frame())
    assert rows == [], f"expected the rows to be lost in the glare, got {rows}"


def test_between_the_columns_the_two_rows_are_two_rows():
    columns = read_columns(a_profile(frame_size=(1920, 1080), text_x0=732))
    rows = panel_rows(_panel_frame(), columns)
    assert len(rows) == 2, rows
    assert all(12 <= b - a <= 44 for a, b in rows), rows


def test_the_columns_do_not_change_a_frame_that_had_no_glare_to_begin_with():
    """The resolution that already worked must read exactly the same rows it did before."""
    frame = _panel_frame((704, 1241), scenery=False)
    assert panel_rows(frame, read_columns(a_profile())) == panel_rows(frame)


def test_the_advance_marker_is_looked_for_at_the_panels_edge_not_the_frames():
    """The ▼ drawn where the real one is: 58px inside the panel's right edge at 1920x1080.

    Three ways to get this wrong, and the client has had all three:

      * search 0.85 of the FRAME (x1632) — five hundred pixels past a panel that ends at
        x1281, so the marker is never found and every panel settles by similarity instead;
      * search the MESSAGE band's columns (ending x1212) — 70px short of the same marker;
      * keep the 704x1241 pixel sizes — the marker there is 15x16 with 115 lit pixels and
        here it is 13x15 with 94, because the whole UI is 13% smaller.
    """
    w, h = 1920, 1080
    profile = a_profile(frame_size=(w, h), text_x0=732)
    scale = ui_scale(profile.frame_size)
    panel = panel_columns(profile)
    frame = np.zeros((h, w), dtype=np.uint8)
    rows = [(400, 420)]
    # A 13x15 triangle with ~94 lit pixels, centred 58px inside the panel's right edge.
    centre = panel[1] - 58
    for i in range(15):
        half = max(1, (13 - i) // 2)
        frame[470 + i, centre - half:centre + half] = INK_LEVEL + 50
    assert advance_marker(frame, rows, panel, scale) is True
    assert advance_marker(frame, rows) is False, "0.85 of the frame is not the panel's edge"
    assert advance_marker(frame, rows, read_columns(profile), scale) is False, \
        "the message band's columns stop short of the marker"


def test_the_panel_is_the_games_own_box_scaled_to_this_screen():
    """Not fitted here: read out of the game. The CanvasScaler on the UI Canvas is
    ScaleWithScreenSize, reference 1080x1920, ScreenMatchMode.Expand — so every UI element is
    a fixed size in canvas units and pixels follow min(w/1080, h/1920).

    Checked against what the recordings actually show, which is the only reason to believe it:

        1920x1080   scale 0.5625   panel measured at x637-1281   (644px)
         704x1241   scale 0.6464   1144 units is 740px on a 704px screen, and the recording
                                   shows the panel bleeding off both edges
    """
    landscape = a_profile(frame_size=(1920, 1080), text_x0=732)
    assert ui_scale((1920, 1080)) == pytest.approx(0.5625)
    left, right = panel_columns(landscape)
    assert (left, right) == pytest.approx((637, 1281), abs=2)

    portrait = a_profile()
    assert ui_scale((704, 1241)) == pytest.approx(0.6464, abs=0.001)
    assert panel_columns(portrait) == (0, 704), "clipped by the screen, not shrunk to fit"


def test_a_screen_narrower_than_the_reference_scales_by_WIDTH():
    """Expand takes the SMALLER of the two ratios, so a 9:19.5 phone — narrower than the
    1080x1920 the UI is designed for — scales by width instead of height. Nothing in the
    client has to know which case it is in; that is the point of taking it from the game."""
    tall = a_profile(frame_size=(1080, 2340), text_x0=150)
    assert ui_scale(tall.frame_size) == pytest.approx(1.0)
    left, right = panel_columns(tall)
    assert (right - left) == pytest.approx(1080, abs=2), "1144 units, clipped to the screen"

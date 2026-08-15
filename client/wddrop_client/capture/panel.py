"""
The mining result panel — a second place the game reports what you got.

Chests and mining announce through completely different UI, which is why three sessions of
mining left no trace in a client that only ever read one of them:

    chest    「獲得了下級鐵礦石 × 3！！」   one line at a time, bottom message band
    mining   「得到了下級鐵礦石 × 3。」     centred dialogue panel, SEVERAL lines at once

Measured on a real frame (704x1241): the panel's lines sat at y591-612 and y622-643, both
starting at x223, at 25px with +0.0 letter spacing, and read back at 0.9081 and 0.9533.

WHY THE ROWS ARE FOUND RATHER THAN CALIBRATED
---------------------------------------------
The message band is calibrated per player because the recogniser has to know the exact
origin of a line to compare it position-for-position. The panel does not need that: it is a
box on a dimmed background with nothing else lit near it, so its rows can simply be located
in the frame — the same row-ink projection calibration itself uses to find the message band.
That matters practically, because it means mining works for everyone already calibrated
instead of requiring every player to re-run calibration in front of an ore vein.

The panel holds SEVERAL items at once and is one interaction, so it is emitted as ONE
observation with all its lines — the same rule as a chest, for the same reason: it is one
roll, not N independent draws.

A PANEL DISMISSED TOO FAST IS LOST, DELIBERATELY
------------------------------------------------
A panel is read only once its ink has held ~80% steady from one sample to the next, which is
what proves the text has stopped fading in. Dismiss it inside two samples and it is never
read: measured on a real vein, three panels lasted two frames each at 0.299 and 0.677
similarity and only the swing the player lingered on was recorded.

The obvious fix — read the last frame before it vanishes, as the message band does — was
built and REMOVED. Without stability behind it the reading is its own only evidence, and
battle scene noise in a session containing no mining at all was accepted as
「下級鐵礦石 x3, 透明鵝卵石 x3」: a complete, plausible, entirely invented observation, which
survived even at a 0.78 score floor. Losing a swing is recoverable — the player can mine
again — while a fabricated one is indistinguishable from data afterwards.

So the rule stands, and the answer to "did I click too quickly" is yes: let the panel sit for
about a fifth of a second, or sample faster.
"""
from __future__ import annotations

import logging

log = logging.getLogger("wddrop.panel")

# Where to look, as a fraction of frame height. The panel is vertically centred; the bottom
# third is the party HUD and the calibrated message band, which must not be mistaken for it.
SEARCH_TOP, SEARCH_BOTTOM = 0.35, 0.62
# A row needs this many lit pixels to count as text. The panel background is a flat gradient,
# so anything above it is either a glyph or the ▼ advance marker.
MIN_ROW_INK = 8
# Rows closer than this belong to the same line of text.
ROW_GAP = 5
# Plausible height of one rendered line, in pixels. Excludes both single-pixel noise and the
# large blurred artwork behind the panel.
MIN_LINE_H, MAX_LINE_H = 12, 44
# More lines than this is not a result panel — it is a scenario dialogue or a menu. This
# bounds LINES IN ONE PANEL, never how many times a vein may be worked: dig time is dynamic
# and nothing here may cap the number of swings.
MAX_LINES = 8
INK_LEVEL = 150


def _columns(arr, columns):
    """(left, right) to read between, defaulting to the whole width."""
    return columns if columns else (0, arr.shape[1])


def panel_rows(gray, columns: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    """Text rows of the result panel, as (top, bottom) pairs, or [] when there is no panel.

    `gray` is the whole frame. Returns rows in screen order, so the caller reads them the way
    the player does.

    `columns` is the horizontal span the game writes in — `calibration.read_columns`. Without
    it every column counts, and MIN_ROW_INK is then a bar that the scenery clears on its own:
    at 1920x1080 rock highlights merged with the panel's rows into blocks taller than a line,
    which `MIN_LINE_H..MAX_LINE_H` then dropped. Rows vanished from panels that were plainly
    on screen, which is what "some rows of the mining result are not detected" was.
    """
    import numpy as np

    arr = np.asarray(gray, dtype=float)
    h = arr.shape[0]
    left, right = _columns(arr, columns)
    top_y, bottom_y = int(h * SEARCH_TOP), int(h * SEARCH_BOTTOM)
    lit = (arr[top_y:bottom_y, left:right] > INK_LEVEL).sum(axis=1)

    rows: list[tuple[int, int]] = []
    start = last = None
    for i, n in enumerate(lit):
        if n >= MIN_ROW_INK:
            if start is None:
                start = i
            last = i
        elif start is not None and last is not None and i - last > ROW_GAP:
            rows.append((top_y + start, top_y + last + 1))
            start = last = None
    if start is not None and last is not None:
        rows.append((top_y + start, top_y + last + 1))

    rows = [(a, b) for a, b in rows if MIN_LINE_H <= b - a <= MAX_LINE_H]
    # A panel is a small number of lines. Anything longer is another screen entirely, and
    # reading it would invent observations out of scenario text.
    return rows if len(rows) <= MAX_LINES else []


# Rendered ink height as a fraction of font size, for CJK. Measured on two independent
# samples of this game's UI: the panel's rows are 21px tall and fit at 25px (0.840), and the
# calibrated message band is 22px at 26px (0.846).
#
# This exists because THE PANEL IS NOT THE SAME SIZE AS THE MESSAGE BAND — 25px against 26px
# — and one pixel decides everything: the same line scores 0.897 at 25px and 0.46-0.55 at 24
# or 26. Deriving the size from the rows that are actually on screen keeps mining working for
# players who calibrated long before this existed, instead of making everyone re-calibrate in
# front of an ore vein.
INK_HEIGHT_RATIO = 0.843


def size_from_rows(rows) -> int | None:
    """Font size implied by the panel's row heights."""
    if not rows:
        return None
    heights = sorted(b - a for a, b in rows)
    median = heights[len(heights) // 2]
    return int(round(median / INK_HEIGHT_RATIO))


# How alike two panel signatures must be to count as the same text.
#
# NOT equality: the panel fades in and its ▼ marker blinks, so an exact mask match is never
# seen twice running — one panel gave 10 distinct masks over 10 frames at every ink threshold
# tried, and demanding equality meant it could never settle at all.
#
# And compared over the INK, not over the region. The panel band is mostly empty, so two
# completely different panels still agree on ~98% of its pixels; a plain agreement ratio
# called every panel "the same" and swallowed four swings out of five. Intersection over
# union of the lit pixels is the measure that actually tracks the text.
# Measured over the panels of one worked vein: consecutive frames of the SAME panel overlap by
# 0.716-0.993 while it fades in, and a panel that is only on screen for three frames never
# gets past 0.874. Settling is only asked to tell "still fading" from "stable" -- the dedupe
# is by presence, not by content -- so the bar sits below the fade, not above it.
SAME_TEXT = 0.80


def panel_signature(gray, rows=None, columns: tuple[int, int] | None = None):
    """What the panel currently shows, as a comparable ink mask.

    Taken over the whole panel SEARCH BAND, not over the detected rows: the row boundaries
    move by a pixel between frames as the text fades in, so a signature shaped by them is a
    different shape each time and two frames can never be compared at all. A fixed region is
    the same shape by construction.

    The MASK, not the pixels: the panel is drawn over a live, blurred 3D scene, so raw pixels
    differ every frame even when the text is identical — the same trap that once made a whole
    session record zero chests.
    """
    import numpy as np

    arr = np.asarray(gray, dtype=float)
    h = arr.shape[0]
    left, right = _columns(arr, columns)
    band = arr[int(h * SEARCH_TOP):int(h * SEARCH_BOTTOM), left:right]
    return (band > INK_LEVEL)[::2, ::2]


def same_text(a, b, threshold: float = SAME_TEXT) -> bool:
    """Whether two signatures show the same thing, tolerating a fade and a blinking marker."""
    import numpy as np

    if a is None or b is None or getattr(a, "shape", None) != getattr(b, "shape", None):
        return False
    union = float((a | b).sum())
    if union == 0:
        return True                       # both blank: the same nothing
    return float((a & b).sum()) / union >= threshold


# The ▼ advance marker: the game stating that the panel is FINISHED and waiting for input.
#
# This is a far better completeness signal than watching the ink settle, because it is the
# game's own statement rather than an inference. Measured across four sessions:
#
#     complete panels    116-120 lit pixels, bounding box 15-16 x 16, every time
#     still animating    0
#     panel-shaped noise 40-63 lit pixels, boxes like 24x3 and 39x70
#
# So the SHAPE is what separates it: the ink count alone would accept 60 noise frames from a
# session containing no mining at all. A player who dismisses the panel quickly is no longer
# punished — the marker is there on the first frame the panel is done, where the settle test
# needed a second frame to compare against.
# EVERY NUMBER HERE IS IN CANVAS UNITS, and the caller scales them. The game's UI is one
# fixed layout scaled by min(width/1080, height/1920) — read out of the CanvasScaler in the
# Steam build, see calibration.UI_REFERENCE — so a constant in PIXELS is a constant that is
# only true at the resolution it was measured at. Both were, and it cost both faults that a
# 1920x1080 recording came back with:
#
#                          704x1241 (scale .646)   1920x1080 (scale .563)   units
#     marker box              15 x 16 px               13 x 15 px            ~23
#     marker ink                 115 px                    94 px            ~280
#     below the last row          90 px                    77 px            ~140
#
# The old constants were the 704 pixels. The search also started at 0.85 of the FRAME width,
# which is x598 there — the panel's right edge is x611, so it worked — and x1632 at 1080,
# where the panel ends at x1281. The marker was never found, so every panel had to settle by
# similarity instead, and a swing dismissed inside two frames never does.
ARROW_SEARCH_BELOW_UNITS = 217.0        # under the last text row
ARROW_FROM_RIGHT_UNITS = 260.0          # the marker centre sits ~104 units inside the edge
ARROW_MIN_INK_UNITS, ARROW_MAX_INK_UNITS = 215.0, 385.0     # square units, so scale²
ARROW_MIN_SIDE_UNITS, ARROW_MAX_SIDE_UNITS = 18.0, 31.0
# Fallback for a caller with no profile to scale by: the frame's own proportions. Only ever
# right at the resolution it was measured at, which is why nothing in the client uses it.
ARROW_FROM_WIDTH = 0.85
ARROW_SEARCH_BELOW = 140


def advance_marker(gray, rows, columns: tuple[int, int] | None = None,
                   scale: float | None = None) -> bool:
    """Whether the ▼ is showing, i.e. the panel has finished drawing.

    `columns` is the PANEL's box (calibration.panel_columns), not the message band's, and
    `scale` is calibration.ui_scale for this screen. Given neither, the old frame-relative
    numbers are used, which are correct at 704x1241 and nowhere else.
    """
    import numpy as np

    if not rows:
        return False
    arr = np.asarray(gray, dtype=float)
    if columns and scale:
        _left, right = columns
        start = int(right - ARROW_FROM_RIGHT_UNITS * scale)
        below_px = int(ARROW_SEARCH_BELOW_UNITS * scale)
        min_ink, max_ink = ARROW_MIN_INK_UNITS * scale ** 2, ARROW_MAX_INK_UNITS * scale ** 2
        min_side, max_side = ARROW_MIN_SIDE_UNITS * scale, ARROW_MAX_SIDE_UNITS * scale
    else:
        start, right = int(arr.shape[1] * ARROW_FROM_WIDTH), arr.shape[1]
        below_px = ARROW_SEARCH_BELOW
        min_ink, max_ink, min_side, max_side = 90, 160, 12, 20
    below = arr[rows[-1][1]:rows[-1][1] + below_px, max(0, start):right]
    mask = below > INK_LEVEL
    ink = int(mask.sum())
    if not (min_ink <= ink <= max_ink):
        return False
    ys, xs = np.where(mask)
    w = int(xs.max() - xs.min() + 1)
    h = int(ys.max() - ys.min() + 1)
    return (min_side <= w <= max_side) and (min_side <= h <= max_side)

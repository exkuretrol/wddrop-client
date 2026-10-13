"""Where the message band is, computed from the frame size instead of calibrated.

A profile used to be measured by hand on one screen size, which is why only three sizes were
ever supported: a player on a fourth had nothing, and no amount of care with the three helped
them. The layout is a fixed arrangement scaled to the frame, so the band's position and the
width a line runs before wrapping follow from the frame size alone.

THE SIZE DOES NOT. `seed_em` is where a size search should start, not a size to read at — it
is a derived value and on one known frame size it is a pixel below what actually reads. The
caller is expected to try a pixel either side and keep whichever scores best, which is also
what makes this robust to the next frame size nobody has measured.

Everything here assumes the whole frame is usable. That holds for a desktop window and is
wrong where an OS reserves an edge, so `safe_area` is explicit rather than assumed. On a frame
far taller than it is wide the usable area is inset further still, by an amount this module
deliberately does not guess at — such a frame raises unless the caller says what is usable.
"""
from __future__ import annotations

from dataclasses import dataclass

# The arrangement these numbers describe is authored against this reference frame, and the
# whole of it scales by a single factor chosen so the reference fits inside the real frame.
REFERENCE = (1080.0, 1920.0)

# The band's panel, relative to the bottom edge of the usable area.
PANEL_BOTTOM_UP = 109.6
# The line's own box inside that panel: half its width across, and its top above the panel's
# bottom. Both are measured from the panel's bottom-centre, which is the point the Windows
# client's shrink leaves fixed.
LINE_HALF_WIDTH = 455.0
LINE_TOP_UP = 299.2
LINE_WIDTH = 910.0

# The Windows client draws this panel smaller than the reference arrangement does. It is not
# a setting and no frame size changes it.
WINDOWS_PANEL_SCALE = 0.898
# The size the line is drawn at, before the frame's scale and the panel's. See the module
# docstring: a starting point for a search, not an answer.
DRAWN_SIZE = 43.0

# Narrower than this and the usable area stops being the frame even with no reserved edge.
# The correction is NOT reproduced here, and the reason is not that it is unknown — the
# threshold it keys on is a published value and the inset follows from it. It is that no
# frame this client reads is ever that shape, so carrying it would put a branch nobody here
# exercises in the path that every supported frame takes, and the way the inset is split
# between the two edges is a reading rather than something stated. A caller that genuinely
# has such a frame knows its own usable area: it passes `safe_area` and is believed.
NARROW_FRAME_ASPECT = 0.52


@dataclass(frozen=True)
class BandGeometry:
    """Where a line starts and how far it runs, in frame pixels."""

    pen: tuple[float, float]   # left edge and top of the first line's box
    seed_em: float             # START a size search here; do not read at it
    wrap_width: float          # how far a line runs before the game breaks it
    scale: float               # frame pixels per reference unit


def band_geometry(frame_size: tuple[int, int], *, windows: bool = True,
                  safe_area: tuple[int, int, int, int] | None = None) -> BandGeometry:
    """The band's geometry on this frame size.

    `safe_area` is (x, y, width, height) of the usable area in frame pixels, defaulting to the
    whole frame. Passing the frame where an edge is actually reserved puts the band tens of
    pixels out, so it is a parameter rather than an assumption.
    """
    width, height = frame_size
    scale = min(width / REFERENCE[0], height / REFERENCE[1])
    if safe_area is None and width / height < NARROW_FRAME_ASPECT:
        raise ValueError(
            f"a {width}x{height} frame is too narrow for the usable area to be assumed; "
            "pass safe_area=(x, y, width, height). Guessing here would place the band tens "
            "of pixels out and still return a plausible-looking answer.")
    sx, sy, sw, sh = safe_area if safe_area is not None else (0, 0, width, height)

    panel = WINDOWS_PANEL_SCALE if windows else 1.0
    # Worked in reference units from the usable area's own edges, then scaled back out: the
    # arrangement is centred horizontally and sits a fixed distance off the bottom, so both
    # have to come from the area actually drawn into rather than from the frame.
    bottom = (sy + sh) / scale - PANEL_BOTTOM_UP
    centre = (sx + sw / 2.0) / scale
    return BandGeometry(
        pen=((centre - LINE_HALF_WIDTH * panel) * scale,
             (bottom - LINE_TOP_UP * panel) * scale),
        seed_em=DRAWN_SIZE * scale * panel,
        wrap_width=LINE_WIDTH * panel * scale,
        scale=scale,
    )

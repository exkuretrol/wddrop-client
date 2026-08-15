"""What the client can see — drawn, so it can be looked at.

WHY THIS EXISTS
---------------
Every expensive failure this project has had was invisible from where anyone would naturally
look. The HUD template that was a photograph of a rock face matched 13 frames in 2341, so
episodes never closed and four chests were recorded as one. The mining panel's band was not
in the list of strips live capture grabs, so mining could never be detected in a live session
while working perfectly on a recording. Neither looks like an error anywhere: the first is a
number that is merely low, the second is silence.

The reason both hide is stated in `strips` below — everything outside the strips is
composited BLACK, so a region left out is not read less accurately. It does not exist. That
is not a thing you can reason about from a log; it is a thing you look at.

TWO PICTURES, AND THEY ANSWER DIFFERENT QUESTIONS
-------------------------------------------------
    annotate()          where the client LOOKS   — the regions drawn on the real frame
    as_capture_sees()   what the client GETS     — the strips, and black everywhere else

The first catches a region in the wrong place. The second catches a region that is not in the
list at all, which is the one that cost a session, because a correctly-placed band still
reads nothing if capture never grabs it.

THE STRIPS HERE ARE THE STRIPS CAPTURE USES
--------------------------------------------
Not a second implementation of them. A preview that draws its own idea of the regions is a
picture that can disagree with the program while looking authoritative — which would make it
worse than having none, because it would be believed. `cmd_run` and the window both take
their strips from this function.
"""
from __future__ import annotations

# The colours the regions are drawn in. Distinct in greyscale too, since the frames this
# draws on are greyscale and a reader may well be looking at a printed or dimmed copy.
COLOURS = {
    "message band": (226, 204, 178),    # the ink colour the game writes drops in
    "HUD": (120, 200, 255),
    "mining panel": (255, 170, 90),
}


def strips(profile, record: bool, mining: bool = True):
    """The regions live capture actually grabs, as (x, y, w, h).

    Recording needs whole frames to be replayable; live capture does not, and grabbing a few
    strips instead of the whole window is what makes a high sample rate affordable — the
    measured cost of a full grab is most of the per-frame budget at 1920x1080.

    THE STRIPS ARE THE READABLE WORLD. Everything outside them is composited as black, so a
    region left out here is not "read less accurately" — it does not exist. Mining reports in
    a centred panel, and leaving that band out meant mining could never be detected in a live
    session at all, while working perfectly on a recording (which has whole frames). It cost
    a real session to find, and it is the reason this takes `mining` rather than quietly
    assuming the message band is the only place the game speaks.
    """
    if record:
        return None
    from .calibration import panel_columns, read_columns

    top, bottom = tuple(profile.message_band)
    w, h = tuple(profile.frame_size)
    # AS WIDE AS THE GAME WRITES, AND NO WIDER. The dialogue box is centred and the text is
    # left-aligned in it, so `text_x0` gives both its edges — at 1920x1080 that is 504 columns
    # of the 1920 this used to copy, for every strip, at every sample. What is cut away is
    # scenery the recogniser never reads: the comparison window is anchored at `text_x0` and
    # was always narrower than the frame.
    x0, x1 = read_columns(profile) or (0, w)
    out = [(x0, max(0, top - 4), x1 - x0, min(h, bottom + 4) - max(0, top - 4))]
    if profile.hud_region:
        left, upper, right, lower = profile.hud_region
        out.append((int(left * w), int(upper * h),
                    int((right - left) * w) + 1, int((lower - upper) * h) + 1))
    if mining:
        from .capture.panel import SEARCH_BOTTOM, SEARCH_TOP

        # THE PANEL'S OWN BOX, which is wider than the message band's — 644 columns against
        # 504 at 1920x1080. The ▼ that says the panel has finished drawing sits in the last
        # 70 of them, so a strip cut to the band's width does not contain it.
        px0, px1 = panel_columns(profile) or (0, w)
        panel_top = int(h * SEARCH_TOP)
        out.append((px0, panel_top, px1 - px0, int(h * SEARCH_BOTTOM) - panel_top))
    return out


def named_regions(profile, mining: bool = True):
    """(label, (x, y, w, h)) for each region, in the order they are drawn.

    Separate from `strips` because a label is for a person and `strips` is for the capture
    backend, which must not grow a dependency on how anything is described.
    """
    found = strips(profile, record=False, mining=mining) or []
    labels = ["message band"] + (["HUD"] if profile.hud_region else []) + \
             (["mining panel"] if mining else [])
    return list(zip(labels, found))


def annotate(frame, profile, mining: bool = True):
    """The frame with every region outlined and named. Returns a NEW RGB image.

    A copy, never the original: this is called on frames that are also being recognised, and
    a debugging aid that draws on the evidence is not one.
    """
    from PIL import ImageDraw

    canvas = frame.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    for label, (x, y, w, h) in named_regions(profile, mining):
        colour = COLOURS.get(label, (255, 255, 255))
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=colour, width=2)
        # Above the box where there is room, inside it where there is not — a label drawn off
        # the top of the frame is not a label.
        draw.text((x + 4, y - 14 if y >= 16 else y + 4), label, fill=colour)
    return canvas


def as_capture_sees(frame, profile, mining: bool = True):
    """The frame as the recogniser receives it live: the strips, and black everywhere else.

    This is the picture that answers "is the thing I am looking for even in the list", which
    is a different question from "is the region in the right place" and the one that is
    hardest to ask any other way.
    """
    from PIL import Image

    source = frame.convert("RGB")
    canvas = Image.new("RGB", source.size, (0, 0, 0))
    for _label, (x, y, w, h) in named_regions(profile, mining):
        box = (max(0, x), max(0, y), min(source.size[0], x + w), min(source.size[1], y + h))
        if box[2] > box[0] and box[3] > box[1]:
            canvas.paste(source.crop(box), (box[0], box[1]))
    return canvas

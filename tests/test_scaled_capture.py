"""
A captured frame that is an enlargement of a calibrated one.

WHY THIS EXISTS AT ALL: the game's fullscreen is always borderless and cannot be made
otherwise from outside — the mode it asks Windows for is fixed, and `-window-mode exclusive`
and the stored preference are both overwritten the moment it applies its own display
settings. Borderless means the window is the size of the DESKTOP while the render stays at
whatever the player chose, so a 1920x1080 game on a 2560x1440 screen is captured at
2560x1440.

The frame is not a new resolution to calibrate for. It is the same picture, enlarged.
"""
from __future__ import annotations

import sys


import pytest  # noqa: E402

pytest.importorskip("PIL.Image", reason="pillow not installed")

from PIL import Image  # noqa: E402

from wddrop_client.calibration import (  # noqa: E402
    MAX_CAPTURE_SCALE,
    scaled_from,
    ui_scale,
)
from wddrop_client.capture.source import Frame, ScaledSource, resample  # noqa: E402

CALIBRATED = [(1920, 1080), (1600, 900), (704, 1241)]


def test_the_common_screens_map_onto_a_calibrated_size():
    assert scaled_from((2560, 1440), CALIBRATED) == ((1920, 1080), pytest.approx(4 / 3))
    assert scaled_from((3840, 2160), CALIBRATED) == ((1920, 1080), 2.0)


def test_the_largest_calibrated_size_wins():
    """2560x1440 is 4/3 of 1920x1080 AND 1.6x of 1600x900, and both are 16:9. The smaller
    scale is the one that threw least away, so it is the one to read at."""
    size, scale = scaled_from((2560, 1440), CALIBRATED)
    assert size == (1920, 1080) and scale < 1.6


def test_a_different_aspect_is_refused_rather_than_guessed():
    """21:9 and 16:10 are not enlargements of a 16:9 render — the game either letterboxes or
    stretches, and which one it does cannot be known from the size alone. Refusing asks for
    a calibration; guessing would read the wrong rows and say nothing about it."""
    assert scaled_from((3440, 1440), [(1920, 1080)]) is None
    assert scaled_from((2560, 1600), [(1920, 1080)]) is None


def test_a_smaller_capture_is_never_enlarged():
    """A capture below the calibration holds less than the fit needs. Enlarging it would
    invent the detail the recogniser then scores against."""
    assert scaled_from((1280, 720), [(1920, 1080)]) is None
    assert scaled_from((1920, 1080), [(1920, 1080)]) is None      # exact: not this path's job


def test_an_absurd_scale_is_refused():
    assert scaled_from((1920 * 5, 1080 * 5), [(1920, 1080)]) is None
    assert scaled_from((int(1920 * MAX_CAPTURE_SCALE), int(1080 * MAX_CAPTURE_SCALE)),
                       [(1920, 1080)]) is not None


def test_the_render_resolution_cancels_out():
    """The reason a scaled frame may be read with a fit made at another size, and the reason
    the game's own resolution setting does NOT have to be known to do it.

    The UI is CanvasScaler/Expand against 1080x1920, so an element of U canvas units is
    U * height/1920 pixels. Render at R, let the compositor stretch R to the display D, and
    the element is U * D.h/1920 — R has cancelled. Resample D to the profile P and it is
    U * P.h/1920, which is what the profile was fitted on.
    """
    units = 100.0
    profile = (1920, 1080)
    display = (2560, 1440)
    for render in ((1280, 720), (1920, 1080), (2560, 1440)):
        on_screen = units * ui_scale(render) * (display[1] / render[1])
        after = on_screen * (profile[1] / display[1])
        assert after == pytest.approx(units * ui_scale(profile))


def test_a_whole_number_scale_is_undone_by_averaging():
    """4K -> 1080p is exactly 2x, so each output pixel is the mean of a 2x2 block — the
    enlargement run backwards. LANCZOS is for the scales that land between pixels."""
    big = Image.new("L", (3840, 2160), 0)
    big.paste(Image.new("L", (2, 2), 200), (100, 100))
    out = resample(big, (1920, 1080))
    assert out.size == (1920, 1080)
    assert out.getpixel((50, 50)) == 200                # the 2x2 block became one pixel


def test_resampling_leaves_a_matching_frame_alone():
    img = Image.new("L", (1920, 1080), 7)
    assert resample(img, (1920, 1080)) is img


def test_the_wrapper_resizes_frames_and_keeps_their_provenance():
    """The frame's source path is what a suspect reading is checked against later, so it has
    to survive the resample."""

    class Fake:
        def frames(self):
            yield Frame(t=1.5, image=Image.new("L", (2560, 1440), 3), source="episode-1/f_1.png")

    out = list(ScaledSource(Fake(), (1920, 1080)).frames())
    assert len(out) == 1
    assert out[0].image.size == (1920, 1080)
    assert out[0].t == 1.5 and out[0].source == "episode-1/f_1.png"


def test_live_capture_scales_each_strip_at_the_grab(monkeypatch):
    """The screen path reads STRIPS — under 2% of the pixels — and that is what caps the
    sample rate. Scaling must not turn it back into a full-frame grab, so each strip is
    asked for at its enlarged position and resampled on arrival onto a profile-sized canvas.
    """
    from wddrop_client.capture import source as src

    grabbed = []

    class FakeShot:
        def __init__(self, w, h):
            self.size = (w, h)
            self.bgra = bytes(w * h * 4)

    class FakeSct:
        monitors = [None, {"left": 0, "top": 0, "width": 2560, "height": 1440}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, box):
            grabbed.append(dict(box))
            return FakeShot(box["width"], box["height"])

    monkeypatch.setitem(sys.modules, "mss", type("m", (), {"mss": lambda: FakeSct()}))
    screen = src.ScreenSource(fps=1000.0, region=(0, 0, 2560, 1440),
                              strips=[(735, 870, 480, 20)], profile_size=(1920, 1080))
    frame = next(iter(screen.frames()))
    assert frame.image.size == (1920, 1080), "the canvas stays in the profile's pixels"
    assert len(grabbed) == 1, "still one strip, not a full frame"
    box = grabbed[0]
    # 4/3 of the strip, at 4/3 of its position.
    assert box["left"] == int(735 * 4 / 3) and box["top"] == int(870 * 4 / 3)
    assert box["width"] == pytest.approx(480 * 4 / 3, abs=2)
    assert box["height"] == pytest.approx(20 * 4 / 3, abs=2)


def test_the_game_is_asked_what_it_renders_rather_than_guessed(monkeypatch):
    """Resampling fixes the geometry and cannot fix the ink: over the same 15 confirmed
    lines, a 1920x1080 render through a 1440p screen kept min 0.8473, while a 1280x720 one
    fell to 0.5626 — under the 0.60 gate, so that reading is DROPPED and nothing says why.

    Unity records the player's own choice, so this is the game's answer and not an
    inference. Guarded rather than required: off Windows there is no registry, and a hint
    that cannot be read must never stop a session.
    """
    from wddrop_client.capture import window as win

    assert win.GAME_PREFS_KEY.endswith("WizardryVariantsDaphne")
    monkeypatch.setattr(win, "rendered_resolution", lambda: None)
    from wddrop_client import __main__ as cli

    cli._warn_if_the_game_renders_smaller((1920, 1080))          # must not raise


def test_the_warning_names_both_resolutions(capsys, monkeypatch):
    from wddrop_client.capture import window as win

    monkeypatch.setattr(win, "rendered_resolution", lambda: (1280, 720))
    from wddrop_client import __main__ as cli

    cli._warn_if_the_game_renders_smaller((1920, 1080))
    out = capsys.readouterr().out
    assert "1280x720" in out and "1920x1080" in out
    assert "DROPPED" in out, "the consequence, not just the mismatch"

"""
Provenance: each recorded item must name the frame it was actually read from.

The subtle case is the flush path. A line that appears on one frame and is gone the next is
recognised from a window captured EARLIER, so stamping "the current frame" points at the
blank frame that triggered the flush — off by one, in the direction that makes the evidence
useless: opening it shows nothing at all.
"""
from __future__ import annotations

from datetime import datetime, timezone


import pytest  # noqa: E402

pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from wddrop_client.calibration import Profile  # noqa: E402
from wddrop_client.capture.episodes import EpisodeTracker  # noqa: E402
from wddrop_client.capture.glyph import RenderMatch  # noqa: E402
from wddrop_client.capture.ocr import MessageFormat  # noqa: E402
from wddrop_client.capture.source import Frame  # noqa: E402
from wddrop_client.runner import CaptureRunner  # noqa: E402

ZH_TW = ("<color=#E2CCB2>獲得了{0}！！</color>", "{0}×{1}", None)
BAND = (10, 30)
W, H = 200, 60


class FakeRecognizer:
    """Returns a fixed name for any window with ink, so the test exercises the RUNNER."""

    min_margin = 0.03

    def __init__(self, name):
        self.name = name

    def recognize(self, window, observed_ink_width=None):
        return RenderMatch(name=self.name, score=0.9, margin=0.5, accepted=True,
                           runner_up=None, template_width=int(observed_ink_width or 50))


def frame_with_text(has_text: bool, source: str) -> Frame:
    arr = np.zeros((H, W), dtype=np.uint8)
    if has_text:
        arr[BAND[0] + 2 : BAND[1] - 2, 20:80] = 255
    return Frame(t=0.0, image=Image.fromarray(arr, mode="L"), source=source)


class FakeHud:
    """Reports the HUD present for frames whose source is marked 'walk'.

    The episode must actually CLOSE for a chest to be emitted; without a HUD signal the
    runner discards the open episode at session end, which would make this test pass or fail
    for the wrong reason.
    """

    def present(self, gray):
        return self._walking

    def __init__(self):
        self._walking = False


def make_runner(collected, hud=None):
    profile = Profile(frame_size=(W, H), message_band=BAND, font_path="x", font_size=10,
                      offset=(0, 0), calibration_score=1.0, window=(W, 20))
    fmt = MessageFormat(*ZH_TW)
    tracker = EpisodeTracker(fmt, "打開", lambda obs: None, stable_frames=1)
    runner = CaptureRunner(profile, FakeRecognizer("蒼藍礦石"), hud, tracker,
                           message_format=fmt, on_event=collected.append)
    return runner


def test_flushed_line_names_the_frame_that_SHOWED_it():
    """The line is on frame 2 and gone by frame 3; frame 2 must be recorded, not frame 3."""
    got: list[dict] = []
    hud = FakeHud()
    runner = make_runner(got, hud=hud)

    class Source:
        fps = 4.0

        def frames(self):
            yield frame_with_text(False, "episode-001/f_00001.png")
            yield frame_with_text(True, "episode-001/f_00002.png")   # the line
            yield frame_with_text(False, "episode-001/f_00003.png")  # gone -> flush
            hud._walking = True                                      # HUD back -> close
            yield frame_with_text(False, "episode-001/f_00004.png")

    runner.run(Source(), dungeon_id=1)
    assert got, "the vanished line should still have been recorded"
    sources = [c.get("source_frame") for e in got for c in e["contents"]]
    assert sources == ["episode-001/f_00002.png"], sources


def test_source_is_shortened_to_episode_and_frame():
    assert CaptureRunner._short_source("a/b/episode-006/f_00069.png") == "episode-006/f_00069.png"
    assert CaptureRunner._short_source("f_00069.png") == "f_00069.png"

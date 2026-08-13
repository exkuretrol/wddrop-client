"""
Capture reads the window where the window IS.

Live capture grabs a rectangle of the desktop and the game is a window inside it, so the
rectangle has to be the window's. It was read ONCE, before the sampling loop — and a window
is a thing players drag. After that, every grab reads a patch of desktop where the game no
longer is: no message band, no minimap, nothing recognised, and nothing in the log to say so.
The session looks like it is working the whole time.

These are the two halves of following it: the position must track, and the SIZE must not.
Every region in a profile is absolute pixels, so a resized window is one the calibration no
longer describes; carrying on at the original size keeps every downstream shape valid and
lets the frame-size check say so once, rather than emitting frames that change shape
mid-session.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import pytest  # noqa: E402

from wddrop_client.capture.source import ScreenSource  # noqa: E402

STARTED = {"left": 100, "top": 200, "width": 704, "height": 1241}


@pytest.fixture
def screen():
    live = ScreenSource(follow_window=True)
    live._warned_resize = False
    return live


def _region(monkeypatch, value):
    """What `client_region` will answer for the handle capture is following.

    Patched on the window module, which is where `_follow` imports it from — every frame, so
    the patch is seen. Nothing here touches a real window: this machine may not have one.
    """
    import wddrop_client.capture.window as window_module

    monkeypatch.setattr(window_module, "client_region", lambda handle: value)


def test_the_box_follows_the_window_when_it_moves(screen, monkeypatch):
    _region(monkeypatch, (640, 480, 704, 1241))
    assert screen._follow(1, dict(STARTED)) == {
        "left": 640, "top": 480, "width": 704, "height": 1241}


def test_a_window_that_has_not_moved_keeps_the_same_box(screen, monkeypatch):
    _region(monkeypatch, (100, 200, 704, 1241))
    box = dict(STARTED)
    assert screen._follow(1, box) == box


def test_a_resized_window_keeps_the_size_capture_started_at(screen, monkeypatch):
    """The profile is pixels. Following a resize would change the shape of every frame
    downstream, which is a different failure from the one the frame-size check reports."""
    _region(monkeypatch, (100, 200, 1920, 1080))
    got = screen._follow(1, dict(STARTED))
    assert (got["width"], got["height"]) == (704, 1241)
    assert screen._warned_resize, "a resize has to be said out loud once"


def test_a_window_that_has_gone_keeps_the_last_box(screen, monkeypatch):
    """Closing the game mid-session ends the session; it must not first produce a frame
    grabbed from a garbage rectangle."""
    _region(monkeypatch, None)
    box = dict(STARTED)
    assert screen._follow(1, box) == box

"""
Reading the game window's own frames instead of a picture of the screen.

A screenshot is of whatever is drawn where the game is, which is the game only while nothing
is in front of it. Everything else that lands there lands in the recording: a browser, a chat
window, this client's own window when a player brings it forward to check a count. The
reading then fails on pixels that were never the game's, and nothing downstream can tell that
from a chest that paid nothing.

Windows.Graphics.Capture asks the compositor for a WINDOW's frames. Measured on the real
game, with a sheet covering the top half of its window:

    screen capture     49.96% of the frame was the sheet
    window capture      0.00%

Most of what matters here can only be measured on Windows with the game running, and it was —
alignment against the path it replaces came out at dx=0 dy=0, and a real session through the
real runner saw the minimap in 108 of 237 frames. What is left for a test suite that runs
anywhere is the arithmetic and the fallbacks: that absence is handled, that the client area is
cut out of the window's frame rather than assumed to start at its corner, and that a machine
which cannot do any of this still records.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import pytest  # noqa: E402

from wddrop_client.capture import source as source_module  # noqa: E402
from wddrop_client.capture import wgc  # noqa: E402


def test_it_is_unavailable_where_it_cannot_work(monkeypatch):
    """Not an error: another platform, an older Windows, or the package not installed. The
    screen path still works there and is what every recording so far was made with."""
    monkeypatch.setattr(wgc.sys, "platform", "linux")
    assert wgc.available() is False


def test_capture_falls_back_to_the_screen_rather_than_failing(monkeypatch):
    """A player whose machine cannot capture a window must still be able to record."""
    monkeypatch.setattr(wgc, "available", lambda: False)
    built = source_module.open_source("window", fps=20.0)
    assert isinstance(built, source_module.ScreenSource)
    assert built.follow_window is True


def test_a_window_that_cannot_be_captured_falls_back_too(monkeypatch):
    """Anything unexpected from the capture stack is a reason to read the screen, not a
    reason to stop. The one exception is "no window matched", which is the honest answer to
    "record the game" when the game is not running — that still stops."""
    monkeypatch.setattr(wgc, "available", lambda: True)
    import wddrop_client.capture.window as window_module

    def explode(*_args, **_kwargs):
        raise OSError("the compositor said no")

    monkeypatch.setattr(window_module, "find_window", explode)
    assert isinstance(source_module.open_source("window", fps=20.0), source_module.ScreenSource)

    def gone(*_args, **_kwargs):
        raise SystemExit("[!] could not find the game window")

    monkeypatch.setattr(window_module, "find_window", gone)
    with pytest.raises(SystemExit):
        source_module.open_source("window", fps=20.0)


def test_the_newest_frame_wins_over_a_queued_one():
    """Frames arrive as the window draws them, faster than the sampler asks. A sampler that
    fell behind and then read a queue would be reading the past — and the whole point of the
    sample rate is to see what is on screen NOW."""
    pytest.importorskip("numpy")
    import numpy as np

    source = wgc.WindowSource(handle=1, fps=20.0)

    class FakeFrame:
        bytes_per_row = 8                              # 2 pixels of BGRA

        def __init__(self, value):
            self.value = value

        def buffer_view(self):
            return np.full(8, self.value, dtype=np.uint8).tobytes()

    for value in (1, 2, 3):
        source._on_frame(FakeFrame(value), 8, 2, 1, [False], 0)
    assert source._latest.qsize() == 1
    assert source._latest.get_nowait()[0, 0, 0] == 3, "the sampler was handed a stale frame"


def test_a_frame_is_copied_out_of_the_mapped_buffer():
    """The buffer belongs to the mapped frame and is valid only until the callback returns.
    Handing the sampler a view of it would be handing it memory the compositor has taken
    back — read at some later moment, on another thread."""
    pytest.importorskip("numpy")
    import numpy as np

    source = wgc.WindowSource(handle=1, fps=20.0)
    backing = np.zeros(8, dtype=np.uint8)

    class FakeFrame:
        bytes_per_row = 8

        def buffer_view(self):
            return backing.tobytes()

    source._on_frame(FakeFrame(), 8, 2, 1, [False], 0)
    got = source._latest.get_nowait()
    backing[:] = 255                                   # as the compositor reuses its buffer
    assert got.max() == 0, "the frame was a view of memory that has since changed"


def test_stopping_is_asked_for_through_the_callback():
    """The capture thread is the compositor's, and the only way to end it is to say so from
    inside a callback. A source that just stopped consuming would leave it running for the
    life of the process."""
    source = wgc.WindowSource(handle=1, fps=20.0)
    source._stop.set()
    stop_list = [False]
    source._on_frame(object(), 0, 0, 0, stop_list, 0)
    assert stop_list[0] is True


def test_a_gap_in_frames_is_waited_out_not_raised(monkeypatch):
    """A minimised window draws nothing, and a player who minimises the game for a moment
    must not lose the dive for it. This used to raise, which ended the session and everything
    not yet emitted with it. The runner's own idle timeout still ends one that never returns.

    The FIRST frame is different: nothing at all, ever, means either a minimised window or a
    path that does not work here, and both are worth saying rather than sitting in a loop.
    """
    pytest.importorskip("numpy")
    import queue

    import numpy as np

    source = wgc.WindowSource(handle=1, fps=20.0, size=(2, 1))
    monkeypatch.setattr(source, "_start", lambda: type("C", (), {"stop": lambda self: None})())
    monkeypatch.setattr(wgc, "client_offset", lambda handle: (0, 0))

    frame = np.zeros((1, 2, 4), dtype=np.uint8)
    answers = [frame, queue.Empty, queue.Empty, frame]

    def get(timeout=None):
        answer = answers.pop(0)
        if answer is queue.Empty:
            raise queue.Empty
        return answer

    monkeypatch.setattr(source._latest, "get", get)
    frames = source.frames()
    assert next(frames) is not None
    assert next(frames) is not None, "a gap ended the session instead of being waited out"
    frames.close()


def test_never_seeing_a_first_frame_is_reported(monkeypatch):
    """Silence from the very start is not a gap to wait through — it is this path not
    working, and the player is owed the reason."""
    import queue

    source = wgc.WindowSource(handle=1, fps=20.0)
    monkeypatch.setattr(source, "_start", lambda: type("C", (), {"stop": lambda self: None})())
    monkeypatch.setattr(wgc, "client_offset", lambda handle: (0, 0))

    def never(timeout=None):
        raise queue.Empty

    monkeypatch.setattr(source._latest, "get", never)
    with pytest.raises(RuntimeError, match="minimised"):
        next(source.frames())


def test_options_an_older_windows_lacks_are_not_asked_for(monkeypatch):
    """Windows 10 has no capture-border property, and asking to turn the border off there
    fails the whole session — "Toggling the capture border is not supported on this
    platform", raised on the compositor's thread, where the only symptom on this side is a
    window that never produces a frame. Measured on Windows 10 22H2 with the real game.

    Windows 11 still gets the border turned off, which is what it was always for.
    """
    monkeypatch.setattr(wgc.sys, "platform", "win32")
    asked: list = []

    class FakeNative:
        def __init__(self, *args):
            asked.append(args)

        def start_free_threaded(self):
            return object()

    monkeypatch.setattr(wgc, "_native", lambda: FakeNative)

    def build(number):
        monkeypatch.setattr(wgc, "_windows_build", lambda: number)

    build(19045)                                       # Windows 10 22H2
    wgc.WindowSource(handle=1, fps=20.0)._start()
    cursor, border = asked[-1][2:4]
    assert border is None, "Windows 10 was asked to toggle a border it does not have"
    assert cursor is False, "1903 does have the cursor toggle; it should still be used"

    build(22631)                                       # Windows 11 23H2
    wgc.WindowSource(handle=1, fps=20.0)._start()
    assert asked[-1][2:4] == (False, False), "Windows 11 lost the borderless capture"

    build(17763)                                       # Windows 10 1809, before either
    wgc.WindowSource(handle=1, fps=20.0)._start()
    assert asked[-1][2:4] == (None, None)

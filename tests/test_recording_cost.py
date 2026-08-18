"""
What recording a frame costs the capture loop.

The write happens on the capture thread, so it is not a background chore — it is time the
screen is not being sampled. That went unnoticed because nothing about it looks wrong: the
frames that ARE written are perfect, the session completes, the log reports the fps that was
asked for. What is missing is a frame that was never taken, and a mining panel dismissed
inside the gap is simply not in the recording for anything to find later.

Measured on a real session's frames, per frame:

    1920x1080   optimize=True 624ms / 177KB     compress_level=1  21ms / 285KB
     704x1241   optimize=True 348ms / 321KB     compress_level=1  17ms / 385KB

The player asked for 20fps and got 2.5 at 1080, median 448ms between frames.
"""
from __future__ import annotations

import time


import pytest  # noqa: E402

pytest.importorskip("numpy", reason="numpy not installed")
Image = pytest.importorskip("PIL.Image", reason="pillow not installed")

from wddrop_client.runner import RECORD_COMPRESS_LEVEL  # noqa: E402


def _frame(size=(1920, 1080)):
    """Noise, not flat colour: a blank image compresses to nothing and would hide the cost."""
    import numpy as np

    rng = np.random.default_rng(7)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0]), dtype="uint8"), "L")


def test_the_encoder_is_not_asked_to_optimize():
    """The flag that caused this: it re-encodes with several filter strategies to save a few
    percent of disk, on the thread that is supposed to be watching the screen."""
    assert RECORD_COMPRESS_LEVEL <= 3, "slow compression belongs off the capture thread"


def test_a_recorded_frame_is_written_with_that_setting(tmp_path, monkeypatch):
    """Wiring, not just the constant — the value is worthless if the call ignores it."""
    from wddrop_client.runner import CaptureRunner

    seen = {}
    original = Image.Image.save

    def spy(self, fp, *a, **kw):
        seen.update(kw)
        return original(self, fp, *a, **kw)

    monkeypatch.setattr(Image.Image, "save", spy)

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.record_dir = tmp_path
    runner._recorded = 0
    runner.record_limit = 10
    runner._episode_index = 1
    runner._episode_frame = 0
    runner.stats = {}
    runner._writer = None
    runner._write(_frame((320, 240)))
    runner._stop_writer()          # the write happens off-thread; wait for it

    assert seen.get("compress_level") == RECORD_COMPRESS_LEVEL
    assert not seen.get("optimize"), "optimize costs 30x the time for ~10% of the bytes"
    assert len(list(tmp_path.rglob("*.png"))) == 1


@pytest.mark.parametrize("size", [(704, 1241), (1920, 1080)])
def test_writing_a_frame_leaves_time_to_sample_the_next_one(size):
    """A budget, not a benchmark: at the 20fps the client offers, a frame is due every 50ms.
    Encoding is allowed a fraction of that, because recognition has to run too. Generous
    enough for a slow machine, and still 10x under what `optimize=True` was costing."""
    import io

    frame = _frame(size)
    frame.save(io.BytesIO(), "PNG", compress_level=RECORD_COMPRESS_LEVEL)   # warm
    start = time.perf_counter()
    for _ in range(3):
        frame.save(io.BytesIO(), "PNG", compress_level=RECORD_COMPRESS_LEVEL)
    each_ms = (time.perf_counter() - start) / 3 * 1000

    assert each_ms < 200, f"{each_ms:.0f}ms per frame at {size[0]}x{size[1]}"


def test_the_writer_does_not_run_on_the_capture_thread(tmp_path):
    """Encoding is not a background chore when it happens in line — it is time the screen is
    not being sampled, and a mining panel dismissed in that gap is not in the recording for
    anything to find later."""
    import threading

    from wddrop_client.runner import CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.record_dir, runner._recorded, runner.record_limit = tmp_path, 0, 100
    runner._episode_index, runner._episode_frame, runner.stats = 1, 0, {}
    here = threading.current_thread()
    seen = []
    original = Image.Image.save

    def spy(self, fp, *a, **kw):
        seen.append(threading.current_thread())
        return original(self, fp, *a, **kw)

    Image.Image.save = spy
    try:
        runner._write(_frame((320, 240)))
        runner._stop_writer()
    finally:
        Image.Image.save = original

    assert seen, "nothing was written"
    assert seen[0] is not here, "the frame was encoded on the capture thread"
    assert len(list(tmp_path.rglob("*.png"))) == 1


def test_a_slow_disk_costs_recorded_frames_never_sampled_ones(tmp_path, monkeypatch):
    """The queue is bounded and DROPS. A dropped frame is missing from the replay; a missed
    sample is missing from the data, which nothing can recover."""
    import queue

    from wddrop_client.runner import CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.record_dir, runner._recorded, runner.record_limit = tmp_path, 0, 1000
    runner._episode_index, runner._episode_frame, runner.stats = 1, 0, {}
    runner._writer = object()                       # pretend a writer is already running
    runner._write_queue = queue.Queue(maxsize=1)
    runner._write_queue.put(("occupied", tmp_path / "x.png"))

    runner._write(_frame((64, 64)))                 # the queue is full

    assert runner.stats.get("record_dropped") == 1
    assert runner._recorded == 0, "a dropped frame must not be counted as recorded"


def test_a_session_is_recorded_under_the_resolution_it_was_recorded_at():
    """`capture/1920x1080/session-...`, not `capture/session-...`.

    Everything about reading a frame is fitted per resolution — the band, the panel's box,
    the letter spacing, the HUD template — so a folder of sessions at mixed sizes has to be
    opened one session.json at a time before any question about a fault can even be asked.
    The folder is named the way the calibration for it is, so the two cannot drift apart.
    """
    from wddrop_client.calibration import ProfileStore
    from wddrop_client.__main__ import _session_record_dir

    landscape = _session_record_dir("/tmp/capture", (1920, 1080))
    assert landscape.parent.name == ProfileStore.key_for((1920, 1080)) == "1920x1080"
    assert landscape.name.startswith("session-")

    portrait = _session_record_dir("/tmp/capture", (704, 1241))
    assert portrait.parent.name == "704x1241"
    assert portrait.parent.parent == landscape.parent.parent

    # No size given (nothing in the client does this, but the helper is called with one
    # argument in tests and tools): the flat layout, exactly as before.
    assert _session_record_dir("/tmp/capture").parent.name == "capture"
    assert _session_record_dir(None) is None

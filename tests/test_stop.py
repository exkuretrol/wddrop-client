"""
Stopping a capture, and recording WHY it stopped.

Two things are being defended.

Cancellation must be COOPERATIVE. The loop is a generator pull over a frame source, so
before this its only exits were "the source ended" and Ctrl-C — neither of which a Stop
button in a window can produce. Stopping between frames also keeps the episode machine
consistent: a chest still open at that moment is emitted as truncated, which killing the
thread would skip.

And the REASON must survive. Manual stop puts the session boundary under the player's
control, and stopping may be outcome-dependent — quitting right after a good drop, or after
a bad streak, correlates session end with drop quality and can manufacture the very pattern
the study tests for. StopReason exists in the schema for that comparison; nothing was
filling it in.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


import pytest  # noqa: E402

pytest.importorskip("PIL.Image")

from wddrop_client.capture.episodes import EpisodeTracker  # noqa: E402
from wddrop_client.capture.ocr import MessageFormat  # noqa: E402
from wddrop_client.runner import CaptureRunner, record_stop_reason  # noqa: E402

ZH_TW = ("<color=#E2CCB2>獲得了{0}！！</color>", "{0}×{1}",
         "Msg@<color=#E2CCB2>但是裡面什麼都沒有……</color>")


class Frames:
    """A frame source that counts how many frames were actually pulled."""

    fps = 4.0

    def __init__(self, count=10_000, on_frame=None):
        from PIL import Image

        self.count = count
        self.on_frame = on_frame
        self.pulled = 0
        self._image = Image.new("RGB", (64, 32), 0)

    def frames(self):
        for i in range(self.count):
            self.pulled += 1
            if self.on_frame:
                self.on_frame(self)
            yield SimpleNamespace(image=self._image, t=i / self.fps, source=None)


def make_runner(profile_size=(64, 32)):
    profile = SimpleNamespace(
        frame_size=profile_size, message_band=(0, 8), window=(64, 16), font_size=8,
        letter_spacing=0.0, offset=(0, 0), text_x0=0,
    )
    tracker = EpisodeTracker(MessageFormat(*ZH_TW), "打開", lambda obs: None)
    return CaptureRunner(
        profile, recognizer=None, hud_detector=None, tracker=tracker,
        message_format=MessageFormat(*ZH_TW), on_event=lambda e: None,
    )


def test_stop_ends_the_loop_without_exhausting_the_source():
    runner = make_runner()
    # The source sets the flag as it hands over frame 5.
    source = Frames(on_frame=lambda s: runner.stop() if s.pulled == 5 else None)
    stats = runner.run(source, dungeon_id=7015)
    # The flag is read at the TOP of the loop, so frame 5 is pulled and then dropped: four
    # frames are processed and nothing is half-processed. That is the point of checking
    # between frames rather than interrupting inside one.
    assert stats["frames"] == 4
    assert source.pulled == 5
    assert stats["stop_reason"] == "user_stop"


def test_stop_can_come_from_another_thread():
    """The whole point: a Stop button lives in the UI thread, the loop does not."""
    runner = make_runner()
    started = threading.Event()
    source = Frames(on_frame=lambda s: started.set())

    def stopper():
        started.wait(5)
        runner.stop("app_closed")

    t = threading.Thread(target=stopper)
    t.start()
    stats = runner.run(source, dungeon_id=7015)
    t.join()
    assert stats["stop_reason"] == "app_closed"
    assert stats["frames"] < source.count


def test_source_running_out_is_not_reported_as_a_user_stop():
    """A recording that ends and a player who pressed Stop are different observations, and
    only the caller knows which of game-closed / end-of-recording applies."""
    runner = make_runner()
    stats = runner.run(Frames(count=3), dungeon_id=7015)
    assert stats["frames"] == 3
    assert stats["stop_reason"] is None


def test_idle_timeout_is_its_own_reason():
    runner = make_runner()
    runner.idle_timeout = 0.5
    stats = runner.run(Frames(count=20), dungeon_id=7015)
    assert stats["stop_reason"] == "idle_timeout"


def test_ctrl_c_is_recorded_as_a_user_stop():
    def boom(source):
        if source.pulled == 3:
            raise KeyboardInterrupt

    runner = make_runner()
    with pytest.raises(KeyboardInterrupt):
        runner.run(Frames(on_frame=boom), dungeon_id=7015)
    assert runner.stop_reason == "user_stop"


# -- stamping the reason onto the events -----------------------------------------
def write_spool(path, dives):
    path.write_text("\n".join(
        json.dumps({"event_id": f"e{i}", "dive": {"dive_id": d}}, ensure_ascii=False)
        for i, d in enumerate(dives)) + "\n", encoding="utf-8")


def test_only_this_dive_is_stamped(tmp_path):
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, ["dive-a", "dive-b", "dive-a"])
    assert record_stop_reason("dive-a", "user_stop", spool) == 2
    reasons = [json.loads(l)["dive"].get("stop_reason")
               for l in spool.read_text(encoding="utf-8").splitlines()]
    assert reasons == ["user_stop", None, "user_stop"]


def test_an_existing_reason_is_not_overwritten(tmp_path):
    spool = tmp_path / "spool.jsonl"
    spool.write_text(json.dumps(
        {"event_id": "e0", "dive": {"dive_id": "d", "stop_reason": "idle_timeout"}}) + "\n",
        encoding="utf-8")
    assert record_stop_reason("d", "user_stop", spool) == 0


def test_unparseable_lines_are_kept(tmp_path):
    """The uploader treats a malformed line as recoverable after a fix, so this must not be
    the thing that finally deletes it."""
    spool = tmp_path / "spool.jsonl"
    spool.write_text('{"event_id": "e0", "dive": {"dive_id": "d"}}\nnot json at all\n',
                     encoding="utf-8")
    record_stop_reason("d", "user_stop", spool)
    lines = spool.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "not json at all"
    assert json.loads(lines[0])["dive"]["stop_reason"] == "user_stop"


def test_nothing_is_written_without_a_dive_or_a_reason(tmp_path):
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, ["d"])
    before = spool.read_text(encoding="utf-8")
    assert record_stop_reason(None, "user_stop", spool) == 0
    assert record_stop_reason("d", None, spool) == 0
    assert spool.read_text(encoding="utf-8") == before


def test_reaching_the_frame_cap_is_reported_not_just_logged(tmp_path):
    """A player who ticked "keep the frames" is relying on them. At 20fps in "all" mode the
    cap is about three minutes — and one real session ran nearly five, so its last ninety
    seconds were never saved. The player kept playing, believing otherwise, and the frames
    that would have explained a missed drop did not exist.

    The DROPS keep being recorded either way. Only the pictures stop, and that is what the
    window has to be able to say.
    """
    from PIL import Image

    from wddrop_client.runner import CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.record_dir = tmp_path
    runner._recorded = 0
    runner.record_limit = 2
    runner._episode_index = 1
    runner._episode_frame = 0
    runner.stats = {}
    runner._writer = None

    frame = Image.new("RGB", (32, 24))
    runner._write(frame)
    assert "record_capped" not in runner.stats, "reported before the cap was reached"
    runner._write(frame)
    runner._stop_writer()

    assert runner.stats["record_capped"] == 2
    assert len(list(tmp_path.rglob("*.png"))) == 2, "the cap is a stop, not a warning"


# -- pausing, which is not a short stop -------------------------------------------------
#
# Stopping ends the dive, and the dive is what `elapsed_seconds` is measured against — so a
# player who stops to restock and starts again has cut one farming run into two, which is
# the exact shape the study is looking for in the data, put there by the interface. Pause
# keeps the dive and simply stops reading.


def test_a_paused_loop_reads_nothing_but_keeps_pulling_frames():
    """Pulling continues on purpose. The source owns its own clock and its own buffers, so
    abandoning the generator to sleep would mean resuming into whatever a live capture had
    piled up while nobody was looking."""
    runner = make_runner()
    runner.pause()
    source = Frames(count=40)

    stats = runner.run(source, dungeon_id=7015)

    assert source.pulled == 40, "a paused loop stopped consuming the source"
    assert stats["paused_frames"] == 40
    assert stats["frames"] == 40 and stats["hud_present"] == 0
    assert stats["recognised"] == 0, "a paused loop read the screen"


def test_stop_works_while_paused():
    """The stop flag is checked BEFORE the pause flag, so the button does not need the
    session resumed first — which a player pressing Stop on a paused session would never
    think to do."""
    runner = make_runner()
    runner.pause()
    source = Frames(on_frame=lambda s: runner.stop("user_stop") if s.pulled == 5 else None)

    runner.run(source, dungeon_id=7015)

    assert runner.stop_reason == "user_stop"
    assert source.pulled == 5


def test_a_long_pause_does_not_trip_the_idle_timeout():
    """Otherwise a break longer than the timeout ends the session silently, and the player
    comes back to a client that says it is recording and is not."""
    runner = make_runner()
    runner.idle_timeout = 2.0
    runner.pause()
    # 4 fps over 200 frames is 50 seconds of frame time, far past the timeout.
    stats = runner.run(Frames(count=200), dungeon_id=7015)

    assert runner.stop_reason != "idle_timeout"
    assert stats["paused_frames"] == 200


def test_the_time_spent_paused_travels_with_the_records_rather_than_being_subtracted():
    """`elapsed_seconds` is the study's independent variable and its meaning — wall time
    since entering — must not quietly change under data already collected. So the pause is
    reported beside it in QC and the analysis does the subtraction itself, on evidence."""
    runner = make_runner()

    def toggle(source):
        if source.pulled == 4:
            runner.pause()
        elif source.pulled == 24:               # 20 frames at 4 fps = 5 seconds
            runner.resume()

    runner.run(Frames(count=30, on_frame=toggle), dungeon_id=7015)

    assert runner.paused_seconds == pytest.approx(5.0, abs=0.5)
    assert runner._pause_qc() == {"paused_seconds": 5}
    # Below a second it is not worth a QC key: every session would then carry a
    # `paused_seconds: 0` that says nothing.
    assert make_runner()._pause_qc() == {}

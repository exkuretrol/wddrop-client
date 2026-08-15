"""When a session stops, the chest still being read is stamped with WHEN IT STOPPED.

A chest still open at stop time is emitted rather than discarded — a truncated record can be
excluded on evidence later, a missing one cannot. It was stamped `start + 0s`, so the last
chest of a session was written at 00:00, before every chest that preceded it, and with an
`occurred_at` equal to the dive's own start:

    13:03:11  chest #3   <- opened at 13:09:33, stopped while its lines were being read
    13:05:20  chest #1
    13:07:57  chest #2

Reported as "the last chest is written as 00:00 when I stop the recording while it is being
parsed", which is exactly what it was.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

pytest.importorskip("numpy")

from wddrop_client.capture.episodes import EpisodeTracker  # noqa: E402
from wddrop_client.capture.ocr import MessageFormat  # noqa: E402

START = datetime(2026, 8, 14, 13, 3, 11, tzinfo=timezone.utc)


def _tracker(seen):
    fmt = MessageFormat("<color=#E2CCB2>{0}を手に入れた!!</color>", "{0}×{1}")
    tracker = EpisodeTracker(fmt, "開ける", seen.append, stable_frames=1)
    tracker.start_session(START)
    return tracker


def test_a_chest_truncated_by_the_stop_is_stamped_when_the_stop_happened():
    seen = []
    tracker = _tracker(seen)
    opened = START + timedelta(seconds=382)
    tracker.tick(opened, False, "蒼雫の鉱石塊×10を手に入れた!!")
    # Stop, six minutes and some into the dive, with the episode still open.
    tracker.stop_session(START + timedelta(seconds=384))

    assert len(seen) == 1, "the chest was dropped rather than emitted as truncated"
    chest = seen[0]
    assert chest.truncated is True
    assert chest.elapsed_seconds == 384, "stamped with the session start, not the stop"
    assert chest.occurred_at == START + timedelta(seconds=384)


def test_it_is_still_the_last_chest_of_the_session_in_time_order():
    """The property that actually broke: sorting the session's records by time put the
    truncated one first, so it read as the chest before the dive began."""
    seen = []
    tracker = _tracker(seen)
    tracker.tick(START + timedelta(seconds=100), False, "蒼雫の鉱石塊×1を手に入れた!!")
    tracker.tick(START + timedelta(seconds=110), True, "")          # HUD closes chest one
    tracker.tick(START + timedelta(seconds=300), False, "獅子奮迅の証×5を手に入れた!!")
    tracker.stop_session(START + timedelta(seconds=305))

    assert [c.elapsed_seconds for c in seen] == sorted(c.elapsed_seconds for c in seen)
    assert seen[-1].truncated is True

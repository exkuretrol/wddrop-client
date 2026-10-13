"""The embedding API: reading is pure, recording is this client's."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wddrop_client.embed import (DiveHandle, DropLine, Geometry, Recorder, Refusal,
                                 Refused)


def _recorder(mode="autopilot"):
    r = Recorder.__new__(Recorder)
    r.config = SimpleNamespace(install_id="00000000-0000-4000-8000-000000000000")
    r.capture_mode, r.locale = mode, "ja"
    return r


def test_geometry_comes_from_the_window_size_with_no_calibration():
    g = Geometry.for_window((1366, 768), font_path="atlas.ja.scenario.json")
    assert g.band[0] < g.band[1] and g.size > 0
    assert g.columns[0] < g.columns[1]


def test_waiting_helps_for_one_refusal_and_not_the_other():
    """THE DISTINCTION THIS API EXISTS TO PRESERVE. A line still being typed finishes on a
    later frame, so waiting is right. A line under the score gate scores the same on every
    frame of the same geometry, so waiting is a loop with no exit — the size is wrong. They
    look identical from outside, which is why they are separate values."""
    assert Refused(Refusal.UNTERMINATED).wait_for_another_frame is True
    assert Refused(Refusal.BELOW_GATE).wait_for_another_frame is False
    assert Refused(Refusal.AMBIGUOUS).wait_for_another_frame is False


def test_the_dive_context_rides_on_every_record():
    """The server has NO dives table — dive_id, dungeon_id and floor_id are columns on the
    drop event. A caller keeping its own dive log is welcome to, but that log cannot reach
    the study, so what it knows has to be stamped here."""
    rec = _recorder()
    now = datetime.now(timezone.utc)
    dive = DiveHandle(rec, dungeon_id=7015, floor_id=3, label_source="screen_read",
                      started_at=now)
    ev = rec.build_event(DropLine("蒼雫の鉱石", 2, 0.91, 0.08), dive_id=dive.dive_id,
                         started_at=now, occurred_at=now + timedelta(seconds=7),
                         dungeon_id=dive.dungeon_id, floor_id=dive.floor_id,
                         label_source=dive.label_source, chest_index=1)
    assert ev["dive"]["dungeon_id"] == 7015 and ev["dive"]["floor_id"] == 3
    assert ev["dive"]["label_source"] == "screen_read"
    assert ev["dive"]["elapsed_seconds"] == 7
    assert ev["capture"]["mode"] == "autopilot"


def test_an_unread_quantity_is_one_AND_flagged_exactly_as_the_player_client_does():
    """Matching the other path is the point, not a coincidence.

    The wire format requires a quantity of at least 1, so something must be written when no
    number was on screen; the flag is what separates "read a 1" from "assumed a 1". If this
    path flagged differently from the player client's, `qty_unknown` would mean one thing for
    a player's rows and another for a bot's — and the study pools them.
    """
    rec = _recorder()
    now = datetime.now(timezone.utc)
    ev = rec.build_event(DropLine("呪符ブリザガ", None, 0.88, 0.07), dive_id="d",
                         started_at=now, occurred_at=now, dungeon_id=None, floor_id=None,
                         label_source="screen_read", chest_index=1)
    item = ev["contents"][0]
    assert item["quantity"] == 1 and item["qty_unknown"] is True

    read = rec.build_event(DropLine("蒼雫の鉱石", 2, 0.91, 0.08), dive_id="d",
                           started_at=now, occurred_at=now, dungeon_id=None, floor_id=None,
                           label_source="screen_read", chest_index=2)
    assert read["contents"][0]["quantity"] == 2
    assert "qty_unknown" not in read["contents"][0]


def test_the_event_validates_against_the_wire_format():
    """Not a shape this file invented: the server rejects anything else, and the enum values
    this path depends on only exist from schema v0.2.0."""
    schema = pytest.importorskip("wddrop_schema.models")
    rec = _recorder()
    now = datetime.now(timezone.utc)
    ev = rec.build_event(DropLine("蒼雫の鉱石", 2, 0.91, 0.08),
                         dive_id="11111111-1111-4111-8111-111111111111", started_at=now,
                         occurred_at=now, dungeon_id=7015, floor_id=3,
                         label_source="screen_read", chest_index=1, qc={"fps": 16.0})
    parsed = schema.DropEvent.model_validate(ev)
    assert parsed.capture.mode.value == "autopilot"
    assert parsed.dive.label_source.value == "screen_read"
    assert parsed.contents[0].quantity == 2


def test_recording_into_a_closed_dive_is_refused():
    """A dive is closed when its reason has been stamped onto the events it produced. A drop
    arriving after that would carry a reason that was decided without it."""
    rec = _recorder()
    dive = DiveHandle(rec, dungeon_id=None, floor_id=None, label_source="screen_read",
                      started_at=datetime.now(timezone.utc))
    dive._closed = True
    with pytest.raises(RuntimeError, match="closed"):
        dive.record(DropLine("蒼雫の鉱石", 1, 0.9, 0.1))


def test_a_record_reaches_both_files(tmp_path, monkeypatch):
    """An outbox the uploader drains and an archive nothing removes from. One file cannot be
    both — draining the outbox once took the player's own copy with it."""
    from wddrop_client import config

    monkeypatch.setattr(config, "spool_path", lambda: tmp_path / "spool.jsonl")
    monkeypatch.setattr(config, "records_path", lambda: tmp_path / "records.jsonl")
    Recorder.write({"event_id": "abc", "contents": []})
    for name in ("spool.jsonl", "records.jsonl"):
        written = json.loads((tmp_path / name).read_text(encoding="utf-8").strip())
        assert written["event_id"] == "abc"

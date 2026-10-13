"""The embedding API: reading is pure, recording is this client's."""
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wddrop_client.embed import (DiveHandle, DropLine, EmptyChest, Geometry, Recorder,
                                 Refusal, Refused)


def _recorder(mode="autopilot"):
    r = Recorder.__new__(Recorder)
    r.config = SimpleNamespace(install_id="00000000-0000-4000-8000-000000000000")
    r.capture_mode, r.locale = mode, "ja"
    r._spool = r._records = None          # the client's own paths, as a default Recorder has
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
    ev = rec.build_event([DropLine("蒼雫の鉱石", 2, 0.91, 0.08)], dive_id=dive.dive_id,
                         started_at=now, occurred_at=now + timedelta(seconds=7),
                         dungeon_id=dive.dungeon_id, floor_id=dive.floor_id,
                         label_source=dive.label_source, chest_index=1)
    assert ev["dive"]["dungeon_id"] == 7015 and ev["dive"]["floor_id"] == 3
    assert ev["dive"]["label_source"] == "screen_read"
    assert ev["dive"]["elapsed_seconds"] == 7
    assert ev["capture_mode"] == "autopilot"


def test_an_unread_quantity_is_one_AND_flagged_exactly_as_the_player_client_does():
    """Matching the other path is the point, not a coincidence.

    The wire format requires a quantity of at least 1, so something must be written when no
    number was on screen; the flag is what separates "read a 1" from "assumed a 1". If this
    path flagged differently from the player client's, `qty_unknown` would mean one thing for
    a player's rows and another for a bot's — and the study pools them.
    """
    rec = _recorder()
    now = datetime.now(timezone.utc)
    ev = rec.build_event([DropLine("呪符ブリザガ", None, 0.88, 0.07)], dive_id="d",
                         started_at=now, occurred_at=now, dungeon_id=None, floor_id=None,
                         label_source="screen_read", chest_index=1)
    item = ev["contents"][0]
    assert item["quantity"] == 1 and item["qty_unknown"] is True

    read = rec.build_event([DropLine("蒼雫の鉱石", 2, 0.91, 0.08)], dive_id="d",
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
    ev = rec.build_event([DropLine("蒼雫の鉱石", 2, 0.91, 0.08)],
                         dive_id="11111111-1111-4111-8111-111111111111", started_at=now,
                         occurred_at=now, dungeon_id=7015, floor_id=3,
                         label_source="screen_read", chest_index=1, qc={"fps": 16.0})
    from wddrop_client.uploader import hydrate

    cfg = SimpleNamespace(install_id="00000000-0000-4000-8000-000000000000", locale="ja",
                          progress_width=0, progress_bits=0, character_grade=None)
    parsed = hydrate(ev, cfg, schema.CaptureMode.OCR)
    # OCR was passed in and the record says autopilot. The RECORD wins — otherwise a player
    # draining a shared spool would relabel the bot's rows as their own.
    assert parsed.capture.mode.value == "autopilot"
    assert parsed.capture.qc.get("fps") == 16.0, "qc must survive the drain"
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
        dive.chest([DropLine("蒼雫の鉱石", 1, 0.9, 0.1)])


def test_a_record_reaches_both_files(tmp_path, monkeypatch):
    """An outbox the uploader drains and an archive nothing removes from. One file cannot be
    both — draining the outbox once took the player's own copy with it."""
    from wddrop_client import config

    monkeypatch.setattr(config, "spool_path", lambda: tmp_path / "spool.jsonl")
    monkeypatch.setattr(config, "records_path", lambda: tmp_path / "records.jsonl")
    _recorder().write({"event_id": "abc", "contents": []})
    for name in ("spool.jsonl", "records.jsonl"):
        written = json.loads((tmp_path / name).read_text(encoding="utf-8").strip())
        assert written["event_id"] == "abc"


def test_one_chest_with_several_lines_is_ONE_event(tmp_path):
    """A chest can print several messages in a row, and the wire format's first line says the
    unit of analysis is one chest open with its FULL content list. Recording per message turns
    one chest into several holding one item each — inflating the chest count and deflating the
    items per chest, silently, in the two numbers the study exists to measure."""
    rec = _recorder()
    now = datetime.now(timezone.utc)
    ev = rec.build_event([DropLine("骸の歪な霊珊", 1, 0.9, 0.1),
                          DropLine("落石の巻物", 2, 0.88, 0.09)],
                         dive_id="d", started_at=now, occurred_at=now, dungeon_id=None,
                         floor_id=None, label_source="screen_read", chest_index=1)
    assert len(ev["contents"]) == 2
    assert [c["item_name"] for c in ev["contents"]] == ["骸の歪な霊珊", "落石の巻物"]
    assert ev["dive"]["chest_index_in_dive"] == 1


def test_a_chest_index_counts_chests_not_lines(tmp_path, monkeypatch):
    rec = _recorder()
    monkeypatch.setattr(rec, "write", lambda event: None)
    dive = rec.open_dive(dungeon_id=1, floor_id=2)
    dive.chest([DropLine("a", 1, 0.9, 0.1), DropLine("b", 1, 0.9, 0.1)])
    dive.chest([DropLine("c", 1, 0.9, 0.1)])
    assert dive._chests == 2


def test_an_empty_chest_is_a_real_outcome():
    """Recorded on positive evidence or not at all: `EmptyChest` comes from recognising the
    line the game writes, never from having read nothing."""
    assert EmptyChest(score=0.82).score == 0.82
    ev = _recorder().build_event([], dive_id="d", started_at=datetime.now(timezone.utc),
                                 occurred_at=datetime.now(timezone.utc), dungeon_id=None,
                                 floor_id=None, label_source="screen_read", chest_index=4)
    assert ev["contents"] == [] and ev["dive"]["chest_index_in_dive"] == 4


def test_the_bot_can_keep_its_records_away_from_a_players(tmp_path):
    """One install_id across both would put a player's own rows and their bot's behind one
    erasure handle, and let either process drain the other's outbox."""
    rec = _recorder()
    rec._spool = tmp_path / "bot" / "spool.jsonl"
    rec._records = tmp_path / "bot" / "records.jsonl"
    rec.write({"event_id": "x", "contents": []})
    assert (tmp_path / "bot" / "spool.jsonl").exists()
    assert (tmp_path / "bot" / "records.jsonl").exists()


def test_draining_never_raises_into_a_farming_loop():
    """An unattended caller cannot act on an exception, and the records are already on disk."""
    rec = _recorder()
    rec._spool = Path("/does/not/exist/spool.jsonl")
    out = rec.drain()
    assert out["uploaded"] == 0 and "error" in out or out["uploaded"] == 0


def test_the_floor_is_recorded_per_chest_because_a_dive_crosses_floors():
    """A dive descends to the farm floor and passes others on the way out, and drop tables are
    keyed per floor — so a dive-level floor would stamp the collecting floor onto chests found
    somewhere else. The dive's floor is only a fallback for a dive that stayed put."""
    rec = _recorder()
    seen = []
    rec.write = seen.append
    dive = rec.open_dive(dungeon_id=7015, floor_id=701503)
    dive.chest([DropLine("骸の歪な霊珊", 1, 0.9, 0.1)], floor_id=701504)
    dive.chest([DropLine("落石の巻物", 1, 0.9, 0.1)])          # falls back to the dive's
    assert [e["dive"]["floor_id"] for e in seen] == [701504, 701503]


def test_lines_that_were_seen_but_never_read_are_counted():
    """A chest with unread lines has an INCOMPLETE content list. Counting it as a complete
    observation understates what chests hold, so the number has to be on the record for
    analysis to exclude it."""
    rec = _recorder()
    seen = []
    rec.write = seen.append
    dive = rec.open_dive(dungeon_id=7015)
    dive.chest([DropLine("骸の歪な霊珊", 1, 0.9, 0.1)], unread=2)
    dive.chest([DropLine("落石の巻物", 1, 0.9, 0.1)])
    assert seen[0]["qc"]["unread_lines"] == 2
    assert "unread_lines" not in seen[1]["qc"], "a clean chest must not carry a zero"

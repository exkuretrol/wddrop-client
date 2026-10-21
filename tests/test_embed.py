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


# -- the mining panel ------------------------------------------------------------------

def test_the_tie_break_is_reachable_where_ties_actually_happen():
    """The entry gate must not be HIGHER than the bar the tie-break itself accepts.

    Graded families — 下級/中級/上級/特上級鉄鉱石 — differ by one character in five, so the
    identical rest of the line drowns the difference and the whole-line score lands BELOW the
    reading gate by construction. Gating entry to the tie-break at that same reading score
    therefore excluded exactly the band it was built for.

    Measured on a real panel frame: 上級鉄鉱石 scored 0.6082 with a 0.0189 margin over its
    sibling 下級鉄鉱石 — the right name, refused — and the tie-break never ran, because
    0.6082 was under a 0.70 entry gate while the tie-break accepts a fit of 0.60.
    """
    import inspect

    from wddrop_client import embed

    src = inspect.getsource(embed.PanelReader.read)
    assert "match.score >= MINING_TIE_MIN_SCORE" in src, (
        "the tie-break must be entered at its own bar, not at the whole-line reading gate")
    assert embed.MINING_TIE_MIN_SCORE <= 0.70


def test_a_panel_reader_takes_its_size_from_the_caller():
    """No default size, because the drawn size is platform-dependent.

    A 1080-wide phone draws this panel at 39 where the authored size is 40, and the Windows
    factor for this prefab is unconfirmed. A default here would be one platform's measurement
    presented as a derivation — which is how the chest band's 43 travelled across a platform
    boundary it never claimed.
    """
    import inspect

    from wddrop_client.embed import PanelReader

    sig = inspect.signature(PanelReader.__init__)
    assert sig.parameters["size"].default is inspect.Parameter.empty, (
        "size must be required: it differs per platform and must not be guessed")
    assert sig.parameters["columns"].kind is inspect.Parameter.KEYWORD_ONLY


def test_break_detection_is_off_unless_the_caller_asks():
    """A false break is worse than a missed one, so it is opt-in.

    An index of three candidates has almost nothing for a wrong answer to lose to: the same
    index reported a pickaxe breaking TWICE in a session containing no mining at all, and the
    true hit could not be separated from the false ones by score. A caller that counts breaks
    for itself — as the bot does — should keep doing that.
    """
    import inspect

    from wddrop_client.embed import PanelReader

    assert inspect.signature(PanelReader.__init__).parameters["pickaxes"].default is None


def test_the_embedded_reader_has_the_tie_break_the_standalone_one_has():
    """The whole point of this API is that an embedding caller gets the SAME recogniser.

    It did not. `break_tie` was wired into the standalone client's band path and never into
    `LineReader`, so a bot calling this module ran a strictly weaker reader than the same
    library used by itself, with nothing saying so. Measured: a junk family that differs by
    one character in seventeen read ZERO times in 195 bot-read lines, and reads at a 0.44
    margin once the tie-break is reached.
    """
    import inspect

    from wddrop_client.embed import LineReader

    src = inspect.getsource(LineReader.read)
    assert "break_tie" in src, "LineReader must use the same tie-break as the runner"
    assert "MINING_TIE_MARGIN" in src and "MINING_TIE_MIN_SCORE" in src, (
        "both gates, or the margin says which of two fits better without either fitting")


def test_a_tie_broken_reading_is_marked_as_one():
    """`score` beside a tie-broken name is the WHOLE-LINE score, low by construction.

    An unflagged tie-break would put 0.83 next to a 0.44 margin with no way to tell which
    mechanism produced it, and analysis ranking on confidence would rate the most deliberate
    readings worst.
    """
    import dataclasses

    from wddrop_client.embed import DropLine

    fields = {f.name for f in dataclasses.fields(DropLine)}
    assert "tie_broken" in fields
    assert DropLine("x", 1, 0.9, 0.1).tie_broken is False, "ordinary readings stay unflagged"


def test_the_flag_reaches_the_record_not_just_the_reader():
    """Four fields have been set by a client and dropped before reaching the database here —
    `qty_unknown`, `stop_reason`, `truncated`, `label_source`. This one is written."""
    import inspect

    from wddrop_client.embed import Recorder

    assert 'item["tie_broken"]' in inspect.getsource(Recorder._item)


def test_the_recorder_takes_a_line_that_predates_its_newest_field(tmp_path):
    """Adding a field must not break callers who pass their own line objects.

    This recorder accepts duck-typed lines, so reading a new attribute directly turns every
    added field into a hard break for every embedder not watching these commits. `tie_broken`
    did exactly that: the bot passes its own line type, and its next start would have raised
    AttributeError on the first chest, with its watcher switching recording off for the run.
    Caught by wd-autopilot before a restart.
    """
    import json

    from wddrop_client.embed import Recorder

    class OldLine:                      # no tie_broken, as an older caller's line has none
        name, quantity, score = "ランペール金貨", 2, 0.8891

    rec = Recorder(spool=tmp_path / "spool.jsonl", records=tmp_path / "records.jsonl")
    rec.open_dive(dungeon_id=7015).chest([OldLine()])

    row = json.loads((tmp_path / "records.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["contents"][0]["item_name"] == "ランペール金貨"
    assert "tie_broken" not in row["contents"][0], "absent means absent, not False"
    # `truncated` was read the same way and had the same fault BEFORE tie_broken existed, so
    # the contract was already narrower than it claimed. Fixing one and not the other would
    # leave the next caller to find it.
    assert row["truncated"] is False


def test_an_empty_chest_is_a_zero_only_when_the_game_said_so(tmp_path):
    """An invented zero biases the drop rate as much as a dropped one, and is undetectable.

    The game prints 「しかし、何も入っていなかった……」 for a chest that held nothing, so a
    measured zero is one where that was READ. Everything else with no contents is a chest we
    failed to read — and the two were arriving identically.

    Found on a real record: a TRAP chest printed 「おおっと!」 and the trap's name, neither of
    which is an item, so both lines were refused and the chest reached the server as
    `contents: []`, `truncated: false` — indistinguishable from a chest that gave nothing. The
    empty class is the bottom of the distribution the study exists to measure.
    """
    import json

    from wddrop_client.embed import DropLine, Recorder

    rec = Recorder(spool=tmp_path / "s.jsonl", records=tmp_path / "r.jsonl")
    dive = rec.open_dive(dungeon_id=7015)
    dive.chest([], unread=1)                                  # the trap
    dive.chest([], empty_confirmed=True)                      # the game said so
    dive.chest([DropLine("ランペール金貨", 2, 0.89, 0.4)])      # ordinary

    rows = [json.loads(l) for l in
            (tmp_path / "r.jsonl").read_text(encoding="utf-8").splitlines()]
    trap, empty, ordinary = rows

    assert trap["truncated"] is True, "an unread chest must not pass as a measured zero"
    assert trap["qc"]["empty_unconfirmed"] is True
    assert empty["truncated"] is False and empty["contents"] == [], "a confirmed zero is real"
    assert "empty_unconfirmed" not in empty["qc"]
    assert ordinary["truncated"] is False and len(ordinary["contents"]) == 1


def test_a_confirmed_empty_chest_cannot_also_have_contents(tmp_path):
    """The game cannot have said both, so a caller claiming both has a bug upstream."""
    import pytest

    from wddrop_client.embed import DropLine, Recorder

    rec = Recorder(spool=tmp_path / "s.jsonl", records=tmp_path / "r.jsonl")
    dive = rec.open_dive(dungeon_id=7015)
    with pytest.raises(ValueError):
        dive.chest([DropLine("ランペール金貨", 1, 0.9, 0.4)], empty_confirmed=True)


def test_consent_recorded_through_the_api_lets_a_drain_run(tmp_path, monkeypatch):
    """The whole point: the drain must SEE what the host recorded.

    wd-autopilot asked its own consent question, got a yes, and wrote the right hash into its
    own `consent.json`. The drain reads `ClientConfig.consent` from the data_dir and refused
    about fifty times with `ConsentRequired` while the records piled up in the spool. The two
    sides never disagreed about the answer — they disagreed about where it lived.

    So this asserts the join, not the parts: record through the API, then drain, and the
    refusal must be gone. Uploading itself is stubbed out; what is under test is consent.
    """
    from wddrop_client import uploader
    from wddrop_client.consent import disclaimer_text
    from wddrop_client.embed import Recorder, accept_consent, consented

    rec = Recorder(capture_mode="autopilot", locale="ja", data_dir=tmp_path)
    assert consented(tmp_path) is False
    before = rec.drain()
    assert "ConsentRequired" in str(before.get("error", "")), before

    accept_consent(tmp_path, shown_text=disclaimer_text())
    assert consented(tmp_path) is True

    sent = []
    monkeypatch.setattr(uploader, "upload_spool",
                        lambda *a, **k: sent.append(1) or
                        {"uploaded": 0, "remaining": 0, "rejected": 0, "held": 0})
    after = Recorder(capture_mode="autopilot", locale="ja", data_dir=tmp_path).drain()
    assert "ConsentRequired" not in str(after.get("error", "")), after
    assert sent, "the drain must have reached the uploader once consent was recorded"


def test_consent_cannot_be_recorded_for_a_disclaimer_that_was_not_shown(tmp_path):
    """`accept_consent` takes the TEXT, not a boolean, and this is why.

    Nothing in a library can verify that a human was asked — that honesty belongs to the
    caller. What it CAN refuse is a record of consent for wording this client would not show:
    a host that cached the disclaimer from an older version and displayed that got agreement
    to something other than what is current. Passing the text catches it; a bare
    `accept_consent(data_dir)` could not.
    """
    import pytest

    from wddrop_client.consent import disclaimer_text
    from wddrop_client.embed import accept_consent, consented

    with pytest.raises(ValueError):
        accept_consent(tmp_path, shown_text=disclaimer_text() + "\n(and one extra clause)")
    assert consented(tmp_path) is False, "a refused acceptance must record nothing"


def test_consent_can_be_withdrawn(tmp_path):
    """An API that can only say yes is not a consent API."""
    from wddrop_client.consent import disclaimer_text
    from wddrop_client.embed import accept_consent, consented, revoke_consent

    accept_consent(tmp_path, shown_text=disclaimer_text())
    assert consented(tmp_path) is True
    revoke_consent(tmp_path)
    assert consented(tmp_path) is False

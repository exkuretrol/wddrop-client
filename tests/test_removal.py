"""
Which records a player is offered a Delete button on.

THIS IS THE TEST THAT MATTERS, more than the plumbing that removes the row. A delete button
on every record is a button that deletes good data, and the one class of record it must never
appear on is the EMPTY chest: it is the worst outcome and a real observation, so removing
those one shrug at a time deletes the bottom of the distribution and inflates every drop rate
the study measures — in exactly the direction the study exists to test for.

The rest is the same judgement from the other side: a row is offered only where the client
itself was unsure, because that is where the player has evidence nobody here will ever have.
They were looking at the screen.
"""
from __future__ import annotations

import json
import uuid


from wddrop_client.removal import (NEAR_THE_GATE, is_imprecise,
                                   remove_record, why_imprecise)


def a_record(**over) -> dict:
    """A clean reading: every line placed, every quantity observed, nothing flagged."""
    event = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": "2026-08-17T04:00:00+00:00",
        "provenance": "chest_direct",
        "qc": {"fps": 20.0},
        "contents": [{"item_name": "蒼雫の鉱石", "item_id": 20000001, "quantity": 3,
                      "match_confidence": 0.91}],
    }
    event.update(over)
    return event


# -- what is offered, and what is not ---------------------------------------------------


def test_a_clean_reading_is_not_offered_a_delete_button():
    assert why_imprecise(a_record()) == []


def test_an_empty_chest_is_never_offered_one():
    """The worst outcome and a real observation. A button here would let the bottom of the
    distribution be deleted away, and every measured rate would rise because of it."""
    assert why_imprecise(a_record(contents=[])) == []


def test_a_marker_is_never_offered_one():
    """A dive mark or a broken pickaxe is local, is never uploaded, and has nothing to take
    back."""
    assert why_imprecise({"provenance": "marker", "note": "pickaxe broke",
                          "contents": []}) == []


def test_an_inferred_quantity_is_offered_one():
    """The game prints no number for a single item, for equipment, or under a drop boost, so
    the client records 1 and flags it. On the first real spool 40 of 94 lines were inferred
    and NOT ONE observed line was a 1 — this is the flag that keeps that visible."""
    record = a_record(contents=[{"item_name": "蒼雫の鉱石", "item_id": 1, "quantity": 1,
                                 "qty_unknown": True}])
    assert is_imprecise(record)


def test_a_name_that_could_not_be_placed_is_offered_one():
    record = a_record(contents=[{"item_name": "???", "quantity": 1}])
    assert is_imprecise(record)


def test_a_reading_that_scraped_the_gate_is_offered_one():
    """Accepted, but close to where it would have been refused. `NEAR_THE_GATE` is the panel
    reader's own confidence floor rather than a number invented here, so "the client was not
    comfortable" means one thing in this program."""
    assert is_imprecise(a_record(contents=[
        {"item_name": "ウロボロス鉱石", "item_id": 2, "quantity": 1,
         "match_confidence": NEAR_THE_GATE - 0.01}]))
    assert not is_imprecise(a_record(contents=[
        {"item_name": "ウロボロス鉱石", "item_id": 2, "quantity": 1,
         "match_confidence": NEAR_THE_GATE}]))


def test_the_qc_flags_are_offered_one():
    for qc in ({"panel_lines_unread": 1}, {"panel_lines_tie_broken": 1},
               {"label_conflict": True}):
        assert is_imprecise(a_record(qc=qc)), qc
    assert is_imprecise(a_record(truncated=True))


def test_one_reason_is_said_once_however_many_lines_carry_it():
    """Several lines of one chest hit the same reason all the time, and the same sentence
    three times reads as three different problems."""
    record = a_record(contents=[
        {"item_name": "a", "item_id": 1, "quantity": 1, "qty_unknown": True},
        {"item_name": "b", "item_id": 2, "quantity": 1, "qty_unknown": True},
        {"item_name": "c", "item_id": 3, "quantity": 1, "qty_unknown": True}])
    assert len(why_imprecise(record)) == 1


# -- removing one -----------------------------------------------------------------------


def test_removing_a_record_takes_it_out_of_the_players_own_copy(tmp_path, monkeypatch):
    """The one exception to "nothing deletes from records.jsonl", and it is deliberate.
    Everywhere else that file is the archive; here the player is not skipping an upload,
    they are saying the reading is wrong — and leaving it would keep counting at them on
    their own Stats page and in their own export."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from wddrop_client.config import ClientConfig, records_path, spool_path

    doomed, kept = a_record(), a_record()
    for path in (spool_path(), records_path()):
        path.write_text("".join(json.dumps(e) + "\n" for e in (doomed, kept)),
                        encoding="utf-8")

    remove_record(ClientConfig(), doomed["event_id"])

    for path in (spool_path(), records_path()):
        left = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
        assert [e["event_id"] for e in left] == [kept["event_id"]], path.name


def test_a_line_that_cannot_be_parsed_is_kept(tmp_path, monkeypatch):
    """It is not the one being removed — nothing can say it is — and the uploader already
    treats an unreadable line as recoverable after a fix rather than as rubbish."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from wddrop_client.config import ClientConfig, records_path, spool_path

    doomed = a_record()
    for path in (spool_path(), records_path()):
        path.write_text("{not json at all\n" + json.dumps(doomed) + "\n", encoding="utf-8")

    remove_record(ClientConfig(), doomed["event_id"])

    assert spool_path().read_text(encoding="utf-8").strip() == "{not json at all"

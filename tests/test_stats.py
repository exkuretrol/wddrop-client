"""
The Stats page's numbers.

It counts the PLAYER'S COPY. Anything keyed off uploads would show a player with sharing
off that they had recorded nothing, and would drop to zero the moment the outbox drained —
which is exactly how the export once handed back an empty file.

And it counts, rather than rating. A drop rate needs a denominator that survives scrutiny;
one player's dives are not a random sample of anything, and the study answers that question
with a pre-registered analysis over pooled data instead.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

from wddrop_client.stats import summarise  # noqa: E402

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def event(*contents, provenance="chest_direct", dungeon=7015, minutes=0, event_id=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "occurred_at": (NOW + timedelta(minutes=minutes)).isoformat(),
        "provenance": provenance,
        "contents": [dict(c) for c in contents],
        "dive": {"dive_id": str(uuid.uuid4()), "dungeon_id": dungeon},
    }


def write(path: Path, *events) -> Path:
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                    encoding="utf-8")
    return path


def test_it_counts_openings_lines_and_the_two_sources(tmp_path):
    records = write(
        tmp_path / "records.jsonl",
        event({"item_name": "莫尼翁銀幣", "quantity": 2},
              {"item_name": "北穿幽靈城的四鱗雜物", "quantity": 1}),
        event({"item_name": "莫尼翁銀幣", "quantity": 3}),
        event({"item_name": "下級鐵礦石", "quantity": 6}, provenance="mining"),
    )
    data = summarise(records)
    assert (data["openings"], data["chests"], data["veins"]) == (3, 2, 1)
    assert data["lines"] == 4
    assert data["items"] == 3
    assert data["dungeons"] == {7015: 3}
    assert data["first"][:10] == "2026-08-10"


def test_an_empty_chest_is_an_observation_not_a_gap(tmp_path):
    """The worst outcome is still an outcome. Dropping it would delete the bottom of the
    distribution and inflate everything measured against it."""
    records = write(tmp_path / "records.jsonl",
                    event(), event({"item_name": "莫尼翁銀幣", "quantity": 1}))
    data = summarise(records)
    assert data["openings"] == 2
    assert data["empty"] == 1
    assert data["lines"] == 1


def test_inferred_quantities_are_counted_apart_from_observed_ones(tmp_path):
    """`quantity = 1` with the flag set means the game printed no number. On the first real
    spool 40 of 94 lines were inferred and NOT ONE observed line was a 1, so a total that
    merged them would be mostly assumption presented as measurement."""
    records = write(
        tmp_path / "records.jsonl",
        event({"item_name": "朗佩爾金幣", "quantity": 1, "qty_unknown": True}),
        event({"item_name": "朗佩爾金幣", "quantity": 4}),
    )
    row = summarise(records)["by_item"][0]
    assert (row["openings"], row["quantity"], row["inferred"]) == (2, 5, 1)


def test_the_numbers_do_not_move_when_the_outbox_is_drained(tmp_path):
    """The point of the page. Uploading changes what has been SENT, never what was seen."""
    records = write(tmp_path / "records.jsonl",
                    event({"item_name": "莫尼翁銀幣", "quantity": 2}))
    spool = write(tmp_path / "spool.jsonl",
                  event({"item_name": "莫尼翁銀幣", "quantity": 2}))

    before = summarise(records, spool)
    assert before["unsent"] == 1

    spool.write_text("", encoding="utf-8")            # a successful upload
    after = summarise(records, spool)

    assert after["unsent"] == 0
    assert after["openings"] == before["openings"] == 1
    assert after["by_item"] == before["by_item"]


def test_a_duplicated_line_is_counted_once(tmp_path):
    """The archive is append-only and a replayed session could write an event twice; the
    same opening counted twice would be a fabricated observation."""
    shared = str(uuid.uuid4())
    records = write(tmp_path / "records.jsonl",
                    event({"item_name": "莫尼翁銀幣", "quantity": 2}, event_id=shared),
                    event({"item_name": "莫尼翁銀幣", "quantity": 2}, event_id=shared))
    assert summarise(records)["openings"] == 1


def test_markers_are_not_openings(tmp_path):
    """Dive markers and pickaxe notes are the player's own annotations. Counting them as
    openings would inflate the denominator of everything."""
    records = write(tmp_path / "records.jsonl",
                    event({"item_name": "莫尼翁銀幣", "quantity": 1}),
                    event(provenance="marker"))
    assert summarise(records)["openings"] == 1


def test_no_file_yet_is_zero_rather_than_an_error(tmp_path):
    data = summarise(tmp_path / "nothing.jsonl", tmp_path / "none.jsonl")
    assert data["openings"] == 0 and data["by_item"] == [] and data["unsent"] == 0


def test_items_are_ordered_most_seen_first_and_stably(tmp_path):
    records = write(
        tmp_path / "records.jsonl",
        event({"item_name": "b", "quantity": 1}, {"item_name": "a", "quantity": 1}),
        event({"item_name": "c", "quantity": 1}, {"item_name": "a", "quantity": 1}),
    )
    rows = summarise(records)["by_item"]
    assert [r["item"] for r in rows] == ["a", "b", "c"]


# -- picking a day ---------------------------------------------------------------------

def _record(when: str, provenance: str = "chest_direct", items=("A",)):
    return {"event_id": when + provenance + "".join(items), "provenance": provenance,
            "occurred_at": when, "dive": {"dungeon_id": 7015},
            "contents": [{"item_name": n, "quantity": 1} for n in items]}


def _written(tmp_path, events):
    import json

    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def test_a_day_is_a_JST_day_not_a_local_or_utc_one(tmp_path):
    """The game resets at 00:00 JST and the study buckets on that, so this page has to as
    well. A player in Taiwan mining at 23:30 local is already on the next JST day, and two
    numbers that should agree and do not is worse than one number."""
    from wddrop_client.stats import jst_day, summarise

    assert jst_day("2026-08-10T14:59:00+00:00") == "2026-08-10"   # 23:59 JST
    assert jst_day("2026-08-10T15:00:00+00:00") == "2026-08-11"   # 00:00 JST, next day

    path = _written(tmp_path, [_record("2026-08-10T14:59:00+00:00"),
                               _record("2026-08-10T15:01:00+00:00")])
    days = {row["day"]: row["openings"] for row in summarise(path)["days"]}
    assert days == {"2026-08-10": 1, "2026-08-11": 1}


def test_choosing_a_day_narrows_the_counts_but_not_the_overall(tmp_path):
    """A day on its own says nothing about whether it was a good one. The comparison IS the
    reason to offer days, so the overall totals come back either way."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [
        _record("2026-08-10T01:00:00+00:00", items=("A", "B")),
        _record("2026-08-11T01:00:00+00:00", items=("C",)),
    ])
    one = summarise(path, day="2026-08-10")
    assert one["openings"] == 1 and one["lines"] == 2
    assert one["overall"]["openings"] == 2 and one["overall"]["lines"] == 3
    assert [r["item"] for r in one["by_item"]] == ["A", "B"], "another day leaked in"


def test_an_unknown_day_shows_nothing_rather_than_everything(tmp_path):
    """Failing open would present another day's numbers as that day's."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [_record("2026-08-10T01:00:00+00:00")])
    empty = summarise(path, day="2026-01-01")
    assert empty["openings"] == 0 and empty["by_item"] == []
    assert empty["overall"]["openings"] == 1


def test_the_days_offered_are_the_days_recorded_newest_first(tmp_path):
    """Never a calendar: a date with nothing behind it reads as a day the player recorded
    nothing, which is a different claim from "you did not play"."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [_record("2026-08-09T01:00:00+00:00"),
                               _record("2026-08-11T01:00:00+00:00"),
                               _record("2026-08-10T01:00:00+00:00")])
    assert [r["day"] for r in summarise(path)["days"]] == [
        "2026-08-11", "2026-08-10", "2026-08-09"]


def test_a_repeated_event_is_counted_once_on_its_day(tmp_path):
    """The spool is replayed on restart, so duplicates reach the file."""
    from wddrop_client.stats import summarise

    one = _record("2026-08-10T01:00:00+00:00")
    path = _written(tmp_path, [one, dict(one)])
    data = summarise(path)
    assert data["openings"] == 1 and data["days"][0]["openings"] == 1


# -- chests and veins are different questions -------------------------------------------

def _mined(when: str, items):
    return {"event_id": when + "mining", "provenance": "mining", "occurred_at": when,
            "dive": {"dungeon_id": 7015},
            "contents": [{"item_name": n, "quantity": q} for n, q in items]}


def _broke(when: str, n: int = 1):
    return [{"event_id": f"marker-{when}-{i}", "provenance": "marker",
             "note": "pickaxe broke", "occurred_at": when, "contents": []} for i in range(n)]


def test_the_two_sources_are_countable_apart(tmp_path):
    """Pooling them puts 582 pebbles beside 32 shells and calls the result a distribution."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [
        _record("2026-08-10T01:00:00+00:00", items=("shell",)),
        _mined("2026-08-10T02:00:00+00:00", [("pebble", 9), ("ore", 3)]),
    ])
    both, chest, vein = (summarise(path, source=s) for s in (None, "chest", "vein"))
    assert (both["openings"], chest["openings"], vein["openings"]) == (2, 1, 1)
    assert {r["item"] for r in chest["by_item"]} == {"shell"}
    assert {r["item"] for r in vein["by_item"]} == {"pebble", "ore"}
    assert vein["total_quantity"] == 12


def test_a_share_is_of_what_was_counted_not_of_everything(tmp_path):
    """Filtered to veins, a pebble's share is its share OF THE ORE — otherwise the number
    changes meaning depending on a dropdown, which is worse than not showing it."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [
        _record("2026-08-10T01:00:00+00:00", items=("shell",)),
        _mined("2026-08-10T02:00:00+00:00", [("pebble", 3), ("ore", 1)]),
    ])
    vein = summarise(path, source="vein")
    shares = {r["item"]: round(r["share"], 3) for r in vein["by_item"]}
    assert shares == {"pebble": 0.75, "ore": 0.25}
    assert [r["of_top"] for r in vein["by_item"]] == [1.0, pytest.approx(1 / 3)]


def test_broken_pickaxes_are_counted_and_are_not_openings(tmp_path):
    """A break gives nothing, so it must not enter the item table or the opening count — but
    it is the denominator for everything mining, so it has to be counted somewhere."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [_mined("2026-08-10T02:00:00+00:00", [("ore", 3)])]
                    + _broke("2026-08-10T02:30:00+00:00", 2))
    data = summarise(path)
    assert data["broken"] == 2
    assert data["openings"] == 1
    assert [r["item"] for r in data["by_item"]] == ["ore"]


def test_the_days_offered_do_not_change_with_the_source(tmp_path):
    """The days a player can PICK must not depend on which source they are looking at, or
    choosing "veins" makes a day they mined nothing disappear from the list."""
    from wddrop_client.stats import summarise

    path = _written(tmp_path, [
        _record("2026-08-10T01:00:00+00:00"),
        _mined("2026-08-11T01:00:00+00:00", [("ore", 1)]),
    ])
    days = [[r["day"] for r in summarise(path, source=s)["days"]] for s in (None, "chest", "vein")]
    assert days[0] == days[1] == days[2] == ["2026-08-11", "2026-08-10"]

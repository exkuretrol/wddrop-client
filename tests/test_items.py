"""
Item identity across languages.

The client asks a player to set the game to Japanese, so from that point their records are
Japanese-named and everything before is not. Names are what the screen shows; the ID is what
makes the two poolable — and what lets the window answer in the language the player chose
rather than the one the game is in.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import pytest  # noqa: E402

from wddrop_client.items import ItemIndex, ItemNames  # noqa: E402

VOCAB = {"items": [{"name": "透明な小石", "id": 20000000, "type": "Item::Junk"},
                   {"name": "下級鉄鉱石", "id": 470000000, "type": "Item::SaleOnly"}],
         "equipment": [{"name": "鉄の剣", "identification": 900, "ids": [1, 2]}]}
NAMES = {"locale": "zh_tw", "items": {"20000000": "透明鵝卵石", "470000000": "下級鐵礦石"},
         "equipment": {"900": "鐵劍"}}


def test_a_recognised_name_carries_its_id():
    index = ItemIndex.from_vocab(VOCAB)
    assert index.identify("透明な小石") == {"item_id": 20000000}
    # Equipment by its FAMILY key: a displayed name does not identify a single row, and the
    # ids differ per roll.
    assert index.identify("鉄の剣") == {"equipment_identification": 900}


def test_a_name_that_resolves_to_nothing_is_left_alone():
    """A gap the analysis can see beats an id that points at the wrong item."""
    assert ItemIndex.from_vocab(VOCAB).identify("something misread") == {}
    assert ItemIndex().identify("透明な小石") == {}


def test_an_item_is_shown_in_the_language_of_the_window():
    """Their game is Japanese because this client asked. Their window may not be."""
    names = ItemNames("zh_tw", NAMES["items"], NAMES["equipment"])
    assert names.display({"item_name": "透明な小石", "item_id": 20000000}) == "透明鵝卵石"
    assert names.display({"item_name": "鉄の剣", "equipment_identification": 900}) == "鐵劍"


def test_without_a_table_it_shows_what_was_on_screen():
    """Never wrong, only in the wrong language — which beats showing nothing."""
    empty = ItemNames()
    assert empty.display({"item_name": "透明な小石", "item_id": 20000000}) == "透明な小石"
    assert len(empty) == 0


def test_an_id_the_table_does_not_have_falls_back_to_the_reading():
    """A game update adds items before the table is rebuilt."""
    names = ItemNames("zh_tw", NAMES["items"], {})
    assert names.display({"item_name": "新しい何か", "item_id": 999}) == "新しい何か"


def test_a_missing_or_broken_table_is_not_fatal(tmp_path):
    assert len(ItemNames.load(None)) == 0
    assert len(ItemNames.load(tmp_path / "nope.json")) == 0
    broken = tmp_path / "names.zh_tw.json"
    broken.write_text("{not json", encoding="utf-8")
    assert len(ItemNames.load(broken)) == 0


def test_the_same_item_pools_across_a_language_change(tmp_path):
    """The reason the id is recorded at all. Grouping by name would file one item as two the
    moment a player switches — which this client asks them to do."""
    from wddrop_client.stats import summarise

    rows = [
        {"event_id": "a", "provenance": "mining", "occurred_at": "2026-08-11T02:00:00+00:00",
         "contents": [{"item_name": "透明な小石", "item_id": 20000000, "quantity": 6}]},
        {"event_id": "b", "provenance": "mining", "occurred_at": "2026-08-11T02:05:00+00:00",
         "contents": [{"item_name": "透明鵝卵石", "item_id": 20000000, "quantity": 9}]},
    ]
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")

    by_item = summarise(path)["by_item"]
    assert len(by_item) == 1, "the same item was counted as two"
    assert by_item[0]["quantity"] == 15 and by_item[0]["openings"] == 2


def test_a_line_with_no_id_still_counts_under_its_name(tmp_path):
    """Everything recorded before ids existed, and anything the vocabulary cannot resolve."""
    from wddrop_client.stats import summarise

    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps({
        "event_id": "a", "provenance": "chest_direct",
        "occurred_at": "2026-08-11T02:00:00+00:00",
        "contents": [{"item_name": "何か", "quantity": 2}]}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    by_item = summarise(path)["by_item"]
    assert [(r["item"], r["quantity"]) for r in by_item] == [("何か", 2)]


def test_it_reads_a_stats_row_as_readily_as_a_recorded_line():
    """The two carry the same identity under different keys. Requiring the caller to know
    which is how the stats page came to show "?" for every item it could have named."""
    names = ItemNames("zh_tw", NAMES["items"], NAMES["equipment"])
    line = {"item_name": "透明な小石", "item_id": 20000000}
    row = {"item": "透明な小石", "item_id": 20000000, "quantity": 6}
    assert names.display(line) == names.display(row) == "透明鵝卵石"
    # And with no id on either shape, the recorded name still comes back.
    assert ItemNames().display({"item": "何か"}) == "何か"

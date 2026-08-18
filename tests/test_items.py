"""
Item identity across languages.

The client asks a player to set the game to Japanese, so from that point their records are
Japanese-named and everything before is not. Names are what the screen shows; the ID is what
makes the two poolable — and what lets the window answer in the language the player chose
rather than the one the game is in.
"""
from __future__ import annotations

import json
from pathlib import Path


import pytest

from wddrop_client.items import ItemIndex, ItemNames

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


# -- what a vein can hand you --------------------------------------------------------

def _vein_pool():
    """The fixture as ENTRIES rather than a file, so nothing here writes JSON to disk."""
    from wddrop_client.capture.ocr import VocabEntry
    from wddrop_client.items import from_a_vein

    entries = [
        # the two ore id blocks, one mined name from each
        VocabEntry("透明な小石", 20000000, "Item::SaleOnly"),
        VocabEntry("水晶輝石", 200000060, "Item::SaleOnly"),
        # ORE, in the right id block, that a vein here does not produce — the coin-like
        # exchange materials and the two other regions' families. See NOT_FROM_A_VEIN_IDS.
        VocabEntry("錆びついた古銭", 200000090, "Item::SaleOnly"),
        VocabEntry("紅焔の輝鉱石", 200000160, "Item::SaleOnly"),
        VocabEntry("雪光輝鉱石", 200000200, "Item::SaleOnly"),
        # SaleOnly, but keepsakes and developer rows rather than ore
        VocabEntry("王家の指輪", 20100001, "Item::SaleOnly"),
        VocabEntry("使ってない　後で消す", 20100019, "Item::SaleOnly"),
        VocabEntry("テスト虫除け（仮）", 99100001, "Item::SaleOnly"),
        VocabEntry("大盛り金貨", 200110070, "Item::SaleOnly"),
        VocabEntry("聖白の輝石", 200110020, "Item::SaleOnly"),
        # the other two types, kept whole
        VocabEntry("銀鉱石", 470000040, "Item::EquipmentReinforceMaterial"),
        VocabEntry("ウロボロス鉱石", 400009000, "Item::EquipmentSubEffectChange"),
        # everything a vein does not produce
        VocabEntry("北穿の幽霊城のガラクタ", 119060637, "Item::Junk"),
        VocabEntry("ルンゴナンゴ翠貝貨", 471000020, "Item::RelicEquipmentMaterial"),
        VocabEntry("金の針", 300000010, "Item::Expendable"),
        VocabEntry("朧丸", None, None, 110101110),
    ]
    return from_a_vein(entries)


def test_a_vein_produces_ore_and_nothing_else():
    """The panel was scored against everything the message band is — 2,384 names, equipment
    included — and a vein produces none of that. It cost a real record: 「朧丸」, a katana,
    came back from a vein and the player confirmed it as a misread of an ore name. A wrong
    name in the answer space does not fail, it WINS, and nothing marks it."""
    pool = set(_vein_pool())
    assert {"透明な小石", "水晶輝石", "銀鉱石", "ウロボロス鉱石"} <= pool
    assert "朧丸" not in pool, "a vein does not hand over equipment"
    for absent in ("北穿の幽霊城のガラクタ", "ルンゴナンゴ翠貝貨", "金の針"):
        assert absent not in pool, absent


def test_ore_a_vein_here_does_not_produce_is_out_as_well():
    """Being ore and coming out of a vein are different questions, and the id block only
    answers the first. The eleven excluded are the project owner's judgement from the drop
    tables; what a reader can check without them is that the coin-like three carry
    `item_icon_cash_material` rather than an ore icon, and that none of the eleven appears in
    any of the 128 recorded swings.

    Excluding a name is not free — a line that IS one of these will be matched to the
    nearest name still in the pool — which is why the list is ids, enumerated, and small."""
    pool = set(_vein_pool())
    for absent in ("錆びついた古銭", "紅焔の輝鉱石", "雪光輝鉱石"):
        assert absent not in pool, absent
    # And the exclusion did not take the families that stayed with it.
    assert {"透明な小石", "水晶輝石"} <= pool


def test_sale_only_is_two_things_and_only_the_ore_half_is_kept():
    """The type holds keepsakes and quest tokens beside the ore, and two rows the developers
    left in the shipped table. The id separates them; the icon does not — 聖白の輝石 carries
    the ore icon `item_icon_exchange_stone024` while sitting in the keepsake block."""
    pool = set(_vein_pool())
    for keepsake in ("王家の指輪", "大盛り金貨", "聖白の輝石"):
        assert keepsake not in pool, keepsake
    for dev_row in ("使ってない　後で消す", "テスト虫除け（仮）"):
        assert dev_row not in pool, dev_row


def test_the_pool_is_a_small_fraction_of_what_the_band_reads():
    """Measured on the shipped vocabulary rather than on the fixture above, because the
    number is the point: 236 candidates against 2,154, and every index the panel builds is
    a rendering of all of them."""
    from wddrop_client.capture.ocr import Vocabulary
    from wddrop_client.items import droppable, from_a_vein

    vocab = Path(__file__).resolve().parents[1] / "data" / "vocab.ja.json"
    if not vocab.exists():
        pytest.skip("the built vocabulary is not here")
    entries = Vocabulary.load(vocab).entries
    pool, band = from_a_vein(entries), droppable(entries)
    assert len(pool) == 236, len(pool)
    assert len(pool) < len(band) / 8
    # Every name ever read from a vein by either player, over 128 recorded swings.
    for mined in ("透明な小石", "下級鉄鉱石", "中級鉄鉱石", "上級鉄鉱石", "特上級鉄鉱石",
                  "銀鉱石", "ウロボロス鉱石"):
        assert mined in pool, mined


# -- what goes under which heading ------------------------------------------------------


def _categories(tmp_path):
    """A vocabulary holding one of each thing the grouping has to tell apart."""
    from wddrop_client.items import ItemCategories

    path = tmp_path / "vocab.ja.json"
    path.write_text(json.dumps({"items": [
        {"name": "ゴールド", "id": 1, "type": "Item::Currency"},
        {"name": "Gil", "id": 3000500, "type": "Item::Currency"},
        {"name": "透明な小石", "id": 20000000, "type": "Item::SaleOnly"},
        {"name": "紅焔の輝鉱石", "id": 200000160, "type": "Item::SaleOnly"},
        {"name": "王家の指輪", "id": 20100001, "type": "Item::SaleOnly"},
        {"name": "銀鉱石", "id": 470000040, "type": "Item::EquipmentReinforceMaterial"},
        {"name": "北穿の幽霊城のガラクタ", "id": 119060637, "type": "Item::Junk"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return ItemCategories.load(path)


def test_everything_sale_only_is_grouped_with_the_money(tmp_path):
    """`Item::SaleOnly` is the one game type that says what this heading is about: an item
    whose only use is to be sold. So the Items ranking answers "what did this dive give me"
    and this answers "what did it cash out to"."""
    from wddrop_client.items import CURRENCY, ITEM

    of = _categories(tmp_path).of
    for money in ({"item_id": 1}, {"item_id": 3000500}):
        assert of(money) == CURRENCY, money
    # Sale-only, whether or not a vein can produce it, and whether or not it is ore.
    for sold in ({"item_id": 20000000}, {"item_id": 200000160}, {"item_id": 20100001}):
        assert of(sold) == CURRENCY, sold
    for kept in ({"item_id": 470000040}, {"item_id": 119060637}):
        assert of(kept) == ITEM, kept


def test_a_line_with_no_id_stays_under_items(tmp_path):
    """An unresolved reading has no type to look up, and guessing one from the name is the
    thing this module exists not to do."""
    from wddrop_client.items import ITEM

    assert _categories(tmp_path).of({"item_name": "何か"}) == ITEM


def test_grouping_degrades_to_money_only_without_a_vocabulary(tmp_path):
    """A build that cannot find its vocabulary must not silently reshuffle the page. It
    falls back to the two currencies by id — the previous behaviour — rather than to nothing
    or to a guess."""
    from wddrop_client.items import CURRENCY, ITEM, ItemCategories

    for absent in (ItemCategories(), ItemCategories.load(None),
                   ItemCategories.load(tmp_path / "nope.json")):
        assert absent.of({"item_id": 1}) == CURRENCY
        assert absent.of({"item_id": 20000000}) == ITEM

    broken = tmp_path / "vocab.ja.json"
    broken.write_text("{not json", encoding="utf-8")
    assert ItemCategories.load(broken).of({"item_id": 1}) == CURRENCY


def test_the_shipped_vocabulary_puts_the_mined_ore_under_the_money(tmp_path):
    """Measured on the real table, because the consequence is the point: on the vein view
    this moves 透明な小石 and the 蒼雫 family out of the Items ranking. Decided that way
    deliberately — ore IS the thing you sell."""
    from wddrop_client.items import CURRENCY, ITEM, ItemCategories

    vocab = Path(__file__).resolve().parents[1] / "data" / "vocab.ja.json"
    if not vocab.exists():
        pytest.skip("the built vocabulary is not here")
    of = ItemCategories.load(vocab).of
    for sold in (20000000, 20000001, 200000060, 200000160):
        assert of({"item_id": sold}) == CURRENCY, sold
    # The other two vein types are things you use, and stay where they are.
    for kept in (470000040, 400009000):
        assert of({"item_id": kept}) == ITEM, kept

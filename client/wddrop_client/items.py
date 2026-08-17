"""Item identity, and what to call an item in front of the player.

TWO DIFFERENT JOBS, AND THEY PULL APART THE MOMENT PLAYERS USE DIFFERENT LANGUAGES.

WHAT IS RECORDED
----------------
An item's ID. Names are what the screen shows, and the screen shows a different one per
language: 「透明鵝卵石」 and 「透明な小石」 are the same drop, and pooling records by name
would file them as two items — so a study run across languages could not be pooled at all.
The client asks a player to set the game to Japanese, which makes this immediate rather than
hypothetical: every record from here on is Japanese-named, and every record before it is not.

The name is kept as well. It is the evidence — what was actually on screen — and a reading
that resolves to no id at all is still a reading worth having.

WHAT IS SHOWN
-------------
Whatever the player reads. Their game is in Japanese because this client asked it to be;
their window may be in any of six languages, and a page that tells them they found
「妖異冥刻雜物」 in Japanese when they set the interface to Chinese is answering in a
language they did not choose.

So: the id travels with the record, and a per-language name table turns it back into words at
the point of display. The table is data, not a code table — it is the game's own text, and it
changes when the game does.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("wddrop.items")


class ItemIndex:
    """Name -> id, for the language the GAME is in.

    Built from the same vocabulary the recogniser matches against, so a name it can read is a
    name this can identify — there is no second source to fall out of step with.

    WHERE A NAME IS NOT AN IDENTITY (measured on ja, 2026-08-13)
    -----------------------------------------------------------
    This is a dict keyed by NAME, so where two rows print the same string the later one wins
    and the earlier ids become unreachable. That is not a bug to fix here — the two names are
    pixel-identical, so no amount of reading separates them — but it is a limit on what an
    `item_id` in a record means, and it is invisible from the data:

        items       2,679 rows -> 2,446 distinct names. 106 names collide, 233 ids
                    unreachable. 古い遺骸 alone is 11 ids (10100000..100101040), all
                    Item::Junk. A colliding name resolves to an arbitrary member of its
                    group, with `resolved` true and a high `match_confidence`.
        equipment   823 families, 823 names, so essentially 1:1 — with two exceptions:
                      短剣（陽炎の忍者用）  -> identifications 100800000 AND 100800100,
                                             two real families sharing one name
                      極光の両手杖 and 【NPC用】極光の両手杖 -> both identification
                                             203000900, so the NPC variant folds into the
                                             player's

    So: safe to group records by name, or by `item_type` (which the colliding groups share).
    Treating `item_id` as THE identity of a drop is where this bites — 106 item names would
    each silently collapse to one of their ids, and a per-id join against a published rate
    table would be wrong for exactly those.
    """

    def __init__(self, by_name: dict[str, int] | None = None,
                 equipment: dict[str, int] | None = None):
        self._items = dict(by_name or {})
        self._equipment = dict(equipment or {})

    def __len__(self) -> int:
        return len(self._items) + len(self._equipment)

    @classmethod
    def from_vocab(cls, raw: dict) -> "ItemIndex":
        items = {e["name"]: e["id"] for e in raw.get("items", [])
                 if e.get("name") and e.get("id") is not None}
        # Equipment is keyed by `identification`, which is the game's OWN key for "which
        # equipment is this": `Equipment.IdentificationId [Key("identification")]`, and the
        # set-bonus master looks gear up by it (`EquipSetSkillContents` has
        # `[Key("equipment_identification")]` / `FindByEquipmentIdentification`), as do
        # unify, the equipment filter and the sort. The row `id` is a different question —
        # within one family the rows differ ONLY by id and their grade/rarity/unification
        # lottery ids, i.e. which tables rolled the piece. 青銅の剣 is 13 such rows. A
        # displayed name cannot reveal which one dropped, and no gameplay system identifies
        # gear by it, so the family key is both the honest answer and the useful one.
        equipment = {e["name"]: e["identification"] for e in raw.get("equipment", [])
                     if e.get("name") and e.get("identification") is not None}
        return cls(items, equipment)

    def identify(self, name: str) -> dict:
        """The id fields for a recognised name, or {} when it resolves to nothing.

        Empty rather than a guess: an unresolved name is a gap the analysis can see, and a
        wrong id is a drop filed as a different item.
        """
        if name in self._items:
            return {"item_id": self._items[name]}
        if name in self._equipment:
            return {"equipment_identification": self._equipment[name]}
        return {}


class ItemNames:
    """id -> name, in the language the WINDOW is in.

    Loaded from `names.<locale>.json` beside the client's other data. Absent is ordinary: the
    page then shows what was on screen, which is never wrong, only in the wrong language.
    """

    FILENAME = "names.{locale}.json"

    def __init__(self, locale: str = "", items: dict | None = None,
                 equipment: dict | None = None):
        self.locale = locale
        self._items = {int(k): v for k, v in (items or {}).items()}
        self._equipment = {int(k): v for k, v in (equipment or {}).items()}

    def __len__(self) -> int:
        return len(self._items) + len(self._equipment)

    @classmethod
    def load(cls, path: str | Path | None) -> "ItemNames":
        if not path or not Path(path).exists():
            return cls()
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:
            log.warning("wddrop: %s is not readable; showing recorded names instead", path)
            return cls()
        return cls(raw.get("locale", ""), raw.get("items"), raw.get("equipment"))

    def display(self, row: dict) -> str:
        """What to call this, preferring the player's own language.

        Takes a CONTENT LINE as recorded or a stats ROW as aggregated — they carry the same
        identity under different keys, and requiring the caller to know which is how the
        stats page came to show "?" for every item it had a perfectly good name for.

        Falls back to the recorded name — the one that was actually on screen — so a line
        whose id is missing, or an item the table does not have, still reads as something.
        """
        item_id = row.get("item_id")
        if item_id is not None and int(item_id) in self._items:
            return self._items[int(item_id)]
        family = row.get("equipment_identification")
        if family is not None and int(family) in self._equipment:
            return self._equipment[int(family)]
        return row.get("item_name") or row.get("equipment_name") or row.get("item") or "?"


# Two groups, and only two: WHAT YOU CASH IN, and WHAT YOU KEEP.
#
# It began as MONEY and THINGS, and the game's own `Item::*` types were no use for that.
# There are 44 of them, they say what an item is FOR rather than what it is, and they group
# things nobody would group: `Item::RelicEquipmentMaterial` holds the coins and banknotes
# beside 「〜の証」 proofs and 「〜の欠片」 weapon shards, because all three are spent on relic
# equipment. So 「ランペール金貨」 and 「10,000バイン紙幣」 stay out — 貨 ending a name is not
# evidence, and those are things a chest gives you and you spend at a counter.
#
# ONE type does say it, and it is the one this now uses. `Item::SaleOnly` means an item whose
# only use is to be sold; it is not loot you play with, it is loot you convert. Grouping it
# with the money makes the two lists answer two different questions — the Items ranking is
# what a dive actually gave you, and this is what it cashed out to — where a single ranking
# put 582 pebbles above everything a player opened a chest for.
#
# THE COST, STATED: on the vein view that moves 透明な小石 and the 蒼雫 family, which are what
# a miner is farming, out of the Items ranking and under this heading. Decided that way
# deliberately; ore IS the thing you sell.
CURRENCY = "currency"
ITEM = "item"

# The one type. Read from the vocabulary rather than listed here, for the reason the server
# joins `item_reference` rather than copying a type onto every row: it is the game's own fact
# about an id, it changes between versions, and a copy cannot be corrected.
SALE_ONLY = "Item::SaleOnly"

# BY ID, NEVER BY NAME. The window speaks six languages and the id is the only spelling that
# does not move: 1 is ゴールド, 金幣, Gold and 골드 depending on who is looking, and a name
# rule would file a player's money as an ordinary item the moment they changed the interface
# language — silently, and only for them.
CURRENCY_IDS = frozenset({
    1,          # ゴールド / 金幣 / Gold — the game's own money
    3000500,    # Gil — the collaboration currency, and money in exactly the same way
})


class ItemCategories:
    """Which of the two headings a row belongs under. Display only.

    Resolved at display time rather than stamped onto a record: the grouping is our word for
    the thing and not an observation, so a record made last month moves if the wording
    changes. Nothing here reaches the wire.

    Built with `load()` where the vocabulary is reachable. Constructed bare it still knows the
    two currencies, so a build that cannot find its vocabulary groups money correctly and
    leaves everything else under Items — which is the previous behaviour, and the right way to
    degrade: the alternative is a page that silently reshuffles depending on whether a file
    was found.
    """

    def __init__(self, sale_only: frozenset | set | None = None):
        self._sale_only = frozenset(sale_only or ())

    @classmethod
    def load(cls, path) -> "ItemCategories":
        """The sale-only ids, out of the same vocabulary the recogniser matched against.

        Read as plain JSON rather than through `Vocabulary`: this runs on the Stats page, and
        pulling in the recogniser's module to answer a grouping question would load the whole
        OCR stack to draw a table.

        Never raises. A missing or unreadable vocabulary gives the bare object above.
        """
        if not path:
            return cls()
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("wddrop: could not read %s for item grouping", path)
            return cls()
        return cls({int(row["id"]) for row in raw.get("items", [])
                    if row.get("type") == SALE_ONLY and row.get("id") is not None})

    def of(self, row: dict) -> str:
        item_id = row.get("item_id")
        if item_id is None:
            return ITEM
        item_id = int(item_id)
        if item_id in CURRENCY_IDS or item_id in self._sale_only:
            return CURRENCY
        return ITEM


# WHAT A DUNGEON CAN ACTUALLY HAND YOU, and therefore what the recogniser needs to be able
# to read. Everything else is weight: 3,268 candidates rendered, held and correlated, when
# 2,154 of them are the answer space.
#
# THE MESSAGE BAND IS NOT A GENERAL ACQUISITION LINE. It is one game string,
# `DungeonTreasure@DropItem` — 「{0}を手に入れた!!」 — emitted by the treasure system. A quest
# handing over a mission pass emits `Scenario@ObtainItemGet` instead, which is different
# wording this never matches. The mining panel is a third, `Common@GetItem`. So "can this
# come out of a chest or a vein" really is the question, and the answer bounds the index.
#
# The judgement is the project owner's, from the drop tables, which are not in the
# vocabulary. Checked against every item ever recorded — 75 records over nine sessions,
# chests and mining — and none of them is excluded by this.
#
# BEING WRONG HERE IS NOT FREE. An excluded name cannot be read at all: the line is either
# refused, or matched to the nearest name that IS included and recorded as that. So this
# excludes categories, never individual awkward names, and every rule below is one that can
# be checked by someone who reads the language.
NOT_FROM_A_DUNGEON = frozenset({
    "Item::MissionPass",            # bought or awarded
    "Item::InheritSkill",           # 秘伝書の一節
    "Item::LimitedSkillLevelup",    # 技能書
    "Item::JobChangeable",          # 指南書
    "Item::Valuable",
    "Item::VipPass",
    "Item::EventMissionRelease",
})
# Junk carrying any of these is event or collection stock: 「稀なる遺骸」 and 「【追憶】…」 are
# the bonus remains, 「朽ちた巻物」 the decayed scrolls.
NOT_FROM_A_DUNGEON_JUNK = ("稀なる遺骸", "【追憶】", "朽ちた巻物")
# The basic remains ARE loot; the described ones are a named character's. Enumerated because
# the line is "does the name carry a modifier", which is a question about Japanese: a pattern
# loose enough to keep 「魔術師の遺骸」 also keeps 「燻る騎士の遺骸」, whose 燻る is exactly the
# description that disqualifies it. 「古い遺骸」 is here by decision — an adjective, and basic.
BASIC_REMAINS = frozenset({
    "古い遺骸", "暁の遺骸", "黎明の遺骸",
    "戦士の遺骸", "騎士の遺骸", "僧侶の遺骸", "盗賊の遺骸", "魔術師の遺骸",
    "冒険者の遺骸Ⅰ", "冒険者の遺骸Ⅱ",
})


def is_npc_gear(name: str) -> bool:
    """Somebody else's weapon. 「◯◯用」 is "for the use of ◯◯" and is always bracketed.

    The bracket is what separates an OWNER from a description: 「ドラゴンスレイヤー（片手）」 is
    a real player weapon whose bracket says one-handed. Rank was tried first and is weaker —
    it catches the twelve at rank 0 and misses 短剣（陽炎の忍者用） at rank 5.
    """
    return "用" in name and ("）" in name or ")" in name)


def is_described_remains(name: str, places=()) -> bool:
    """A named character's remains, as opposed to loot.

    Remains that START WITH A PLACE are the dungeon's, not a person's:
    「薬種の古跡の古い遺骸」 and 「導きの霊廟の遺骸」 are the same thing as the 「〜のガラクタ」
    beside them, junk named after where it was found.
    """
    if "遺骸" not in name or name in BASIC_REMAINS:
        return False
    return not any(name.startswith(place) for place in places)


def dungeon_places() -> set:
    """Every place the game names — the client's own table, and the catalogue beside it.

    Two sources: the built-in table exists so the picker works with no data file at all, and
    `catalog.<locale>.json` is the fuller list the picker prefers when it is there.
    「導きの霊廟」 is in the catalogue and not in the code, which is the case that needs it.
    """
    from .dungeons import DUNGEONS

    places = {d.get("ja") for d in DUNGEONS.values() if d.get("ja")}
    try:
        from .config import data_dir

        catalogue = Path(data_dir()) / "catalog.ja.json"
        if catalogue.exists():
            def walk(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key in ("name", "dungeon_name", "floor_name") and isinstance(value, str):
                            places.add(value)
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(json.loads(catalogue.read_text(encoding="utf-8")))
    except Exception:                                      # noqa: BLE001
        # A catalogue that cannot be read costs a slightly shorter list of places, which
        # costs a couple of remains being treated as a person's. It must not cost a session.
        log.debug("wddrop: the catalogue could not be read for place names", exc_info=True)
    # Two characters is not a place, it is a coincidence waiting to match a name.
    return {p for p in places if len(p) >= 3}


def droppable(entries) -> list:
    """The names a dungeon can produce, in the order given, deduplicated.

    Takes vocabulary entries (anything with `.name`, `.item_type`, `.identification`).
    """
    places = dungeon_places()
    out, seen = [], set()
    for entry in entries:
        name = getattr(entry, "name", None)
        if not name or name in seen:
            continue
        kind = getattr(entry, "item_type", None)
        if getattr(entry, "identification", None) is not None:
            if is_npc_gear(name):
                continue
        elif kind in NOT_FROM_A_DUNGEON:
            continue
        elif kind == "Item::Junk" and (
                any(marker in name for marker in NOT_FROM_A_DUNGEON_JUNK)
                or is_described_remains(name, places)):
            continue
        seen.add(name)
        out.append(name)
    return out


# WHAT A VEIN CAN HAND YOU, which is a far smaller question than what a dungeon can.
#
# The mining panel was matched against the same 2,384 names the message band is — every
# droppable item AND every equipment family — and a vein does not produce equipment, junk,
# relics, consumables or event stock. Over 128 recorded swings by two players (252 lines,
# dungeon 7015) it produced exactly seven names in these three types, and one eighth line:
# 「朧丸」, a katana, confirmed by the player as a misread of an ore name. That is the cost of
# an answer space that admits things the panel cannot say — the wrong name does not fail, it
# WINS, and nothing marks it.
#
# The three types, and what each contributes:
#
#   Item::EquipmentReinforceMaterial   the five iron ores — all five have been mined
#   Item::EquipmentSubEffectChange     ウロボロス鉱石 (mined), the two 全変造石, and the
#                                      変造石/精錬石 families
#   Item::SaleOnly                     透明な小石 (mined) and the ore families a vein here
#                                      produces — see NOT_FROM_A_VEIN_IDS for the eleven
#                                      rows in the same id block that it does not
#
FROM_A_VEIN = frozenset({
    "Item::EquipmentReinforceMaterial",
    "Item::EquipmentSubEffectChange",
    "Item::SaleOnly",
})

# `Item::SaleOnly` IS TWO DIFFERENT THINGS UNDER ONE NAME, and only one of them is ore. The
# type also holds keepsakes and quest tokens — 王家の指輪, 王紋輝冠のブローチ, 雪原兎の毛皮 —
# and two rows the developers left in the shipped table, 「使ってない　後で消す」 and
# 「テスト虫除け（仮）」. The ore is exactly the ids below, and the id is what separates them:
# the four families of four are consecutive (`item_icon_exchange_stone011`-`044`), everything
# from 200100000 up is a keepsake, and 20100001+ is the older block of the same.
#
# BY ID RATHER THAN BY ICON OR NAME, for the reason CURRENCY_IDS is: a name rule moves with
# the locale, and an icon is a file name that can be reused — 聖白の輝石 carries the ore icon
# `item_icon_exchange_stone024` while sitting in the keepsake block, and it is not vein
# output. The id is the game's own identity for the thing.
ORE_ID_BLOCKS = (
    (20000000, 20099999),        # 透明な小石 and the 蒼雫 family
    (200000000, 200099999),      # 水晶 / 紅焔 / 雪光, and the three coin-like materials
)


# BEING ORE IS NOT THE SAME QUESTION AS COMING OUT OF A VEIN. The id blocks above separate
# ore from keepsakes; which ore a vein actually PRODUCES is a fact about the drop tables,
# which are not in the vocabulary. So these eleven are excluded by the project owner's
# judgement — the same authority `NOT_FROM_A_DUNGEON` rests on, and for the same reason.
#
# Two things a reader can check here without the tables. The first three carry
# `item_icon_cash_material` rather than any of the four ore icon families — they are the
# coin-like exchange materials, not stones. And none of the eleven appears in a single one of
# the 128 recorded swings in `tests/truth`, where 透明な小石 and the 蒼雫 family do.
#
# BEING WRONG HERE IS NOT FREE, exactly as it is not for NOT_FROM_A_DUNGEON: an excluded name
# cannot be read at all. A line that IS one of these is refused, or matched to the nearest
# name still in the pool and recorded as that — the wrong name does not fail, it wins.
NOT_FROM_A_VEIN_IDS = frozenset({
    200000090,                                     # 錆びついた古銭 ┐ the coin-like exchange
    200000110,                                     # 貝貨           │ materials, on
    200000120,                                     # 砕けた徽章      ┘ `cash_material`
    200000130, 200000140, 200000150, 200000160,    # the 紅焔 family
    200000170, 200000180, 200000190, 200000200,    # the 雪光 family
})


def _is_ore_id(item_id) -> bool:
    if item_id is None:
        return False
    return any(low <= int(item_id) <= high for low, high in ORE_ID_BLOCKS)


def from_a_vein(entries) -> list:
    """The names the mining panel may say, in the order given, deduplicated.

    Same input as `droppable` — anything with `.name`, `.item_type`, `.item_id`. Returns 236
    of the 2,384 the panel used to be scored against.
    """
    out, seen = [], set()
    for entry in entries:
        name = getattr(entry, "name", None)
        kind = getattr(entry, "item_type", None)
        if not name or name in seen or kind not in FROM_A_VEIN:
            continue
        if kind == "Item::SaleOnly" and not _is_ore_id(getattr(entry, "item_id", None)):
            continue
        # Unconditional, not SaleOnly-only: an id is the game's own identity for one thing,
        # so a rule keyed on it does not need to be told which type it was reached through.
        item_id = getattr(entry, "item_id", None)
        if item_id is not None and int(item_id) in NOT_FROM_A_VEIN_IDS:
            continue
        seen.add(name)
        out.append(name)
    return out

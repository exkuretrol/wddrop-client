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

"""
Checking the player's dungeon label against what the chest actually contained.

WHY THIS EXISTS
---------------
`dungeon_id` is `label_source: user_declared` — the player picks it from a list and nothing
verifies it. It is also the analysis STRATUM, so a wrong one is worse than a missing one: it
does not add noise, it moves observations into another dungeon's distribution.

It went wrong on the first real session. Five chests were recorded as 初始的奈落 (2000) while
every junk line in them read 北穿幽靈城的… — and 北穿幽靈城 is dungeon 7015. The player had not
touched a dropdown that defaults to a real dungeon, so "did not choose" and "chose the first
one" were indistinguishable.

THE EVIDENCE IS FREE
--------------------
Junk is named after the dungeon it comes from. Measured over the built vocabularies:

    zh_tw   382 of 1,071 junk items name their dungeon (36%), across 17 dungeons
    ja      384 of 1,094 (35%), en 419 of 1,067 (39%), ko 381, zh_cn 382
    de        0 of 1,094  — German junk names do not lead with the dungeon at all

So this is a check that HAS evidence sometimes, and none at other times, and the difference
matters. Silence here means "the contents said nothing", never "the contents agreed" — a
locale where the convention does not hold (de) must not therefore look permanently correct.

WHAT IT IS NOT
--------------
It cannot confirm a label, only contradict one. A chest of 拜恩紙幣 and 治療劑 names no
dungeon, and 36% coverage means most chests in the affected dungeons still say nothing at
all. The hint is recorded on every event regardless, in `qc`, so a disagreement can be
audited — or repaired — long after the session that produced it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("wddrop.labels")


class DungeonHints:
    """Which dungeon a set of item names points at, if any."""

    def __init__(self, by_dungeon: dict[int, str], junk_names: set[str]):
        # Longest name first: 試煉洞窟 is a prefix of 試煉洞窟（青銅階）, so matching in any
        # other order would attribute the graded dungeons' junk to the ungraded one.
        self._dungeons = sorted(by_dungeon.items(), key=lambda kv: -len(kv[1]))
        self._junk = junk_names
        self.names = dict(by_dungeon)

    def __len__(self) -> int:
        return len(self._dungeons)

    @classmethod
    def load(cls, vocab_path: str | Path, catalog_path: str | Path | None = None) -> "DungeonHints":
        """Build from a vocabulary and the catalogue beside it.

        A missing catalogue yields an EMPTY set of hints rather than an error: the check is
        an extra, and a client without one must still capture.
        """
        vocab_path = Path(vocab_path)
        vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
        locale = vocab.get("locale", "zh_tw")
        catalog_path = Path(catalog_path) if catalog_path else \
            vocab_path.parent / f"catalog.{locale}.json"
        if not catalog_path.exists():
            log.debug("wddrop: no catalogue beside %s; dungeon cross-check disabled", vocab_path)
            return cls({}, set())
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        dungeons = {d["id"]: d["name"] for d in catalog.get("dungeons", []) if d.get("name")}
        # ONLY junk. Every item that names a dungeon is Item::Junk (checked across all six
        # locales), and restricting to it keeps an ordinary item that happens to share a
        # prefix from being read as evidence.
        junk = {e["name"] for e in vocab.get("items", [])
                if e.get("name") and e.get("type") == "Item::Junk"}
        return cls(dungeons, junk)

    def dungeon_of(self, item_name: str) -> int | None:
        """The dungeon this one item names, if it names one."""
        if item_name not in self._junk:
            return None
        for dungeon_id, name in self._dungeons:
            if item_name.startswith(name) and len(item_name) > len(name):
                return dungeon_id
        return None

    def infer(self, item_names) -> int | None:
        """The dungeon a chest's contents point at.

        None when nothing points anywhere — and also when the lines point at DIFFERENT
        dungeons, because contradictory evidence is not evidence. That can happen legitimately
        (a stack carried in from elsewhere), and guessing between them would be inventing a
        label rather than checking one.
        """
        found = {d for d in (self.dungeon_of(n) for n in item_names) if d is not None}
        return found.pop() if len(found) == 1 else None

    def check(self, declared: int | None, item_names) -> dict:
        """QC for one chest: what the contents said, and whether it disagrees.

        Travels with the event in `qc`, so the evidence survives the session that produced
        it — which is what makes a mislabelled batch repairable later instead of merely
        regrettable.
        """
        hint = self.infer(item_names)
        if hint is None:
            return {}
        out = {"contents_dungeon_id": hint}
        if declared is not None and declared != hint:
            out["label_conflict"] = True
        return out

    def conflict_names(self, declared: int | None, qc: dict) -> tuple[str, str] | None:
        """The two dungeons in a label conflict, named. None when there is no conflict.

        Names, never ids: the window is for a player choosing where they are standing, and
        an internal number tells them nothing they can act on. The caller phrases the
        sentence, so it can be phrased in their language.
        """
        if not qc.get("label_conflict"):
            return None
        hint = qc["contents_dungeon_id"]
        return self.names.get(declared, ""), self.names.get(hint, "")

    def describe_conflict(self, declared: int | None, qc: dict) -> str:
        """The same thing for the command line, where the ids ARE the useful part."""
        if not qc.get("label_conflict"):
            return ""
        hint = qc["contents_dungeon_id"]
        return (f"[!] you selected {self.names.get(declared, declared)} but this chest's junk "
                f"comes from {self.names.get(hint, hint)} ({hint}) — check the dungeon")

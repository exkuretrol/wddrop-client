"""
Pickaxes: counting the ones that break, and noticing when there are none left.

WHY THIS AND NOT THE ORE
-----------------------
Mining consumes pickaxes. `DungeonMiningController` in the game sends
`DungeonMiningRequest{user_inventory_bag_id}` and gets back `DungeonMiningResponse` carrying
both `received_contents` (the ore) and `lost_contents` (the pickaxe) — so the cost is as much
a part of the event as the yield. But the two are not equally observable from the screen:

  * the ore line's wording is unconfirmed — no recording yet contains a mining event, and
    the one ore line we do have (蒼藍礦石塊 ×10) is provably chest loot;
  * the pickaxe messages are the game's OWN strings and exist in every locale, so they can
    be matched exactly rather than guessed at.

And a broken pickaxe is the thing the player actually needs told. A pickaxe that breaks is a
cost they paid; running out is what silently ends a mining run, because from then on every
mining spot just says "you could mine this if you had a pickaxe" — which is easy to walk
past without noticing.

    Dungeon@PickaxeBreak    zh_tw 「{0}壞掉了」   ja 「{0}が壊れてしまった」
                            en    "The {0} has broken!"
    Dungeon@PickaxeNotHave  zh_tw 「如果有十字鎬的話應該能採掘。」
                            en    "This spot looks mineable if you had a pickaxe."

Both come from `client_texts` via build_vocab, never hardcoded — the break template's
placeholder sits in a different place per locale (Korean adds a particle: 「{0}이(가)
망가졌다.」), so a regex written for one would silently match nothing anywhere else.

WHAT IS STILL ASSUMED
---------------------
That these lines render in the same calibrated message band as the drop lines. Everything
else here is read from the game's data; that one point needs a recording of an actual mining
run to confirm (`--record-mode all`, since if the minimap stays up during mining the episode
recorder never saves those frames in the first place).
"""
from __future__ import annotations

import json
import logging
import unicodedata
from collections import Counter
from pathlib import Path

log = logging.getLogger("wddrop.pickaxe")

BROKE = "broke"
NONE_LEFT = "none_left"


def _norm(text: str) -> str:
    """NFKC, stripped — the same folding the message parser applies.

    The templates carry full-width punctuation that the renderer and the reader see in
    different forms, so comparing raw strings would miss on exactly the locales that use it.
    """
    return unicodedata.normalize("NFKC", (text or "").strip())


class PickaxeWatch:
    """Matches the two pickaxe messages and keeps the running count.

    Deliberately a closed set of literal strings: one line per pickaxe that exists, plus the
    out-of-pickaxes sentence. That is a handful of candidates rather than a vocabulary, so it
    is cheap enough to try on any settled line the item recogniser refused.
    """

    def __init__(self, break_template: str | None, none_text: str | None,
                 pickaxe_names: list[str] | None = None):
        self.pickaxe_names = list(pickaxe_names or [])
        self.none_text = _norm(none_text) if none_text else ""
        self._broken_lines: dict[str, str] = {}
        if break_template and "{0}" in break_template:
            for name in self.pickaxe_names:
                self._broken_lines[_norm(break_template.replace("{0}", name))] = name
        self.broken: Counter = Counter()
        self.out_of_pickaxes = False

    @classmethod
    def from_vocab(cls, vocab_path: str | Path) -> "PickaxeWatch":
        raw = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
        templates = raw.get("templates") or {}
        return cls(
            templates.get("pickaxe_break"),
            templates.get("pickaxe_none"),
            [p["name"] for p in raw.get("pickaxes", []) if p.get("name")],
        )

    def __len__(self) -> int:
        """How many messages this watch can recognise at all. Zero means the vocabulary
        predates the mining templates and the feature is simply off."""
        return len(self._broken_lines) + (1 if self.none_text else 0)

    @property
    def candidates(self) -> list[str]:
        """Every line to render and compare against the screen."""
        return list(self._broken_lines) + ([self.none_text] if self.none_text else [])

    def feed(self, text: str) -> tuple[str, str] | None:
        """Classify one recognised line. Returns (BROKE, pickaxe name) or (NONE_LEFT, "")."""
        s = _norm(text)
        if not s:
            return None
        name = self._broken_lines.get(s)
        if name is not None:
            self.broken[name] += 1
            log.info("wddrop: a %s broke (%d this session)", name, self.total_broken)
            return BROKE, name
        if self.none_text and s == self.none_text:
            # Not counted — this fires once per mining spot the player walks up to, so it
            # says "you have none", not "you lost another one".
            if not self.out_of_pickaxes:
                log.warning("wddrop: out of pickaxes — restock in town to keep mining")
            self.out_of_pickaxes = True
            return NONE_LEFT, ""
        return None

    @property
    def total_broken(self) -> int:
        return sum(self.broken.values())

    def summary(self) -> str:
        """One line for the player. Empty when there is nothing to say."""
        if not self.total_broken and not self.out_of_pickaxes:
            return ""
        parts = []
        if self.total_broken:
            parts.append("pickaxes broken: " + ", ".join(
                f"{name} x{n}" for name, n in sorted(self.broken.items())))
        if self.out_of_pickaxes:
            parts.append("you have none left — restock in town before mining again")
        return " — ".join(parts)

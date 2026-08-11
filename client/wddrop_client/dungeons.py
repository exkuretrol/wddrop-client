"""The dungeons this study covers, written here rather than shipped as data.

WHY IN CODE
-----------
It is an id and a name. Carrying that as a generated file meant the client could not start
without one — the picker is populated from it and an empty picker blocks recording — so a
file derived from the game's own assets became a hard dependency of a program that otherwise
only reads the screen. Nineteen names is not data, it is a list.

WHICH NINETEEN
--------------
The ones whose junk carries the dungeon's own name (「北穿幽靈城的乳白色雜物」). That is not
an arbitrary cut: dungeon-branded junk is what the client cross-checks a player's dungeon
choice against, so these are the dungeons where a mislabelled dive can be CAUGHT. Elsewhere
the label would be taken on trust, and the label is the one thing the window cannot verify.

Names are the game's Traditional Chinese ones, which is the locale this study runs in. A
client in another language still shows these; dropping a `catalog.<locale>.json` beside the
client overrides the whole table if a fuller or translated one is ever wanted.

ADDING ONE
----------
A line. If a player farms somewhere not listed, that is the fix — and worth doing, because
the alternative is that they cannot record at all.
"""
from __future__ import annotations

# id -> name, as the game shows it.
STUDY_DUNGEONS: dict[int, str] = {
    2000: "初始的奈落",
    2001: "貿易水路",
    2003: "豪雪地帶",
    4000: "火之魔窟",
    4001: "風之魔窟",
    4002: "光之魔窟",
    4003: "土之魔窟",
    4005: "水之魔窟",
    5014: "王之試煉",
    5016: "百花之庭",
    5018: "雷鳴洞窟",
    6000: "古城遺跡",
    7000: "怨嗟洞窟",
    7001: "花之洞窟",
    7006: "砂影洞窟",
    7008: "拂曉之路",
    7010: "鬼啼島",
    7014: "藥種古蹟",
    7015: "北穿幽靈城",
}


def catalog() -> list[dict]:
    """The same shape a `catalog.<locale>.json` would give, minus the floors.

    No floors on purpose. The floor control is hidden and every record files a null floor —
    a player will not keep a floor dropdown current through a dive, and a stale floor is
    worse than an honest null in the one field the analysis strata are built on.
    """
    return [{"id": key, "name": name, "floors": []}
            for key, name in sorted(STUDY_DUNGEONS.items())]

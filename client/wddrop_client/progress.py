"""
How far through the story this player is — the one covariate the client cannot read.

WHY IT IS ASKED AT ALL
----------------------
Most dungeons scale with a value the game keeps on its own side. It decides how strong the
enemies are, which groups appear at all, and what some quests pay out — in the dungeon this
study is mostly farmed in, the same completed quest pays 2,500, 4,500 or 8,500 gold depending
on it. Two players standing on the same floor can be in measurably different games.

Whether it also changes what is IN a chest is not established, and that is the point: it is
the question this dataset can answer, and only if the covariate is recorded beside the drops.

WHY IT IS A BITFIELD AND NOT A NUMBER
-------------------------------------
The value itself is unreadable — no screen shows it, no file the client can reach holds it,
and the amount each ending adds is decided on the game's side and is not even constant. What
a player CAN answer is which endings they have seen. So this is a bitfield of those endings,
self-reported, and it is never to be called a level: two players with identical bits can hold
different values.

APPEND ONLY. A new ending gets a new bit on the end. An existing bit never moves and never
changes meaning, because an answer given last month cannot be re-asked — and `width` records
how many bits existed when it was given, so a bit that did not exist yet reads as unknown
rather than as "no".

The endings are ordered — within a chapter the losing one comes first — and in the snow
chapter the last two are branches of one fork rather than two steps. Both facts are written
down beside the derivation, not enforced here: the player is the authority on their own game,
and a client that argued with an answer would be arguing from a list it derived itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Ending:
    """One thing a player can be asked whether they have seen — one bit.

    `key` is stable and never reused: it is what the bit MEANS, and a bit's POSITION is its
    identity rather than its place in the story. That is what lets a newly-found condition be
    appended without disturbing an answer someone gave months ago.

    `covers` names the game's own endings this bit stands for. Several of them map to more
    than one, and deliberately: a player knows they finished a chapter, not which of its
    three good endings they saw — and where all three do the same thing to the world, asking
    them to guess would buy resolution by inventing it.
    """

    key: str
    dungeon_id: int
    label: str                      # English source string; translated in i18n.py
    covers: tuple[str, ...] = ()
    # Endings that must already have happened for this one to be reachable. Not a rule the
    # answer is checked against — the player knows their own game — but it decides the ORDER
    # they are listed in, which is what makes the list readable top to bottom.
    requires: tuple[str, ...] = ()


# The built-in list, and the only thing that decides what a bit means. `progress_conditions.
# json` beside the client can add to it — the same way the dungeon table takes a catalogue.
#
# ONE BIT PER ENDING THE GAME ITSELF TRACKS, in the order they can be reached. An earlier
# entry is either a step on the way to a later one or the outcome you get instead of it, so a
# player reads down the list and stops where they are.
#
# RE-CUT ONCE, before any build carried it. The rule from here is append only: a bit's
# position is its meaning, and an answer already given cannot be asked again. It was safe
# exactly this once because nothing had shipped these bits and no answer existed to break.
ENDINGS: tuple[Ending, ...] = (
    # 初始の奈落. The execution is what the first run ends in; everything else needs a later
    # one. Whether everyone survived is what separates the two clears, and the reconciliation
    # is a side quest that rides along with either.
    Ending("abyss1_executed", 2000, "You were taken to the execution ground",
           ("ed_pattern_bad_na1",)),
    Ending("abyss1_cleared_lost_someone", 2000, "You finished it, but someone did not make it",
           ("ed_pattern_normal_na1",), ("abyss1_executed",)),
    Ending("abyss1_cleared_all_survived", 2000, "You finished it with everyone still alive",
           ("ed_pattern_true_na1",), ("abyss1_executed",)),
    Ending("abyss1_reconciled", 2000, "You made peace with the uncle",
           ("ed_pattern_extra_na1",),
           ("abyss1_cleared_lost_someone", "abyss1_cleared_all_survived")),
    # 交易水路.
    Ending("abyss2_lost_them", 2001, "The person you were sent to find was already dead",
           ("ed_pattern_bad_na2",)),
    Ending("abyss2_saved_them", 2001, "You brought the person you were sent to find back",
           ("ed_pattern_normal_na2",), ("abyss2_lost_them",)),
    Ending("abyss2_resolved", 2001,
           "You fought the great aberration and she made up her mind",
           ("ed_pattern_true_na2",), ("abyss2_lost_them",)),
    Ending("abyss2_couple_lived", 2001, "The couple got away alive",
           ("ed_pattern_extra_na2",), ("abyss2_saved_them", "abyss2_resolved")),
    # 不落の城塞. Whether the bad route must come first here is NOT established — the good
    # route depends on solving the cases rather than on a previous run — so no requirement is
    # claimed for it.
    Ending("abyss3_duke_died", 2002, "The duke was killed and you were blamed for it",
           ("ed_pattern_bad_na3",)),
    Ending("abyss3_duke_saved", 2002, "The duke survived",
           ("ed_pattern_normal_na3",)),
    Ending("abyss3_accused", 2002, "You handed over the evidence and named the culprit",
           ("ed_pattern_true_na3",), ("abyss3_duke_saved",)),
    # 豪雪地帯. The priest cannot be beaten on the first run, so everything else follows it.
    Ending("abyss4_priest_won", 2003, "The priest changed into something you could not beat",
           ("ed_pattern_bad_na4",)),
    Ending("abyss4_villagers", 2003, "You beat the priest, and the villagers came for you",
           ("villagers_na4",), ("abyss4_priest_won",)),
    Ending("abyss4_cleared", 2003, "You beat the priest and finished the story",
           ("ed_pattern_normal_na4",), ("abyss4_priest_won",)),
    Ending("abyss4_freed_him", 2003, "You freed the priest and saw the story through",
           ("ed_pattern_true_na4",), ("abyss4_cleared",)),
)

WIDTH = len(ENDINGS)


@dataclass(frozen=True)
class Grade:
    """One rung of the adventurer grade ladder, as the game defines it.

    A SEPARATE AXIS from the story. The story is a set of things that either happened or did
    not; this is a ladder the main character climbs, one rung at a time, and it caps the
    party's level — 40 at bronze, 70 at copper. Two players at the same story point but
    different grades are farming with different parties, so it is its own field rather than
    more bits: an ordinal packed into a bitfield would be a set that can only ever hold one
    member.

    `id` is the game's own, so a new rung is a new id and nothing renumbers.
    """

    id: int
    max_level: int
    names: dict                     # the game's own name, per interface language


# From the game's grade table, names included — the same arrangement the dungeon list uses,
# because these are the game's words rather than this window's.
GRADES: tuple[Grade, ...] = (
    Grade(1, 20, {"ja": "無等級", "zh_tw": "無階", "zh_cn": "无阶", "en": "No Grade", "ko": "무 등급", "de": "Ranglos"}),
    Grade(2, 30, {"ja": "鉛等級", "zh_tw": "鉛階", "zh_cn": "铅阶", "en": "Lead Grade", "ko": "납 등급", "de": "Bleirang"}),
    Grade(3, 40, {"ja": "青銅等級", "zh_tw": "青銅階", "zh_cn": "青铜阶", "en": "Bronze Grade", "ko": "청동 등급", "de": "Bronzerang"}),
    Grade(4, 50, {"ja": "鉄等級", "zh_tw": "鐵階", "zh_cn": "铁阶", "en": "Iron Grade", "ko": "철 등급", "de": "Eisenrang"}),
    Grade(5, 60, {"ja": "鋼等級", "zh_tw": "鋼階", "zh_cn": "钢阶", "en": "Steel Grade", "ko": "강철 등급", "de": "Stahlrang"}),
    Grade(6, 70, {"ja": "銅等級", "zh_tw": "銅階", "zh_cn": "铜阶", "en": "Copper Grade", "ko": "구리 등급", "de": "Kupferrang"}),
    Grade(7, 80, {"ja": "銀等級", "zh_tw": "銀階", "zh_cn": "银阶", "en": "Silver Grade", "ko": "은 등급", "de": "Silberrang"}),
    Grade(8, 90, {"ja": "金等級", "zh_tw": "金階", "zh_cn": "金阶", "en": "Gold Grade", "ko": "금 등급", "de": "Goldrang"}),
    Grade(9, 100, {"ja": "白金等級", "zh_tw": "白金階", "zh_cn": "白金阶", "en": "Platinum Grade", "ko": "백금 등급", "de": "Platinrang"}),
    Grade(10, 110, {"ja": "ミスリル等級", "zh_tw": "秘銀階", "zh_cn": "秘银阶", "en": "Mythril Grade", "ko": "미스릴 등급", "de": "Mithrilrang"}),
    Grade(11, 120, {"ja": "アダマンタイト等級", "zh_tw": "精金階", "zh_cn": "精金阶", "en": "Adamantite Grade", "ko": "아다만타이트 등급", "de": "Adamantrang"}),
    Grade(12, 130, {"ja": "オリハルコン等級", "zh_tw": "奧利哈爾鋼階", "zh_cn": "奥利哈尔钢阶", "en": "Orichalcum Grade", "ko": "오리할콘 등급", "de": "Orichalcumrang"}),
)

# The highest rung the game has actually opened. The table ships all twelve because the game
# does, but offering a player a grade that does not exist yet is inviting an answer that
# cannot be true.
HIGHEST_RELEASED_GRADE = 6

# Where everyone starts. The game's own bottom rung rather than a "not sure" of our
# invention: a player who has passed no promotion exam IS this grade, so there is no third
# state to offer. Unanswered is still None in the config — the difference is that nobody is
# asked to tell two names for the same place apart.
GRADE_FLOOR = 1


def released_grades() -> tuple[Grade, ...]:
    return tuple(g for g in GRADES if g.id <= HIGHEST_RELEASED_GRADE)


def grade_name(grade_id, locale: str) -> str:
    for grade in GRADES:
        if grade.id == grade_id:
            return grade.names.get(locale) or grade.names.get("ja") or ""
    return ""


def as_flags(seen: dict[str, bool]) -> int:
    """The answer as one integer, bit N for ENDINGS[N] — the shape a wire would carry.

    Same encoding as any permissions field: OR to combine, AND to test, and a new condition
    takes the next power of two. What is NOT borrowed from those is treating an unset bit as
    "no": a bit that did not exist when the player answered means nobody asked them, and the
    client version on the row is what separates the two.
    """
    return sum(1 << index for index, ending in enumerate(ENDINGS) if seen.get(ending.key))


def from_flags(value: int) -> dict[str, bool]:
    """The inverse, for anything reading a stored integer back."""
    return {e.key: bool(int(value or 0) >> index & 1) for index, e in enumerate(ENDINGS)}


def decode(bits: str, width: int) -> dict[str, bool | None]:
    """`{key: seen}` — True, False, or None for a bit that did not exist when this was
    answered. None is not False: it means nobody was ever asked."""
    out: dict[str, bool | None] = {}
    for index, ending in enumerate(ENDINGS):
        if index >= max(0, int(width or 0)):
            out[ending.key] = None
        else:
            out[ending.key] = index < len(bits) and bits[index] == "1"
    return out


def encode(seen: dict[str, bool]) -> tuple[str, int]:
    """`(bits, width)` from an answer. Width is this build's, because this build asked."""
    return "".join("1" if seen.get(e.key) else "0" for e in ENDINGS), WIDTH


def answered(cfg) -> bool:
    """Whether this player has ever answered. An all-zero answer IS an answer — "I have not
    finished anything" is information, and asking again would be asking a second time."""
    return bool(getattr(cfg, "progress_width", 0))


def should_ask(cfg, scales: bool | None, now: datetime | None = None) -> bool:
    """Whether to put the question now.

    `scales` is the dungeon's flag from the catalogue: True, False, or None for a dungeon
    whose placements were never extracted. **None counts as yes** — an unnecessary question
    costs a dismissal, a missing one costs the covariate for every session that follows.
    """
    if scales is False:
        return False
    interval = int(getattr(cfg, "progress_interval_days", 0) or 0)
    if interval <= 0:                             # "never ask" — Settings still has it
        return False
    if answered(cfg) and getattr(cfg, "progress_width", 0) >= WIDTH:
        return False                              # asked, and about everything this build knows
    last = getattr(cfg, "progress_asked_at", None)
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True                               # unreadable stamp: ask rather than never
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - when >= timedelta(days=interval)


def mark_asked(cfg, now: datetime | None = None) -> None:
    """Record that the question was PUT. Called for a dismissal exactly as for an answer —
    see the config field's own note on why those must cost the same."""
    cfg.progress_asked_at = (now or datetime.now(timezone.utc)).isoformat()
    cfg.save()


def remember(cfg, seen: dict[str, bool], grade=None, now: datetime | None = None) -> None:
    """Store an answer — both axes, since both are asked at once.

    `grade` of None means the player did not say, and that is left as None rather than
    written as a grade: "not sure" and "no grade at all" are different answers, and the
    second one is a real rung with a real level cap.
    """
    cfg.progress_bits, cfg.progress_width = encode(seen)
    cfg.character_grade = int(grade) if grade is not None else None
    mark_asked(cfg, now)

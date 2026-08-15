"""
Ground truth: confirming what a chest ACTUALLY contained.

Every recognition problem so far has been found the same way — the player noticed a chest
was wrong, and the frames were then read by hand. That does not scale, and worse, it only
finds the errors somebody happened to spot. A study cannot report a drop rate without
knowing its own error rate.

So confirmations are recorded. For each chest the player either agrees with what was read,
or supplies what it should have been. That yields three things nothing else provides:

  * a MEASURED accuracy figure — how often the client is right, and in which direction it
    is wrong (a fabricated item is a different problem from a missed one),
  * a regression corpus — recordings whose correct answer is known, so a change that fixes
    one case and breaks another is caught rather than shipped,
  * corrected data — a confirmation overrides the reading, so the spool improves even where
    the recogniser cannot yet.

MISSING items are recorded as first-class, not as a note. A chest read as one item when it
held two is the failure mode that has recurred most, and it is invisible in any measure
based only on what was read.

QUANTITIES are part of the truth, not just names. A fabricated quantity (a real x1 recorded
as x9) is a wrong NUMBER attached to a correct name, so a name-only comparison scores it as
perfect. Items are therefore written as "name xN", and a wrong count is counted separately
from a wrong or missing name.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# "name x3" / "name ×3" / "name" -> (name, quantity or None)
_ITEM_RE = re.compile(r"^(?P<name>.*?)\s*[x\u00d7X]\s*(?P<qty>\d+)\s*\??$")


def parse_item(text: str) -> tuple[str, int | None]:
    """Split 'name xN' into its parts. A missing count means 'not stated'."""
    text = (text or "").strip()
    m = _ITEM_RE.match(text)
    if m:
        return m.group("name").strip(), int(m.group("qty"))
    return text.rstrip("?").strip(), None


def format_item(name: str, quantity: int | None) -> str:
    return f"{name} x{quantity}" if quantity is not None else name


# Item names CONTAIN COMMAS — 10,000拜恩紙幣 in zh_tw, and commas appear in item names in
# all six locales. Separating a list of items by comma therefore splits that one item into
# "10" and "000拜恩紙幣", and because this is the ground-truth path the damage is maximal:
# a perfectly correct reading gets recorded as one missed item plus one spurious one. It
# was found exactly that way, by a transcript that scored 13/15 chests against a recogniser
# that had in fact got all 15 right.
#
# A semicolon appears in no item name in any of the six locales (checked over all 3,400
# names per locale), so it can separate them unambiguously.
ITEM_SEPARATOR = ";"


def split_items(text: str) -> list[str]:
    """A written item list -> its items. Empty, or '(nothing)', means an empty chest —
    which is a real observation and the worst outcome, not a missing answer."""
    text = (text or "").strip()
    if text in ("", "(nothing)"):
        return []
    return [i.strip() for i in text.split(ITEM_SEPARATOR) if i.strip()]


def parse_transcript(text: str) -> dict[str, list[str]]:
    """Chest key -> true items, from a written record of what the chests held.

    Typing a session's truth at an interactive prompt is fine for a handful of chests and
    hopeless for a study: the answers cannot be reviewed before they are committed, cannot
    be diffed when a recogniser change moves them, and cannot be kept as a corpus. A
    transcript is a file, so it can be all three.

        # a whole line starting with a hash is a comment
        session-20260809-034520#1: 莫尼翁銀幣 x2; 北穿幽靈城的四鱗雜物
        session-20260809-034520#3: 朗佩爾金幣
        session-20260809-034520#4: (nothing)

    Items are separated by `;`, never by a comma — see ITEM_SEPARATOR.

    Omit `xN` exactly when the game showed no number — a quantity that is written down when
    the screen did not show one is a fabricated quantity, which is the error this whole
    mechanism exists to catch. `(nothing)` is an empty chest, which is a real observation
    and the worst outcome, not a missing entry.
    """
    out: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        # Only a WHOLE line is a comment. A chest key contains a '#' of its own, so
        # stripping from the first hash anywhere would eat every key.
        if not line or line.startswith("#"):
            continue
        key, colon, items = line.partition(":")
        if not colon:
            raise ValueError(f"transcript line is not 'key: items': {raw!r}")
        key = key.strip()
        if key in out:
            raise ValueError(f"transcript names {key} twice")
        out[key] = split_items(items)
    return out

# Verdicts a chest can carry.
CONFIRMED = "confirmed"      # what was read matches what the player saw
CORRECTED = "corrected"      # the player supplied the true contents
UNVERIFIED = "unverified"


@dataclass
class ChestTruth:
    """One chest whose true contents the player has stated."""

    key: str                       # dive_id + chest index; stable across replays of a run
    session: str                   # recording folder, so the frames can be found again
    verdict: str
    read_items: list[str] = field(default_factory=list)
    true_items: list[str] = field(default_factory=list)
    note: str = ""
    # WHO said so. A player who was there and an after-the-fact reading of the recorded
    # frames are not the same evidence — the frame reader can only see what the capture
    # caught, so it cannot testify about a message that was never sampled, which is the very
    # failure it would be used to rule out. Mixing them into one accuracy figure would
    # overstate what is known, so the sources are reported apart.
    verified_by: str = "player"

    @property
    def _read_map(self) -> dict[str, int | None]:
        return dict(parse_item(i) for i in self.read_items)

    @property
    def _true_map(self) -> dict[str, int | None]:
        return dict(parse_item(i) for i in self.true_items)

    @property
    def missed(self) -> list[str]:
        """Items that were on screen but never recorded."""
        read = self._read_map
        return [n for n in self._true_map if n not in read]

    @property
    def spurious(self) -> list[str]:
        """Items recorded that were not actually there — the worst class of error."""
        true = self._true_map
        return [n for n in self._read_map if n not in true]

    @property
    def wrong_quantity(self) -> list[str]:
        """Right item, wrong count.

        Kept apart from a wrong name because it is a different failure with a different
        cause, and because a name-only comparison would score it as a perfect read — which
        is how a fabricated x9 went unnoticed across several sessions.
        """
        read, true = self._read_map, self._true_map
        out = []
        for name, qty in true.items():
            if name in read and qty is not None and read[name] is not None and read[name] != qty:
                out.append(f"{name}: read x{read[name]}, actually x{qty}")
        return out


class TruthStore:
    """Confirmations, keyed so a replay of the same recording lines up with them."""

    def __init__(self, entries: dict[str, ChestTruth] | None = None):
        self._entries = dict(entries or {})

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def key_for(event: dict) -> str:
        """Identify one reading by its SESSION and its index, not by dive_id.

        dive_id is regenerated on every replay, so keying on it would lose every
        confirmation the moment a recording was re-read — which is exactly when the
        confirmations are needed.

        A MINING SWING IS NOT A CHEST, and it has no chest index. Both used to key on
        `chest_index_in_dive`, which is None for every panel, so a session's swings all
        answered to `<session>#None`: confirming the first one marked the rest "already
        confirmed", and they were never looked at. They are counted separately now — swings
        by `mining_index_in_dive`, chests by their own index — so the two cannot collide and
        neither can collide with itself.

        An event recorded before that counter existed falls back to the frame its first line
        was read from, which is unique per swing within a recording and is what the reader
        would be checked against anyway.
        """
        dive = event.get("dive") or {}
        label = event.get("session_label", "")
        if event.get("provenance") == "pickaxe_break":
            return f"{label}#break{dive.get('break_index_in_dive')}"
        if event.get("provenance") == "mining" or dive.get("mining_index_in_dive") is not None:
            index = dive.get("mining_index_in_dive")
            if index is None:
                contents = event.get("contents") or [{}]
                index = f"@{contents[0].get('source_frame') or dive.get('elapsed_seconds')}"
            return f"{label}#mine{index}"
        return f"{label}#{dive.get('chest_index_in_dive')}"

    def get(self, key: str) -> ChestTruth | None:
        return self._entries.get(key)

    def put(self, truth: ChestTruth) -> None:
        self._entries[truth.key] = truth

    def all(self) -> list[ChestTruth]:
        return sorted(self._entries.values(), key=lambda t: t.key)

    # -- persistence --------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "TruthStore":
        p = Path(path)
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls({k: ChestTruth(**v) for k, v in raw.items()})

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps({k: asdict(v) for k, v in self._entries.items()},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    # -- reporting ----------------------------------------------------------------
    @staticmethod
    def _counts(verified: list["ChestTruth"]) -> dict:
        chests_exact = sum(1 for t in verified if t.verdict == CONFIRMED)
        missed = sum(len(t.missed) for t in verified)
        spurious = sum(len(t.spurious) for t in verified)
        wrong_qty = sum(len(t.wrong_quantity) for t in verified)
        true_lines = sum(len(t.true_items) for t in verified)
        read_lines = sum(len(t.read_items) for t in verified)
        return {
            "chests_verified": len(verified),
            "chests_exact": chests_exact,
            "chest_accuracy": (chests_exact / len(verified)) if verified else None,
            "item_lines_true": true_lines,
            "item_lines_read": read_lines,
            "items_missed": missed,
            "items_spurious": spurious,
            "items_wrong_quantity": wrong_qty,
            "item_recall": ((true_lines - missed) / true_lines) if true_lines else None,
        }

    def accuracy(self) -> dict:
        """Counts that say how far the client can be trusted.

        Reported separately because they are different failures: a MISSED item understates a
        drop rate, a SPURIOUS one invents data, and neither is visible in a plain
        "how many chests looked right" figure.

        Broken down BY SOURCE as well as in total. A confirmation from the player who was
        there and one from a later reading of the recorded frames answer different
        questions, and only the first can speak to a message that was never captured.
        """
        verified = [t for t in self.all() if t.verdict in (CONFIRMED, CORRECTED)]
        sources = sorted({t.verified_by for t in verified})
        out = self._counts(verified)
        out["by_source"] = {
            s: self._counts([t for t in verified if t.verified_by == s]) for s in sources
        }
        return out

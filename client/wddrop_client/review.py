"""
Human-in-the-loop resolution for readings the matcher could not resolve.

WHERE THIS SITS
---------------
Resolution is attempted in order, and only what survives all of it reaches a human:

    1. exact match against the vocabulary
    2. script-variant canonicalisation (443 char mappings derived from the game's own item names; turns
       青銅短剑 -> 青銅短劍 into an exact hit)
    3. learned corrections — a misread this user already resolved once
    4. fuzzy match above threshold
    5. -> REVIEW QUEUE (this module)

Step 3 is why the queue shrinks with use rather than growing: 「雪凶鸟羽冠」 gets asked once,
and every later occurrence resolves silently.

NEVER DURING PLAY
-----------------
The queue is drained when the player chooses, not mid-dungeon. Interrupting a farming run to
ask about a word is both bad UX and bad data — a hurried answer is a guess.

THE BIAS THIS INTRODUCES, AND WHAT CONTAINS IT
----------------------------------------------
A user-picked label looks as authoritative as a machine-matched one but is not. Three
containments, all of which must stay:

* `resolution_source` travels with every item, so analysis can compare user-confirmed rows
  against auto-resolved ones and exclude them in a sensitivity check.
* Candidates are offered UNRANKED-BY-DEFAULT in the sense that none is pre-selected, and
  "not sure" is a first-class answer. Forcing a choice manufactures data.
* The original image crop is shown alongside. This is the important one: it turns the task
  from "guess which of these five strings the OCR meant" into "read the word on screen".
  Without it the user is just re-guessing the same corrupted string we already have.

PRIVACY
-------
Crops are LOCAL ONLY. DISCLAIMER.md §3 promises no screenshots are collected, so a crop is
kept on disk purely to render the review UI, is never attached to an upload, and is deleted
with its queue entry.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger("wddrop.review")

# How many alternatives to offer. Enough to contain the answer, few enough to actually read.
MAX_CANDIDATES = 5
# Below this a candidate is not worth showing at all — it would only add noise and invite a
# wrong pick.
MIN_CANDIDATE_SCORE = 0.45


class ResolutionSource(str, __import__("enum").Enum):
    """How an item's identity was established. Travels into the upload."""

    AUTO_EXACT = "auto_exact"
    AUTO_VARIANT = "auto_variant"        # resolved after script-variant canonicalisation
    AUTO_LEARNED = "auto_learned"        # a correction this user confirmed earlier
    AUTO_FUZZY = "auto_fuzzy"
    USER_CONFIRMED = "user_confirmed"
    UNRESOLVED = "unresolved"


@dataclass
class Candidate:
    name: str
    score: float
    item_id: int | None = None
    item_type: str | None = None
    identification: int | None = None


@dataclass
class ReviewItem:
    """One unresolved reading awaiting a human decision."""

    key: str                       # normalised reading; also the dedup key
    raw_text: str
    read_name: str
    occurred_at: str
    candidates: list[Candidate] = field(default_factory=list)
    dungeon_id: int | None = None
    crop_path: str | None = None   # local only, never uploaded
    occurrences: int = 1


class CorrectionMap:
    """Misread -> confirmed name, learned from this user's own decisions."""

    def __init__(self, mapping: dict[str, str] | None = None):
        self._map = dict(mapping or {})

    @staticmethod
    def normalise(text: str) -> str:
        return unicodedata.normalize("NFKC", text or "").replace(" ", "").strip().lower()

    def get(self, read_name: str) -> str | None:
        return self._map.get(self.normalise(read_name))

    def learn(self, read_name: str, confirmed_name: str) -> None:
        self._map[self.normalise(read_name)] = confirmed_name

    def forget(self, read_name: str) -> None:
        self._map.pop(self.normalise(read_name), None)

    def to_dict(self) -> dict[str, str]:
        return dict(self._map)

    @classmethod
    def load(cls, path: Path) -> "CorrectionMap":
        p = Path(path)
        if not p.exists():
            return cls()
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        Path(path).write_text(
            json.dumps(self._map, ensure_ascii=False, indent=1), encoding="utf-8"
        )


class ReviewQueue:
    """Bounded, deduplicated queue of readings needing a human decision."""

    def __init__(self, max_items: int = 200):
        self.max_items = max_items
        self._items: dict[str, ReviewItem] = {}

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> list[ReviewItem]:
        # Most-repeated first: resolving those clears the most future readings.
        return sorted(self._items.values(), key=lambda i: (-i.occurrences, i.occurred_at))

    def add(
        self,
        read_name: str,
        raw_text: str,
        candidates: list[Candidate],
        *,
        occurred_at: datetime,
        dungeon_id: int | None = None,
        crop_path: str | None = None,
    ) -> ReviewItem:
        key = CorrectionMap.normalise(read_name)
        existing = self._items.get(key)
        if existing is not None:
            # The same misread recurring is one question, not many.
            existing.occurrences += 1
            return existing

        if len(self._items) >= self.max_items:
            # Drop the least-repeated, oldest entry rather than growing without bound.
            victim = min(self._items.values(), key=lambda i: (i.occurrences, i.occurred_at))
            log.info("wddrop: review queue full, dropping %r", victim.read_name)
            self._items.pop(victim.key, None)

        item = ReviewItem(
            key=key,
            raw_text=raw_text,
            read_name=read_name,
            occurred_at=occurred_at.isoformat(),
            candidates=[c for c in candidates if c.score >= MIN_CANDIDATE_SCORE][:MAX_CANDIDATES],
            dungeon_id=dungeon_id,
            crop_path=crop_path,
        )
        self._items[key] = item
        return item

    def resolve(self, key: str, confirmed_name: str, corrections: CorrectionMap) -> ReviewItem | None:
        """Record the user's choice and learn it so the same misread never asks again."""
        item = self._items.pop(key, None)
        if item is None:
            return None
        corrections.learn(item.read_name, confirmed_name)
        self._discard_crop(item)
        return item

    def skip(self, key: str) -> None:
        """'Not sure' — drop the question WITHOUT learning anything.

        A skip must never be recorded as a resolution: an uncertain user producing a label
        is exactly the failure mode `resolution_source` exists to keep out of the data.
        """
        item = self._items.pop(key, None)
        if item is not None:
            self._discard_crop(item)

    @staticmethod
    def _discard_crop(item: ReviewItem) -> None:
        if not item.crop_path:
            return
        try:
            Path(item.crop_path).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("wddrop: could not remove crop %s: %s", item.crop_path, exc)

    # -- persistence --------------------------------------------------------------
    def save(self, path: Path) -> None:
        payload = [asdict(i) for i in self.items]
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, max_items: int = 200) -> "ReviewQueue":
        q = cls(max_items=max_items)
        p = Path(path)
        if not p.exists():
            return q
        for raw in json.loads(p.read_text(encoding="utf-8")):
            cands = [Candidate(**c) for c in raw.pop("candidates", [])]
            item = ReviewItem(candidates=cands, **raw)
            q._items[item.key] = item
        return q


def top_candidates(vocab, read_name: str, limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Best vocabulary entries for a reading, for the user to choose between."""
    from difflib import SequenceMatcher

    key = vocab._norm(read_name)
    scored = []
    for entry in vocab.entries:
        score = SequenceMatcher(None, key, vocab._norm(entry.name)).ratio()
        if score >= MIN_CANDIDATE_SCORE:
            scored.append(
                Candidate(
                    name=entry.name,
                    score=round(score, 4),
                    item_id=entry.item_id,
                    item_type=entry.item_type,
                    identification=entry.identification,
                )
            )
    scored.sort(key=lambda c: -c.score)
    return scored[:limit]

"""
Tests for the human-in-the-loop review queue.

The case throughout is real: 雪兇鳥羽冠 misread by one character scores 0.800, under the
0.82 threshold, so it cannot be auto-resolved and is exactly what the queue is for.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

from wddrop_client.capture.ocr import Vocabulary, VocabEntry  # noqa: E402
from wddrop_client.review import (  # noqa: E402
    Candidate, CorrectionMap, ResolutionSource, ReviewQueue, top_candidates,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
MISREAD = "雪兜鳥羽冠"
TRUE_NAME = "雪兇鳥羽冠"


def vocab() -> Vocabulary:
    return Vocabulary([
        VocabEntry(name=TRUE_NAME, identification=400101200, ids=(400101200,)),
        VocabEntry(name="雪兇鳥羽衣", identification=400101300, ids=(400101300,)),
        VocabEntry(name="蒼藍礦石", item_id=20000001, item_type="Item::SaleOnly"),
    ])


def test_candidates_surface_the_true_name():
    cands = top_candidates(vocab(), MISREAD)
    assert cands, "a near-miss must produce candidates to choose from"
    assert TRUE_NAME in [c.name for c in cands]
    # Sorted best-first, but the UI must not pre-select — that is a UI contract, not here.
    assert cands == sorted(cands, key=lambda c: -c.score)


def test_wildly_wrong_reading_offers_nothing_rather_than_noise():
    assert top_candidates(vocab(), "完全看不懂的字") == []


def test_repeated_misread_is_one_question():
    q = ReviewQueue()
    for _ in range(4):
        q.add(MISREAD, f"獲得了{MISREAD}！！", top_candidates(vocab(), MISREAD), occurred_at=NOW)
    assert len(q) == 1
    assert q.items[0].occurrences == 4


def test_resolving_teaches_the_correction_map():
    q, corr = ReviewQueue(), CorrectionMap()
    q.add(MISREAD, "raw", top_candidates(vocab(), MISREAD), occurred_at=NOW)
    q.resolve(q.items[0].key, TRUE_NAME, corr)
    assert len(q) == 0
    assert corr.get(MISREAD) == TRUE_NAME
    # And the same misread now resolves without ever reaching a human again.
    assert corr.get("雪兜鳥羽冠 ") == TRUE_NAME     # whitespace-insensitive


def test_skip_does_not_learn_anything():
    """'Not sure' must not become a label. An uncertain user producing data is the exact
    failure mode resolution_source exists to keep out."""
    q, corr = ReviewQueue(), CorrectionMap()
    q.add(MISREAD, "raw", top_candidates(vocab(), MISREAD), occurred_at=NOW)
    q.skip(q.items[0].key)
    assert len(q) == 0
    assert corr.get(MISREAD) is None


def test_queue_is_bounded_and_drops_the_least_repeated():
    q = ReviewQueue(max_items=3)
    q.add("common", "r", [], occurred_at=NOW)
    q.add("common", "r", [], occurred_at=NOW)     # 2 occurrences
    q.add("rare1", "r", [], occurred_at=NOW)
    q.add("rare2", "r", [], occurred_at=NOW)
    q.add("rare3", "r", [], occurred_at=NOW)      # evicts a 1-occurrence entry
    assert len(q) == 3
    assert "common" in [i.read_name for i in q.items]


def test_most_repeated_is_offered_first():
    q = ReviewQueue()
    q.add("once", "r", [], occurred_at=NOW)
    for _ in range(3):
        q.add("thrice", "r", [], occurred_at=NOW)
    assert q.items[0].read_name == "thrice"


def test_crop_is_deleted_on_resolve_and_on_skip(tmp_path):
    """Crops are local-only (DISCLAIMER §3 promises no screenshots are collected); they must
    not outlive the question they were shown for."""
    corr = CorrectionMap()
    for action in ("resolve", "skip"):
        crop = tmp_path / f"{action}.png"
        crop.write_bytes(b"x")
        q = ReviewQueue()
        q.add(MISREAD, "raw", [], occurred_at=NOW, crop_path=str(crop))
        key = q.items[0].key
        if action == "resolve":
            q.resolve(key, TRUE_NAME, corr)
        else:
            q.skip(key)
        assert not crop.exists(), f"crop survived {action}"


def test_queue_and_corrections_round_trip(tmp_path):
    q, corr = ReviewQueue(), CorrectionMap()
    q.add(MISREAD, "獲得了…", top_candidates(vocab(), MISREAD), occurred_at=NOW, dungeon_id=2003)
    qp, cp = tmp_path / "q.json", tmp_path / "c.json"
    q.save(qp)
    corr.learn("abc", "def")
    corr.save(cp)

    q2, c2 = ReviewQueue.load(qp), CorrectionMap.load(cp)
    assert len(q2) == 1
    item = q2.items[0]
    assert item.read_name == MISREAD and item.dungeon_id == 2003
    assert TRUE_NAME in [c.name for c in item.candidates]
    assert c2.get("abc") == "def"


def test_loading_absent_files_is_empty_not_an_error(tmp_path):
    assert len(ReviewQueue.load(tmp_path / "nope.json")) == 0
    assert CorrectionMap.load(tmp_path / "nope.json").to_dict() == {}


def test_resolution_source_distinguishes_user_from_machine():
    """Analysis must be able to exclude user-confirmed rows in a sensitivity check."""
    assert ResolutionSource.USER_CONFIRMED != ResolutionSource.AUTO_EXACT
    assert {s.value for s in ResolutionSource} >= {
        "auto_exact", "auto_variant", "auto_learned", "auto_fuzzy",
        "user_confirmed", "unresolved",
    }

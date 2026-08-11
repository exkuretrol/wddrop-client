"""
Cross-checking the declared dungeon against what the chest contained.

`dungeon_id` is user_declared and is the analysis STRATUM, so a wrong one does not add
noise — it moves observations into another dungeon's distribution. The first real session
recorded five chests of 北穿幽靈城 junk labelled 初始的奈落, because the window's dropdown
opened on a real dungeon and was never touched.

Junk is named after the dungeon it comes from, which makes the label checkable for free.
The tests below pin the three properties that matter: it must catch a genuine conflict, it
must stay SILENT when the contents say nothing, and it must never guess.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

from wddrop_client.labels import DungeonHints  # noqa: E402


@pytest.fixture
def hints(tmp_path):
    (tmp_path / "catalog.zh_tw.json").write_text(json.dumps({
        "locale": "zh_tw",
        "dungeons": [
            {"id": 2000, "name": "初始的奈落", "floors": []},
            {"id": 7015, "name": "北穿幽靈城", "floors": []},
            # 試煉洞窟 is a genuine prefix of the graded variants in the real catalogue.
            {"id": 5400, "name": "試煉洞窟", "floors": []},
            {"id": 5401, "name": "試煉洞窟（青銅階）", "floors": []},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "vocab.zh_tw.json").write_text(json.dumps({
        "locale": "zh_tw",
        "items": [
            {"name": "北穿幽靈城的妖異冥刻雜物", "type": "Item::Junk"},
            {"name": "北穿幽靈城的四鱗雜物", "type": "Item::Junk"},
            {"name": "初始的奈落的雜物", "type": "Item::Junk"},
            {"name": "試煉洞窟的雜物", "type": "Item::Junk"},
            {"name": "試煉洞窟（青銅階）的雜物", "type": "Item::Junk"},
            # Not junk, and it starts with a dungeon name: it must NOT count as evidence.
            {"name": "初始的奈落鑰匙", "type": "Item::Valuable"},
            {"name": "100拜恩紙幣", "type": "Item::Valuable"},
        ],
        "equipment": [],
    }, ensure_ascii=False), encoding="utf-8")
    return DungeonHints.load(tmp_path / "vocab.zh_tw.json")


def test_it_names_the_dungeon_the_junk_came_from(hints):
    assert hints.infer(["北穿幽靈城的妖異冥刻雜物", "100拜恩紙幣"]) == 7015


def test_it_says_nothing_when_the_contents_say_nothing(hints):
    """Most chests name no dungeon at all — 36% coverage in zh_tw, 0% in de. Silence must
    mean 'no evidence', never 'agreed', or a locale without the convention would look
    permanently correct."""
    assert hints.infer(["100拜恩紙幣"]) is None
    assert hints.check(2000, ["100拜恩紙幣"]) == {}


def test_a_non_junk_item_is_not_evidence(hints):
    """初始的奈落鑰匙 starts with a dungeon name but is a key, not that dungeon's junk."""
    assert hints.dungeon_of("初始的奈落鑰匙") is None
    assert hints.infer(["初始的奈落鑰匙"]) is None


def test_the_longest_dungeon_name_wins(hints):
    """試煉洞窟 is a prefix of 試煉洞窟（青銅階）, so matching in list order would file the
    graded dungeon's junk under the ungraded one."""
    assert hints.dungeon_of("試煉洞窟（青銅階）的雜物") == 5401
    assert hints.dungeon_of("試煉洞窟的雜物") == 5400


def test_contradictory_contents_are_not_a_guess(hints):
    """A chest naming two dungeons can happen legitimately. Choosing between them would be
    inventing a label rather than checking one."""
    assert hints.infer(["北穿幽靈城的四鱗雜物", "初始的奈落的雜物"]) is None


def test_a_conflict_is_flagged_and_the_evidence_is_kept(hints):
    qc = hints.check(2000, ["北穿幽靈城的妖異冥刻雜物"])
    assert qc == {"contents_dungeon_id": 7015, "label_conflict": True}
    # The message names dungeons, not ids — the player picked from a list of names.
    assert "北穿幽靈城" in hints.describe_conflict(2000, qc)
    assert "初始的奈落" in hints.describe_conflict(2000, qc)


def test_agreement_records_the_hint_without_a_conflict(hints):
    """The hint rides along even when it agrees: it is what makes a batch auditable later."""
    assert hints.check(7015, ["北穿幽靈城的四鱗雜物"]) == {"contents_dungeon_id": 7015}


def test_a_missing_catalogue_disables_the_check_rather_than_failing(tmp_path):
    """The cross-check is an extra. A client without a catalogue must still capture."""
    (tmp_path / "vocab.zh_tw.json").write_text(
        json.dumps({"locale": "zh_tw", "items": [], "equipment": []}), encoding="utf-8")
    empty = DungeonHints.load(tmp_path / "vocab.zh_tw.json")
    assert len(empty) == 0
    assert empty.check(2000, ["anything"]) == {}


# -- against the real data --------------------------------------------------------
REAL_VOCAB = ROOT / "data" / "vocab.zh_tw.json"
REAL_CATALOG = ROOT / "data" / "catalog.zh_tw.json"


@pytest.mark.skipif(not (REAL_VOCAB.exists() and REAL_CATALOG.exists()),
                    reason="built data files not present")
def test_the_real_session_that_started_this_is_caught():
    """The five chests that were labelled 初始的奈落 while holding 北穿幽靈城 junk."""
    hints = DungeonHints.load(REAL_VOCAB, REAL_CATALOG)
    contents = ["蒼藍礦石塊", "10,000拜恩紙幣", "北穿幽靈城的尋常四鱗雜物"]
    qc = hints.check(2000, contents)
    assert qc == {"contents_dungeon_id": 7015, "label_conflict": True}
    # And the same chest, correctly labelled, is not flagged.
    assert hints.check(7015, contents) == {"contents_dungeon_id": 7015}


def test_a_conflict_is_reported_by_name_not_by_id(hints):
    """The window phrases this sentence itself, in the player's language, so it is handed
    the two names — an id in the message is a number nobody can act on."""
    qc = {"label_conflict": True, "contents_dungeon_id": 7015}
    names = hints.conflict_names(2000, qc)
    assert names is not None
    for name in names:
        assert name and not name.isdigit()
    assert hints.conflict_names(2000, {}) is None

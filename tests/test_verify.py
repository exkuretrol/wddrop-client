"""
Tests for the ground-truth store.

The distinction these defend: a MISSED item and a SPURIOUS one are different failures. A
miss understates a drop rate; a spurious item invents data. A single "was the chest right?"
number hides both, which is how a fabricated quantity survived several sessions.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `__main__` imports the schema package; the verify module itself does not.

from wddrop_client.verify import CONFIRMED, CORRECTED, ChestTruth, TruthStore


def test_key_is_stable_across_replays():
    """dive_id is regenerated on every replay, so keying on it would discard every
    confirmation exactly when a recogniser change makes them valuable."""
    a = {"session_label": "session-1", "dive": {"dive_id": "aaa", "chest_index_in_dive": 2}}
    b = {"session_label": "session-1", "dive": {"dive_id": "zzz", "chest_index_in_dive": 2}}
    assert TruthStore.key_for(a) == TruthStore.key_for(b)


def test_key_separates_sessions():
    a = {"session_label": "session-1", "dive": {"chest_index_in_dive": 1}}
    b = {"session_label": "session-2", "dive": {"chest_index_in_dive": 1}}
    assert TruthStore.key_for(a) != TruthStore.key_for(b)


def test_missed_and_spurious_are_distinguished():
    t = ChestTruth(key="k", session="s", verdict=CORRECTED,
                   read_items=["A", "X"], true_items=["A", "B"])
    assert t.missed == ["B"]
    assert t.spurious == ["X"]


def test_confirmed_chest_has_no_errors():
    t = ChestTruth(key="k", session="s", verdict=CONFIRMED,
                   read_items=["A", "B"], true_items=["A", "B"])
    assert t.missed == [] and t.spurious == []


def test_accuracy_counts_items_not_just_chests():
    """A chest read as one item when it held two is 'one chest wrong' but also 'one item
    missed' — and recall is the number that matters for a drop-rate study."""
    store = TruthStore()
    store.put(ChestTruth(key="a", session="s", verdict=CONFIRMED,
                         read_items=["A", "B"], true_items=["A", "B"]))
    store.put(ChestTruth(key="b", session="s", verdict=CORRECTED,
                         read_items=["C"], true_items=["C", "D"]))
    acc = store.accuracy()
    assert acc["chests_verified"] == 2
    assert acc["chests_exact"] == 1
    assert acc["items_missed"] == 1
    assert acc["items_spurious"] == 0
    assert acc["item_lines_true"] == 4
    assert acc["item_recall"] == 0.75


def test_round_trip(tmp_path):
    store = TruthStore()
    store.put(ChestTruth(key="a", session="s", verdict=CORRECTED,
                         read_items=["C"], true_items=["C", "D"], note="fast click"))
    p = tmp_path / "verified.json"
    store.save(p)
    back = TruthStore.load(p)
    assert len(back) == 1
    t = back.get("a")
    assert t.true_items == ["C", "D"] and t.missed == ["D"] and t.note == "fast click"


def test_loading_absent_file_is_empty(tmp_path):
    assert len(TruthStore.load(tmp_path / "nope.json")) == 0


# -- quantities are part of the truth ---------------------------------------------
# A fabricated quantity is a wrong NUMBER on a correct name, so a name-only comparison
# scores it as perfect. That is how a real x1 recorded as x9 went unnoticed for sessions.

def test_parse_item_forms():
    from wddrop_client.verify import parse_item

    assert parse_item("蒼藍礦石 x3") == ("蒼藍礦石", 3)
    assert parse_item("蒼藍礦石 ×3") == ("蒼藍礦石", 3)
    assert parse_item("蒼藍礦石 x1?") == ("蒼藍礦石", 1)
    assert parse_item("蒼藍礦石") == ("蒼藍礦石", None)


def test_wrong_quantity_is_its_own_category():
    t = ChestTruth(key="k", session="s", verdict=CORRECTED,
                   read_items=["大暴雪符咒 x9"], true_items=["大暴雪符咒 x1"])
    assert t.missed == [] and t.spurious == []
    assert t.wrong_quantity == ["大暴雪符咒: read x9, actually x1"]


def test_quantity_error_counted_in_accuracy():
    store = TruthStore()
    store.put(ChestTruth(key="a", session="s", verdict=CORRECTED,
                         read_items=["A x9"], true_items=["A x1"]))
    acc = store.accuracy()
    assert acc["items_wrong_quantity"] == 1
    assert acc["items_missed"] == 0 and acc["items_spurious"] == 0


def test_unstated_quantity_is_not_a_mismatch():
    """A line the game showed without a number is recorded as unknown; that must not count
    as a quantity error against a truth that also states none."""
    t = ChestTruth(key="k", session="s", verdict=CONFIRMED,
                   read_items=["A"], true_items=["A"])
    assert t.wrong_quantity == []


# -- profiles are keyed by resolution ---------------------------------------------
def test_profile_store_keys_by_resolution(tmp_path):
    """Windowed and fullscreen must coexist. Every region in a profile is absolute pixels, so
    one profile is only valid at one resolution — keeping just the newest meant recalibrating
    every time the window changed, and discarding the other fit."""
    from wddrop_client.calibration import Profile, ProfileStore

    def make(w, h, size):
        return Profile(frame_size=(w, h), message_band=(10, 30), font_path="f.ttf",
                       font_size=size, offset=(0, 0), calibration_score=0.9)

    store = ProfileStore()
    store.put(make(1920, 1080, 22))
    store.put(make(2560, 1440, 29))
    store.save(tmp_path)

    back = ProfileStore.load(tmp_path)
    assert back.keys() == ["1920x1080", "2560x1440"]
    assert back.get((1920, 1080)).font_size == 22
    assert back.get((2560, 1440)).font_size == 29
    assert back.get((1280, 720)) is None
    assert back.only() is None          # ambiguous when several exist


def test_profile_store_falls_back_to_a_legacy_single_profile(tmp_path):
    """Existing setups have profile.json and must keep working without recalibrating."""
    from wddrop_client.calibration import Profile, ProfileStore

    Profile(frame_size=(1920, 1080), message_band=(10, 30), font_path="f.ttf",
            font_size=22, offset=(0, 0), calibration_score=0.9).save(tmp_path / "profile.json")
    store = ProfileStore.load(tmp_path)
    assert store.keys() == ["1920x1080"]
    assert store.only() is not None


# -- transcripts, and the comma that cost two chests ------------------------------
def test_items_are_separated_by_semicolon_because_names_contain_commas():
    """10,000拜恩紙幣 is one item. Splitting a stated list on commas turns it into "10" and
    "000拜恩紙幣" — one missed item plus one spurious one, invented out of a reading that
    was in fact correct. Measured: a transcript scored 13/15 chests against a recogniser
    that had got all 15 right."""
    from wddrop_client.verify import split_items

    assert split_items("10,000拜恩紙幣") == ["10,000拜恩紙幣"]
    assert split_items("大巨岩符咒; 10,000拜恩紙幣; 雜物 x3") == [
        "大巨岩符咒", "10,000拜恩紙幣", "雜物 x3"]


def test_empty_chest_is_an_observation_not_a_blank():
    from wddrop_client.verify import split_items

    assert split_items("(nothing)") == []
    assert split_items("") == []


def test_transcript_parses_keys_containing_a_hash():
    from wddrop_client.verify import parse_transcript

    parsed = parse_transcript(
        "# a comment\n"
        "\n"
        "session-20260809-034520#1: 莫尼翁銀幣 x2; 四鱗雜物\n"
        "session-20260809-034520#2: (nothing)\n"
    )
    assert parsed == {
        "session-20260809-034520#1": ["莫尼翁銀幣 x2", "四鱗雜物"],
        "session-20260809-034520#2": [],
    }


def test_transcript_refuses_a_line_without_a_key():
    import pytest

    from wddrop_client.verify import parse_transcript

    with pytest.raises(ValueError):
        parse_transcript("莫尼翁銀幣 x2\n")


def test_transcript_refuses_a_duplicated_chest():
    """Two answers for one chest is a mistake in the record, and silently keeping the last
    would decide ground truth by file order."""
    import pytest

    from wddrop_client.verify import parse_transcript

    with pytest.raises(ValueError):
        parse_transcript("s#1: A\ns#1: B\n")


def test_accuracy_reports_sources_apart():
    """A player who was there and a later reading of the recorded frames are not the same
    evidence: the frame reader can only see what the capture caught, so it cannot rule out
    a message that was never sampled. One merged figure would overstate what is known."""
    store = TruthStore()
    store.put(ChestTruth(key="s#1", session="s", verdict=CONFIRMED,
                         read_items=["A"], true_items=["A"], verified_by="player"))
    store.put(ChestTruth(key="s#2", session="s", verdict=CORRECTED,
                         read_items=["A"], true_items=["A", "B"], verified_by="frames"))
    acc = store.accuracy()
    assert acc["chests_verified"] == 2 and acc["items_missed"] == 1
    assert acc["by_source"]["player"]["items_missed"] == 0
    assert acc["by_source"]["frames"]["items_missed"] == 1


def test_verified_by_defaults_to_player_for_older_records():
    """Confirmations recorded before the field existed were all made by the player."""
    assert ChestTruth(key="k", session="s", verdict=CONFIRMED).verified_by == "player"


# -- a swing is not a chest, and two swings are not each other --------------------------

def test_every_swing_of_a_session_has_its_own_key():
    """The bug this replaces: mining events carried no chest index, so `chest_index_in_dive`
    was None for all of them and they all answered to `<session>#None`. Confirming the first
    swing of a session marked every other swing "already confirmed", and the rest of the
    session was never checked — silently, and in the one tool whose whole job is checking."""
    from wddrop_client.verify import TruthStore

    swings = [
        {"session_label": "session-20260814-185958", "provenance": "mining",
         "dive": {"mining_index_in_dive": n, "elapsed_seconds": n * 5}}
        for n in (1, 2, 3)
    ]
    keys = [TruthStore.key_for(e) for e in swings]
    assert len(set(keys)) == 3, keys
    assert keys[0].endswith("#mine1")


def test_a_swing_and_a_chest_cannot_collide():
    from wddrop_client.verify import TruthStore

    label = "session-20260814-185958"
    chest = {"session_label": label, "provenance": "chest_direct",
             "dive": {"chest_index_in_dive": 1}}
    swing = {"session_label": label, "provenance": "mining",
             "dive": {"mining_index_in_dive": 1}}
    assert TruthStore.key_for(chest) != TruthStore.key_for(swing)
    # And a chest's key is UNCHANGED, so confirmations already on disk still match.
    assert TruthStore.key_for(chest) == f"{label}#1"


def test_a_swing_recorded_before_the_counter_falls_back_to_its_frame():
    """Old spools have no `mining_index_in_dive`. Colliding them all onto one key is the bug;
    the frame a swing was read from is unique within a recording and is what the reader would
    be checked against anyway."""
    from wddrop_client.verify import TruthStore

    label = "session-20260814-185958"
    old = [{"session_label": label, "provenance": "mining", "dive": {"elapsed_seconds": t},
            "contents": [{"item_name": "下級鉄鉱石", "source_frame": f}]}
           for t, f in ((51, "episode-022/f_00004.png"), (123, "episode-028/f_00004.png"))]
    keys = [TruthStore.key_for(e) for e in old]
    assert len(set(keys)) == 2, keys


def test_a_pickaxe_break_is_a_reading_with_its_own_key():
    """It is the reading with the most to lose from being wrong — a false one spends a
    pickaxe the player still has and moves the swings-since-break figure the whole
    pickaxe-lifetime number rests on — and `verify` could not see it at all."""
    from wddrop_client.verify import TruthStore

    label = "session-20260813-145742"
    breaks = [{"session_label": label, "provenance": "pickaxe_break",
               "dive": {"break_index_in_dive": n}} for n in (1, 2)]
    keys = [TruthStore.key_for(e) for e in breaks]
    assert keys == [f"{label}#break1", f"{label}#break2"]
    # And it collides with neither a chest nor a swing at the same index.
    assert keys[0] != TruthStore.key_for(
        {"session_label": label, "dive": {"chest_index_in_dive": 1}})
    assert keys[0] != TruthStore.key_for(
        {"session_label": label, "provenance": "mining", "dive": {"mining_index_in_dive": 1}})


# -- re-reading a confirmed session is how a change gets checked -------------------------

def test_verify_shows_what_you_confirmed_next_to_what_it_reads_now():
    """Only the current reading was printed, so a re-run after a change to the reader said
    "already confirmed" over a DIFFERENT answer and compared nothing. A confirmed session is
    the only ground truth this project has."""
    from wddrop_client.__main__ import _against_what_was_confirmed
    from wddrop_client.verify import CONFIRMED, ChestTruth

    truth = ChestTruth(key="s#mine6", session="s", verdict=CONFIRMED,
                       read_items=["上級鉄鉱石 x3"], true_items=["上級鉄鉱石 x3", "透明な小石 x3"],
                       verified_by="tester")
    same, lines = _against_what_was_confirmed(truth, ["上級鉄鉱石 x3", "透明な小石 x3"])
    assert same
    assert any("you confirmed" in ln and "上級鉄鉱石 x3" in ln for ln in lines)
    assert any("tester" in ln for ln in lines)

    differs, lines = _against_what_was_confirmed(truth, ["下級鉄鉱石 x3", "透明な小石 x3"])
    assert not differs
    assert any("DIFFERS" in ln and "下級鉄鉱石 x3" in ln for ln in lines), lines


def test_an_empty_reading_is_shown_as_nothing_rather_than_as_a_blank():
    """A chest that paid out nothing and a chest whose lines were all refused look identical
    as an empty list, and the difference is the whole point of confirming one."""
    from wddrop_client.__main__ import _against_what_was_confirmed
    from wddrop_client.verify import CONFIRMED, ChestTruth

    truth = ChestTruth(key="s#4", session="s", verdict=CONFIRMED, read_items=[],
                       true_items=[], verified_by="tester")
    same, lines = _against_what_was_confirmed(truth, [])
    assert same and any("(nothing)" in ln for ln in lines)


def test_re_answering_is_narrowed_to_what_changed_not_the_whole_session():
    """The replay cannot be narrowed — every frame has to be read again to produce the
    readings at all — but the QUESTIONS can, and a session is a dozen of them. Being asked
    about thirteen agreements to reach the one disagreement is how a confirmed session stops
    being re-checked at all.

        (nothing)     confirmed entries are shown and not asked about
        --differing   asked only where the client now reads something else
        --again       asked about everything

    Encoded as the decision itself, since it is a line of flags in a loop that is otherwise
    only exercised by a recording.
    """
    def ask(*, again: bool, differing: bool, same: bool) -> bool:
        return again or (differing and not same)

    assert not ask(again=False, differing=False, same=True)
    assert not ask(again=False, differing=False, same=False)
    assert not ask(again=False, differing=True, same=True)
    assert ask(again=False, differing=True, same=False), "the case worth a person's time"
    assert ask(again=True, differing=False, same=True)
    assert ask(again=True, differing=False, same=False)


def test_the_flags_are_offered_on_verify():
    """A flag nobody can find is a flag that does not exist. `verify --help` names both."""
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    out = subprocess.run([_sys.executable, str(root / "wddrop.py"), "verify", "--help"],
                         capture_output=True, text=True, timeout=120)
    assert "--differing" in out.stdout, out.stdout[-400:]
    assert "--again" in out.stdout

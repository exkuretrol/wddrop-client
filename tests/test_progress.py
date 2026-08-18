"""
The story-progress question: what it stores, and when it is put.

It exists because most dungeons scale with a value the game keeps on its own side — enemy
strength, which groups appear at all, and what some quests pay (2,500 / 4,500 / 8,500 gold on
the same quest in the dungeon this study farms). Whether it also moves what is in a chest is
the open question, and it can only be answered if the covariate sits beside the drops.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from wddrop_client import progress  # noqa: E402


class Cfg:
    """The two fields this reads, and a save that records it happened."""

    def __init__(self, **kw):
        self.progress_bits = kw.get("bits", "")
        self.progress_width = kw.get("width", 0)
        self.progress_asked_at = kw.get("asked_at")
        self.progress_interval_days = kw.get("interval", 14)
        self.character_grade = kw.get("grade")
        self.saved = 0

    def save(self):
        self.saved += 1


def test_a_bit_that_did_not_exist_yet_is_unknown_not_no():
    """The whole reason the width is stored. A player answered when there were five endings;
    a build that knows eight must not read the three it added as "they have not done those" —
    nobody asked them. Getting this wrong invents data rather than losing it, which is worse.
    """
    seen = progress.decode("10110", 5)
    assert seen["abyss1_executed"] is True
    assert seen[progress.ENDINGS[1].key] is False
    assert seen["abyss4_villagers"] is None, "a bit beyond the answer's width is unknown"
    assert seen["abyss4_cleared"] is None


def test_an_answer_round_trips():
    answer = {e.key: (i % 2 == 0) for i, e in enumerate(progress.ENDINGS)}
    bits, width = progress.encode(answer)
    assert width == progress.WIDTH and len(bits) == progress.WIDTH
    assert progress.decode(bits, width) == answer


def test_the_answer_is_one_integer_of_flags():
    """Same encoding as any permissions field: bit N is ENDINGS[N], OR to combine, AND to
    test, and a new condition takes the next power of two. 1 + 2 = both of the first two."""
    answer = {e.key: False for e in progress.ENDINGS}
    answer[progress.ENDINGS[0].key] = True
    assert progress.as_flags(answer) == 1
    answer[progress.ENDINGS[1].key] = True
    assert progress.as_flags(answer) == 3
    assert progress.from_flags(3)[progress.ENDINGS[0].key] is True
    assert progress.from_flags(3)[progress.ENDINGS[2].key] is False


def test_the_reference_file_and_the_code_agree():
    """The file describes what every bit means, for anything reading a stored answer back
    after this list has grown. If the two drift, a row recorded today is interpreted with
    tomorrow's meanings — the one failure this whole design exists to prevent.
    """
    import json

    reference = json.loads((ROOT / "data" / "progress_conditions.json")
                           .read_text(encoding="utf-8"))
    conditions = reference["conditions"]
    assert [c["bit"] for c in conditions] == list(range(len(conditions))), "bits are not dense"
    assert len(conditions) == progress.WIDTH

    endings = {e["key"] for e in reference["endings"]}
    for described, ending in zip(conditions, progress.ENDINGS):
        assert described["key"] == ending.key, "the file and the code disagree on a bit"
        assert described["dungeon_id"] == ending.dungeon_id
        assert described["label"] == ending.label
        assert tuple(described["covers"]) == ending.covers
        for covered in described["covers"]:
            assert covered in endings, f"{covered} is not a known ending"


def test_the_reference_says_which_endings_move_the_game():
    """Fourteen of the fifteen do. The flag exists because ONE does not, and a design that
    listed only the ones that count would have to be rewritten the day that changes."""
    import json

    reference = json.loads((ROOT / "data" / "progress_conditions.json")
                           .read_text(encoding="utf-8"))
    endings = reference["endings"]
    assert len(endings) == 15
    raising = [e for e in endings if e["raises_dungeon_level"]]
    assert len(raising) == 14
    assert [e["key"] for e in endings if not e["raises_dungeon_level"]] == ["ed_pattern_bad_na3"]
    assert all("confirmed" in e for e in endings), "each has to say how sure we are"


def test_every_ending_the_game_tracks_has_a_bit():
    """One bit per ending, after a re-cut. The list used to fold normal / true / extra into
    one "you finished it", on the reasoning that a player cannot tell them apart — but the
    game does not decide them by a label, it decides them by things that happened: everyone
    survived or someone did not, the evidence was handed over or it was not. Asked that way
    they are answerable, and the finer answer is the one worth having.
    """
    import json

    reference = json.loads((ROOT / "data" / "progress_conditions.json")
                           .read_text(encoding="utf-8"))
    endings = {e["key"] for e in reference["endings"]}
    covered = {c for e in progress.ENDINGS for c in e.covers}
    assert covered == endings, f"not asked about: {sorted(endings - covered)}"
    assert len(progress.ENDINGS) == len(endings), "a bit covers more than one ending"


def test_an_ending_is_listed_after_whatever_it_needs():
    """The list is read top to bottom, so anything that cannot be reached yet has to come
    after what it waits on. Several requirements mean ANY of them — the reconciliation rides
    along with either clear, not with a particular one.
    """
    position = {e.key: i for i, e in enumerate(progress.ENDINGS)}
    for ending in progress.ENDINGS:
        for needed in ending.requires:
            assert needed in position, f"{ending.key} waits on an ending that does not exist"
            assert position[needed] < position[ending.key], \
                f"{ending.key} is listed before {needed}, which it needs"


def test_a_condition_that_changes_nothing_is_still_asked():
    """The chapter-3 bad ending raises nothing at all, and is still a bit. Without it, a
    player who played that chapter and lost the duke is the same answer as one who never
    opened it — and "never played" is the reading that would quietly dominate."""
    keys = [e.key for e in progress.ENDINGS]
    assert "abyss3_duke_died" in keys


def test_the_keys_are_append_only():
    """The order and the names ARE the storage format: bit N means whatever ENDINGS[N] said
    when someone answered, and that answer cannot be re-asked. Reordering this list, or
    reusing a key for something else, silently rewrites history.
    """
    assert [e.key for e in progress.ENDINGS] == [
        "abyss1_executed", "abyss1_cleared_lost_someone", "abyss1_cleared_all_survived",
        "abyss1_reconciled",
        "abyss2_lost_them", "abyss2_saved_them", "abyss2_resolved", "abyss2_couple_lived",
        "abyss3_duke_died", "abyss3_duke_saved", "abyss3_accused",
        "abyss4_priest_won", "abyss4_villagers", "abyss4_cleared", "abyss4_freed_him",
    ]


def test_every_ending_names_a_chapter_a_player_can_see():
    """The question uses the dungeon's own name in the player's language rather than a
    chapter number, so every entry has to point at a real dungeon."""
    from wddrop_client.dungeons import DUNGEONS

    for ending in progress.ENDINGS:
        assert ending.dungeon_id in DUNGEONS, ending.key


def test_the_wording_carries_no_internal_names():
    """A player reads these. Script names, flag names and internal ids are ours."""
    for ending in progress.ENDINGS:
        text = ending.label.lower()
        for leak in ("main2", "sub2", "flag", "_00", "badend", "goodend", "endroll",
                     "dungeon level", "dungeon_level"):
            assert leak not in text, f"{ending.key} says {leak!r}"


def test_a_dungeon_that_does_not_scale_is_never_asked_about():
    """Interrupting someone for a covariate that cannot affect their dungeon is a question
    with no answer worth having."""
    assert progress.should_ask(Cfg(), scales=False) is False


def test_a_dungeon_nobody_measured_is_asked_about():
    """`None` is "we never extracted that one", not "it does not scale". An unnecessary
    question costs a dismissal; a missing one costs the covariate for every session after."""
    assert progress.should_ask(Cfg(), scales=None) is True


def test_dismissing_costs_the_same_as_answering():
    """A prompt that comes back next session because it was closed is how people learn to
    click prompts away without reading — and a reflexive answer is worse than no answer.
    """
    cfg = Cfg()
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    progress.mark_asked(cfg, now)                       # dismissed, not answered
    assert cfg.saved == 1
    assert progress.should_ask(cfg, scales=True, now=now + timedelta(days=13)) is False
    assert progress.should_ask(cfg, scales=True, now=now + timedelta(days=14)) is True


def test_never_means_never():
    assert progress.should_ask(Cfg(interval=0), scales=True) is False


def test_an_answer_is_not_asked_about_again():
    """Including an all-zero one: "I have not finished anything" is an answer."""
    cfg = Cfg(bits="0" * progress.WIDTH, width=progress.WIDTH,
              asked_at="2020-01-01T00:00:00+00:00")
    assert progress.answered(cfg) is True
    assert progress.should_ask(cfg, scales=True) is False


def test_a_build_that_knows_more_endings_asks_again():
    """An answer given when there were five endings does not cover the sixth. The interval
    still applies, so this is one question, not a nag."""
    cfg = Cfg(bits="11111", width=5, asked_at="2020-01-01T00:00:00+00:00")
    assert progress.should_ask(cfg, scales=True) is True


def test_an_unreadable_stamp_asks_rather_than_never_asking():
    cfg = Cfg(asked_at="last tuesday")
    assert progress.should_ask(cfg, scales=True) is True


# -- the window ---------------------------------------------------------------------------

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6 import QtWidgets  # noqa: E402

from test_ui import _catalogue, make_config  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config directory, so a test can never reach the player's own."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def window(app, home):
    """A window that is CLOSED afterwards.

    It owns worker threads — the update check among them — and one left open outlives the
    test, so the interpreter tears down a running QThread and aborts. The suite still prints
    every test as passed and then exits 134, which reads as a crash nobody caused.
    """
    from wddrop_client.ui import MainWindow

    made = []

    def build(cfg=None):
        win = MainWindow(cfg or make_config(accepted=True), data=home)
        made.append(win)
        win.show()
        return win

    yield build
    for win in made:
        win.close()
        win.deleteLater()
    app.processEvents()


def test_the_question_is_put_when_a_scaling_dungeon_is_picked(app, window, tmp_path):
    """Not at first run. The first thing a player does is get the window working, and a
    questionnaire before their first recorded chest is where people give up — so it waits
    until they choose a dungeon whose drops the answer actually bears on.
    """
    from wddrop_client.ui import ProgressDialog

    window = window()
    window._load_catalog(_catalogue(tmp_path))
    window._catalog = [dict(d, scales=True) for d in window._catalog]

    window.dungeon.setCurrentIndex(window.dungeon.findText("北穿幽靈城"))
    app.processEvents()
    assert isinstance(window._progress_dialog, ProgressDialog), "the question was not put"
    window._progress_dialog.reject()
    app.processEvents()


def test_the_prompt_does_not_block_the_window(app, home, tmp_path):
    """It is raised from inside the picker's signal handler. `exec` there starts a nested
    event loop — the window stops answering anything else until a person deals with the
    dialog, and nothing driving the window without one gets past this line.
    """
    import inspect

    from wddrop_client import ui

    source = inspect.getsource(ui.MainWindow._maybe_ask_progress)
    assert ".open()" in source and ".exec()" not in source


def test_a_dungeon_that_does_not_scale_asks_nothing(app, window, tmp_path):
    window = window()
    window._load_catalog(_catalogue(tmp_path))
    window._catalog = [dict(d, scales=False) for d in window._catalog]

    window.dungeon.setCurrentIndex(window.dungeon.findText("北穿幽靈城"))
    app.processEvents()
    assert getattr(window, "_progress_dialog", None) is None


def test_dismissing_the_prompt_is_remembered(app, window, tmp_path):
    cfg = make_config(accepted=True)
    window = window(cfg)
    window._load_catalog(_catalogue(tmp_path))
    window._catalog = [dict(d, scales=True) for d in window._catalog]

    window.dungeon.setCurrentIndex(window.dungeon.findText("北穿幽靈城"))
    app.processEvents()
    window._progress_dialog.reject()
    app.processEvents()
    assert cfg.progress_asked_at, "a dismissal that is not recorded comes back next session"
    assert not cfg.progress_width, "nothing was answered, so nothing should be stored"


def test_an_answer_is_stored_as_bits(app, window, tmp_path):
    from wddrop_client.progress import ENDINGS, WIDTH, decode

    cfg = make_config(accepted=True)
    window = window(cfg)
    window._load_catalog(_catalogue(tmp_path))
    window._catalog = [dict(d, scales=True) for d in window._catalog]

    window.dungeon.setCurrentIndex(window.dungeon.findText("北穿幽靈城"))
    app.processEvents()
    dialog = window._progress_dialog
    dialog.boxes[ENDINGS[0].key].setChecked(True)
    dialog.accept()
    app.processEvents()

    assert cfg.progress_width == WIDTH
    assert decode(cfg.progress_bits, cfg.progress_width)[ENDINGS[0].key] is True
    assert decode(cfg.progress_bits, cfg.progress_width)[ENDINGS[1].key] is False


def test_the_dialog_never_shows_an_internal_name(app, home):
    """A player reads every word of this. Script names, flags and chapter numbers are ours."""
    from wddrop_client.i18n import Translator
    from wddrop_client.ui import ProgressDialog

    dialog = ProgressDialog(Translator("zh_tw"), make_config(accepted=True))
    said = " ".join(w.text() for w in dialog.findChildren(QtWidgets.QLabel) if w.text())
    said += " ".join(b.text() for b in dialog.findChildren(QtWidgets.QCheckBox))
    for leak in ("main2", "sub2", "BADEND", "GOODEND", "dungeon_level", "sc=", "fid"):
        assert leak not in said, f"the dialog says {leak!r}"
    assert said.strip(), "the dialog said nothing at all"


# -- the grade ladder, which is a different kind of thing -----------------------------------

def test_the_grade_is_an_ordinal_not_a_bit():
    """The story is a SET of things that either happened or did not; the grade is a ladder
    the main character climbs one rung at a time, and it caps the whole party's level — 40 at
    bronze, 70 at copper. Packed into the same bitfield it would be a set that can only ever
    hold one member, and every future rung would cost a bit.
    """
    assert all(isinstance(g.id, int) and g.max_level > 0 for g in progress.GRADES)
    ladder = [g.id for g in progress.GRADES]
    assert ladder == sorted(ladder) and len(set(ladder)) == len(ladder)
    caps = [g.max_level for g in progress.GRADES]
    assert caps == sorted(caps), "the ladder does not rise"


def test_only_the_rungs_the_game_has_opened_are_offered():
    """The table carries all twelve because the game's own does, up to a rung whose name has
    only ever appeared in a data file. Offering one invites an answer that cannot be true."""
    offered = progress.released_grades()
    assert offered[-1].id == progress.HIGHEST_RELEASED_GRADE
    assert len(offered) < len(progress.GRADES), "unreleased rungs are being offered"
    assert progress.grade_name(progress.HIGHEST_RELEASED_GRADE, "zh_tw") == "銅階"


def test_the_grade_names_are_the_games_own_words():
    """Not ours to translate: the player picks the word they saw in their own client."""
    for locale, expected in (("ja", "青銅等級"), ("zh_tw", "青銅階"), ("en", "Bronze Grade")):
        assert progress.grade_name(3, locale) == expected


def test_the_bottom_rung_is_where_everyone_starts():
    """There is no "not sure" to offer. A player who has passed no promotion exam IS the
    bottom grade — it is the game's own starting state, with its own level cap — so an
    unanswered player and a player who has passed nothing are the same person. Adding an
    unknown option would ask someone to tell two names for the same place apart.

    Unanswered is still None in the config; what changes is that saving always produces a
    real grade rather than a hole.
    """
    assert progress.GRADE_FLOOR == progress.GRADES[0].id
    cfg = Cfg()
    progress.remember(cfg, {e.key: False for e in progress.ENDINGS},
                      grade=progress.GRADE_FLOOR)
    assert cfg.character_grade == progress.GRADE_FLOOR


def test_the_reference_records_the_ladder_and_what_is_open():
    import json

    reference = json.loads((ROOT / "data" / "progress_conditions.json")
                           .read_text(encoding="utf-8"))
    grades = reference["grades"]
    assert [g["id"] for g in grades] == [g.id for g in progress.GRADES]
    open_now = [g["id"] for g in grades if g["released"]]
    assert max(open_now) == progress.HIGHEST_RELEASED_GRADE
    assert any(not g["released"] for g in grades), "the unreleased rungs are worth recording"


def test_the_two_kinds_of_progress_are_separate_categories(app, home):
    """A rung climbed and a set of things seen are not the same question, and mixing them
    into one list makes the shorter one look like an afterthought. The grade goes first
    because every player can answer it without thinking back.
    """
    from wddrop_client.i18n import Translator
    from wddrop_client.ui import ProgressDialog

    dialog = ProgressDialog(Translator("zh_tw"), make_config(accepted=True))
    labels = [w.text() for w in dialog.findChildren(QtWidgets.QLabel) if w.text()]
    assert "主角的等級" in labels and "主線劇情" in labels
    assert labels.index("主角的等級") < labels.index("主線劇情"), "the grade must come first"
    # And the grade is a picker, not a checkbox: it is one rung, not a set.
    grades = __import__("wddrop_client.progress",
                        fromlist=["released_grades"]).released_grades()
    assert dialog.grade.count() == len(grades), "an extra option crept in beside the rungs"
    assert dialog.grade.currentData() == progress.GRADE_FLOOR, "it should open at the bottom"


# -- what leaves the machine ---------------------------------------------------------------

def _cfg():
    cfg = Cfg()
    cfg.install_id, cfg.locale = "00000000-0000-0000-0000-000000000000", "ja"
    return cfg


def _raw():
    return {"event_id": "00000000-0000-0000-0000-0000000000e1",
            "occurred_at": "2026-08-16T00:00:00+00:00",
            "provenance": "chest_direct", "contents": []}


def test_an_unanswered_profile_sends_nothing_rather_than_zero():
    """Zero is a real answer — "I have finished none of it" — and it is not the same as
    nobody having been asked. A client that sent 0 for an unanswered player would fill the
    study with beginners who never existed.
    """
    from wddrop_client import uploader

    from wddrop_schema.models import CaptureMode

    event = uploader.hydrate(_raw(), _cfg(), CaptureMode.OCR)
    assert event.capture.progress is None
    assert event.capture.character_grade is None


def test_an_answered_profile_sends_the_flags_and_the_grade():
    from wddrop_client import uploader

    from wddrop_schema.models import CaptureMode

    cfg = _cfg()
    answer = {e.key: False for e in progress.ENDINGS}
    answer[progress.ENDINGS[0].key] = True
    progress.remember(cfg, answer, grade=6)
    event = uploader.hydrate(_raw(), cfg, CaptureMode.OCR)
    assert event.capture.progress == 1
    assert event.capture.character_grade == 6


def test_the_covariates_ride_on_the_event_not_on_the_player():
    """A player-level record holds only the LATEST answer, so every row collected before
    someone finished a chapter would be re-attributed to progress they did not have when it
    was recorded. The covariate has to be observed with the reading.
    """
    from wddrop_schema.models import CaptureInfo, DropEvent

    assert "progress" in CaptureInfo.model_fields
    assert "character_grade" in CaptureInfo.model_fields
    assert "progress" not in DropEvent.model_fields, "it belongs to the reading, not the row"


def test_the_disclaimer_says_both_of_them_are_collected():
    """The text lists what is sent, item by item. Sending something it does not name is the
    mirror of the mistake this file already had to correct — three things it claimed to
    collect and the client never sent.
    """
    text = (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
    assert "劇情進度" in text and "主角等級" in text
    assert "story progress and character grade" in text

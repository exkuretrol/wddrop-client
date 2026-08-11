"""
The window, driven headless.

What is worth testing here is not the layout, it is the RULES the window enforces and the
thread boundary it depends on:

  * collection cannot start before consent, and the accepted text is the canonical one;
  * capture cannot start without a calibration;
  * chests reach the GUI thread through Qt's queue, never by the worker touching a widget;
  * Stop actually stops, and the reason is recorded.

Run offscreen, so this needs no display. The capture test drives a real recorded session
through the real runner, so it exercises the same path a live capture takes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

QtCore = pytest.importorskip("PySide6.QtCore", reason="PySide6 not installed")
from PySide6 import QtWidgets  # noqa: E402

from wddrop_client.config import ClientConfig  # noqa: E402
from wddrop_client.consent import ConsentState, disclaimer_hash  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config directory, so a test can never touch the player's real spool."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    return tmp_path


def make_config(accepted: bool) -> ClientConfig:
    state = ConsentState(accepted_hash=disclaimer_hash() if accepted else None)
    return ClientConfig(consent=state)


def test_consent_is_a_gate_not_a_checkbox(app, home):
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=False), data=home)
    assert window.stack.currentIndex() == 0, "an unconsented client must open on the gate"
    assert not window.consent_page.button.isEnabled()

    window.consent_page.agree.setChecked(True)
    assert window.consent_page.button.isEnabled()
    window.consent_page.button.click()
    assert window.stack.currentIndex() == 1


def test_accepting_records_the_hash_of_the_text_that_was_shown(app, home):
    """Storing a hash of some other copy of the disclaimer would break the one guarantee
    consent has: that editing the terms re-prompts instead of inheriting agreement."""
    from wddrop_client.consent import disclaimer_text
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=False), data=home)
    assert window.consent_page._disclaimer() == disclaimer_text()
    window.consent_page.agree.setChecked(True)
    window.consent_page.button.click()
    assert window.cfg.consent.general_ok


def test_capture_cannot_start_without_a_calibration(app, home, monkeypatch):
    """Every region in a profile is absolute pixels, so there is nothing sensible to do
    without one — and starting anyway would read the wrong strip in silence.

    "Without one" means neither the player's nor a shipped one, so the shipped store is
    emptied here; the test below is the other half.
    """
    from wddrop_client.calibration import ProfileStore
    from wddrop_client.ui import MainWindow

    monkeypatch.setattr(ProfileStore, "shipped", classmethod(lambda cls: ProfileStore()))
    window = MainWindow(make_config(accepted=True), data=home)
    assert not window.start.isEnabled()
    assert "not calibrated" in window.cal_label.text()


def test_a_shipped_calibration_is_enough_to_start(app, home):
    """A fresh install at a verified resolution must be ready to record.

    It was not: the window counted only the player's own store, so it asked them to
    calibrate a size the client already had a tested fit for and refused to start until they
    did. The fits were inside the exe the whole time and only the command line consulted
    them — which is what "it doesn't ship the default profiles" looked like from outside.
    """
    from wddrop_client.calibration import ProfileStore
    from wddrop_client.ui import MainWindow

    if not ProfileStore.shipped().keys():
        pytest.skip("profiles.shipped.json not built")

    window = MainWindow(make_config(accepted=True), data=home)
    assert "not calibrated" not in window.cal_label.text()
    assert "704x1241" in window.cal_label.text()
    # Ready still needs the data files; on a machine that has them, so is Start.
    if "missing" not in window.data_label.text():
        assert window._ready, "a shipped calibration did not make the window ready"


CAPTURE = paths.capture("session-20260809-034520") or Path("/nonexistent")
# State moved out of the program folder, so the profile is wherever it lives NOW.
# Both are tried: a machine that has not run the migration still has the old one.
# The store, not the single profile: `profile.json` is whichever resolution was calibrated
# LAST, and this recording is 704x1241. Picking by frame size is what ProfileStore exists
# for, and it keeps the test from breaking every time someone calibrates at another size.
# The SHIPPED store last: a player who clears their own state — which is exactly what
# testing a fresh install looks like — should not silently turn this test off.
PROFILES = next((p for p in (paths.PROFILES, ROOT / "profiles.shipped.json")
                 if p and p.exists()), None)


def _profile_for(frame_size, destination) -> bool:
    """Write the profile matching this recording's resolution. False if there is none."""
    import json

    if PROFILES is None:
        return False
    store = json.loads(PROFILES.read_text(encoding="utf-8"))
    entry = (store.get("profiles") or store).get(f"{frame_size[0]}x{frame_size[1]}")
    if not entry:
        return False
    destination.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    return True
ATLAS = ROOT / "data" / "atlas.zh_tw.json"
VOCAB = ROOT / "data" / "vocab.zh_tw.json"


@pytest.mark.skipif(not (CAPTURE.is_dir() and ATLAS.exists() and VOCAB.exists() and PROFILES),
                    reason="needs a recorded session, a profile and the built data files")
def test_a_recorded_session_reaches_the_window_and_stop_works(app, home, monkeypatch):
    """End to end on the real path: worker thread -> Qt queue -> table rows, then Stop.

    Chests must arrive on the GUI thread. If the worker were touching widgets directly this
    would not fail loudly — it would corrupt state at random, hours into someone's session —
    so the check is that the receiving slot runs on the main thread.
    """
    from wddrop_client.ui import MainWindow

    if not _profile_for((704, 1241), home / "profile.json"):
        pytest.skip("no calibration for this recording's resolution")
    window = MainWindow(make_config(accepted=True), data=home)
    window.locale.setCurrentText("zh_tw")

    threads = []
    original = MainWindow._chest

    def spy(self, event):
        threads.append(QtCore.QThread.currentThread())
        original(self, event)

    monkeypatch.setattr(MainWindow, "_chest", spy)

    args = window.args_for(dungeon=7015, floor=None, source=str(CAPTURE), fps=16.0)
    from wddrop_client.ui import CaptureWorker

    window.worker = CaptureWorker(window.cfg, args, window)
    window.worker.chest.connect(window._chest)
    finished = []
    window.worker.done.connect(finished.append)
    window.worker.failed.connect(lambda m: finished.append({"failed": m}))
    # Stop as soon as three chests have been seen, which is what the Stop button does.
    window.worker.chest.connect(
        lambda _: window.worker.stop("user_stop") if window.table.rowCount() >= 3 else None)
    window.worker.start()

    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while not finished and deadline.elapsed() < 240_000:
        app.processEvents()
        window.worker.wait(50)
    window.worker.wait(10_000)
    app.processEvents()

    assert finished, "the capture never finished"
    assert "failed" not in finished[0], finished[0]
    assert window.table.rowCount() >= 3
    assert finished[0]["stop_reason"] == "user_stop"
    assert threads and all(t is app.thread() for t in threads), \
        "chests must be delivered on the GUI thread"
    # The first chest of this recording, read from the frames (tests/truth/). Column 1 says
    # WHERE it came from — chests and mining share the table but are different observations.
    assert window.table.item(0, 1).text() == "chest"
    assert "莫尼翁銀幣" in window.table.item(0, 2).text()


def test_start_refuses_until_a_dungeon_is_actually_chosen(app, home, monkeypatch, tmp_path):
    """The dropdown used to open on the first catalogue entry, so "never touched it" and
    "chose 初始的奈落" produced identical data. Five real chests were mislabelled that way."""
    import json

    from wddrop_client import ui as ui_mod
    from wddrop_client.ui import MainWindow

    catalog = tmp_path / "catalog.zh_tw.json"
    catalog.write_text(json.dumps({"locale": "zh_tw", "dungeons": [
        {"id": 2000, "name": "初始的奈落", "floors": []},
        {"id": 7015, "name": "北穿幽靈城", "floors": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    for name in ("vocab.zh_tw.json", "atlas.zh_tw.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ui_mod, "find_data",
                        lambda pattern, locale: tmp_path / pattern.format(locale=locale))

    window = MainWindow(make_config(accepted=True), data=home)
    assert window.dungeon.currentData() is None, "must open on a placeholder, not a dungeon"
    assert not window.start.isEnabled()

    window.dungeon.setCurrentIndex(1)
    assert window.dungeon.currentData() == 2000


def test_the_interface_language_follows_the_system_but_capture_does_not(app, home, monkeypatch):
    """Two different things: the WINDOW's language and the GAME's. A player whose Windows is
    English but whose client is Traditional Chinese must not have their capture silently
    switched to the English vocabulary — that recognises nothing."""
    from wddrop_client.i18n import match_locale
    from wddrop_client.ui import MainWindow

    # The encoding suffix used to break the region match and serve zh_cn to a zh_tw system.
    assert match_locale("zh_TW.UTF-8") == "zh_tw"
    assert match_locale("Chinese (Traditional)_Taiwan.950") == "zh_tw"
    assert match_locale("fr_FR.UTF-8") == "en"

    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    cfg = make_config(accepted=True)
    cfg.locale = "zh_tw"                      # the game stays Traditional Chinese
    window = MainWindow(cfg, data=home)
    assert window.t.locale == "ja"            # ...while the window speaks Japanese
    assert window.args_for().locale == "zh_tw"


def test_sharing_is_asked_at_first_run_and_is_off_unless_answered(app, home):
    """Buried in Settings it is a default nobody chose; asked on the consent page it is an
    answer. Recording works either way — sharing only decides whether it is also sent."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=False), data=home)
    assert window.stack.currentIndex() == 0
    assert window.consent_page.share.isChecked() is False
    window.consent_page.agree.setChecked(True)
    window.consent_page.share.setChecked(True)
    window.consent_page.button.click()
    assert window.cfg.share_uploads is True
    assert window.cfg.asked_sharing is True


def test_a_dive_marker_never_leaves_the_computer(app, home):
    """It is the player's note about their own session. The server is not told."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    window._mark_dive()
    assert len(window.markers) == 1
    assert window.markers[0]["provenance"] == "marker"
    # It shows in the log, so the player can see where they marked.
    assert window.table.rowCount() == 1


def test_the_break_rate_is_counted_not_assumed(app, home):
    """The swings before a break ARE that pickaxe's lifetime. Until the first one breaks
    there is no rate, and saying so beats showing a number nobody measured."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    window.cfg.pickaxes = 10
    window._refresh_pickaxes()
    assert "not enough data yet" in window.pickaxe_label.text()

    for _ in range(6):
        window._mined(0)
    window._pickaxe_broke()
    assert window._pickaxe_lives == [6]
    assert "6" in window.pickaxe_label.text()


def test_the_wheel_does_not_edit_a_control_it_only_scrolls_past(app, home):
    """Scrolling Settings rewrote whichever control passed under the cursor. On this form
    that means the game language or the sample rate — both decide whether a session records
    anything at all, and neither announces that it changed."""
    from PySide6 import QtCore, QtGui

    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    window.show()
    window._show_page(3)                     # Settings, where the scrolling happens
    app.processEvents()
    combo = window.locale
    before = combo.currentIndex()

    def wheel():
        return QtGui.QWheelEvent(
            QtCore.QPointF(5, 5), QtCore.QPointF(5, 5), QtCore.QPoint(0, -120),
            QtCore.QPoint(0, -120), QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
            QtCore.Qt.NoScrollPhase, False)

    assert not combo.hasFocus()
    app.sendEvent(combo, wheel())
    assert combo.currentIndex() == before, "a scroll-past must not change the game language"

    # Focused, it is a control again — the wheel is how a focused combobox is meant to work.
    combo.setFocus(QtCore.Qt.MouseFocusReason)
    assert combo.hasFocus()
    app.sendEvent(combo, wheel())
    assert combo.currentIndex() != before


def test_the_pickaxe_line_says_what_it_is_counting(app, home):
    """A figure captioned "pickaxes left" that sat still through a mining run read as broken
    detection. The count moves on the BUTTON — one vein is many swings for one pickaxe, so
    spending one per swing would spend pickaxes the player still has — and the swing count
    beside it is what shows the client is watching."""
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=True)
    cfg.pickaxes = 3
    window = MainWindow(cfg, data=home)
    window._refresh_pickaxes()
    assert "3" in window.pickaxe_label.text()

    window._mined(-1)
    window._mined(-1)
    text = window.pickaxe_label.text()
    assert "3" in text, "swinging does not spend a pickaxe"
    assert "2" in text, "but the swing count has to move"

    window._pickaxe_broke()
    after = window.pickaxe_label.text()
    assert "2" in after, "the button is what spends one"
    # And the lifetime is now measured rather than assumed.
    assert window._pickaxe_lives == [2]
    assert window._swings_since_break == 0


def test_the_floor_is_hidden_and_therefore_null(app, home):
    """A stale floor label is worse than an honest null: it files chests under a floor they
    did not come from, in the field the analysis strata are built on."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    assert not window.floor.isVisible()
    assert window.args_for(dungeon=7015, floor=window.floor.currentData()).floor is None


def test_the_calibration_dialog_speaks_the_windows_language(app, home):
    """It is the first thing a new player is sent to and cannot be skipped, so leaving it in
    English made the page explaining what to photograph the page they could not read."""
    from wddrop_client.i18n import Translator
    from wddrop_client.ui import CalibrateDialog, MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    dialog = CalibrateDialog(window.args_for(), window, t=Translator("ja"))
    assert dialog.action.text() == "撮影"
    assert dialog.skip.text() == "この撮影を飛ばす"
    assert "ステップ 1" in dialog.step.text()


def test_the_stats_page_reads_the_players_copy(app, home):
    """Not the outbox, and not the server: uploading changes what has been sent, never what
    was seen."""
    import json

    from wddrop_client.config import records_path
    from wddrop_client.ui import MainWindow

    records_path().write_text(json.dumps({
        "event_id": "e1", "occurred_at": "2026-08-10T12:00:00+00:00",
        "provenance": "chest_direct", "dive": {"dungeon_id": 7015},
        "contents": [{"item_name": "莫尼翁銀幣", "quantity": 2}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    window = MainWindow(make_config(accepted=True), data=home)
    window._show_page(1)
    # The item, and the TOTAL row under it — a column of shares does not read without the
    # number they are shares of.
    assert window.stats_table.rowCount() == 2
    assert window.stats_table.item(0, 0).text() == "莫尼翁銀幣"
    assert window.stats_table.item(0, 1).text() == "×2"
    assert window.stats_table.item(0, 2).text() == "100.0%"
    assert "1" in window.stats_headline.text()


def _catalogue(tmp_path):
    import json

    path = tmp_path / "catalog.zh_tw.json"
    path.write_text(json.dumps({"locale": "zh_tw", "dungeons": [
        {"id": 2000, "name": "初始的奈落", "floors": [{"id": 200001, "name": "B1F"}]},
        {"id": 7015, "name": "北穿幽靈城", "floors": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    return path


def test_no_dungeon_shows_its_id(app, home, tmp_path):
    """The id is what the data is filed under, not something the player picking a dungeon
    has any use for."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    window._load_catalog(_catalogue(tmp_path))

    labels = [window.dungeon.itemText(i) for i in range(window.dungeon.count())]
    assert "北穿幽靈城" in labels
    assert not any("7015" in text or "2000" in text for text in labels), labels
    # The id is still what gets recorded.
    assert window.dungeon.itemData(window.dungeon.findText("北穿幽靈城")) == 7015


def test_the_pickaxe_controls_appear_only_where_there_is_something_to_mine(app, home, tmp_path):
    """Veins exist in one dungeon; everywhere else the panel reader is off entirely. A
    pickaxe count elsewhere is a control that cannot do anything, beside a button that would
    record a break that could not have happened."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    window.show()
    window._load_catalog(_catalogue(tmp_path))

    window.dungeon.setCurrentIndex(window.dungeon.findText("初始的奈落"))
    app.processEvents()
    assert not window.pickaxes.isVisible() and not window.broke.isVisible()

    window.dungeon.setCurrentIndex(window.dungeon.findText("北穿幽靈城"))
    app.processEvents()
    assert window.pickaxes.isVisible() and window.broke.isVisible()
    assert window.pickaxe_caption.isVisible()


def test_the_pickaxe_count_is_on_the_page_it_is_used_on(app, home):
    """It changes every time the player restocks, which is far too often for a settings
    page you have to go and find."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    record, settings = window.pages.widget(0), window.pages.widget(3)
    assert window.pickaxes in record.findChildren(QtWidgets.QSpinBox)
    assert window.pickaxes not in settings.findChildren(QtWidgets.QSpinBox)
    # It writes through to the config the moment it changes, from wherever it lives.
    window.pickaxes.setValue(7)
    assert window.cfg.pickaxes == 7


def test_the_server_endpoint_cannot_be_edited_in_the_window(app, home):
    """A wrong value sends a player's records nowhere, or somewhere nobody intended — and
    "change your server address to..." is exactly the instruction someone else would give."""
    from PySide6 import QtWidgets

    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    assert not isinstance(window.server, QtWidgets.QLineEdit)
    assert window.server.text() == window.cfg.server_url
    assert not hasattr(window, "_server_changed")


# A frame from the RECORDING archive, not the player's calibration shot. drop.png is
# replaced every time they re-calibrate, so a test whose expected answer is "whatever they
# last photographed" fails the moment they do — which happened twice. Recordings are
# append-only, so this frame and the item on it stay what they were.
DROP_SHOT = next((p for p in (
    (paths.capture("session-20260809-034520") or Path("/nonexistent"))
    / "episode-002" / "f_00036.png",
) if p.exists()), Path("/nonexistent"))
DROP_SHOT_NAME = "莫尼翁銀幣"


@pytest.mark.skipif(not (DROP_SHOT.exists() and ATLAS.exists() and VOCAB.exists()),
                    reason="needs a recorded frame and the built data files")
def test_calibration_proposes_the_item_name_instead_of_asking_for_it(app, home):
    """Typing an item name exactly, in a language your keyboard may not have, is the worst
    step in setup. It can be read instead — but only as a PROPOSAL: the fit refuses to save a
    profile that cannot read back the name it was given, and that check is worthless if the
    name came from the same image. So this fills the box; the player still agrees.

    Known limit, and the reason this is only a proposal: the band is chosen by fitting the
    PREFIX alone, and on a shot whose HUD carries other text that comparison has been seen to
    pick the wrong band and read nonsense — correctly refused, but nothing offered either.
    """
    from PIL import Image

    from wddrop_client.__main__ import _font_candidates, _load_vocab, _prefix_from
    from wddrop_client.calibration import propose_item_name
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    args = window.args_for()
    vocab, fmt, _ = _load_vocab(args)

    guess = propose_item_name(Image.open(DROP_SHOT), _prefix_from(fmt),
                              _font_candidates(args), [e.name for e in vocab.entries])
    assert guess is not None, "nothing was read"
    name, _score, margin = guess
    assert name == DROP_SHOT_NAME, name
    # Separation, never absolute score — blur raises the score while destroying the answer.
    assert margin > 0.05, margin


def _sharing_config(mode):
    from wddrop_client.config import ClientConfig

    cfg = make_config(accepted=True)
    cfg.share_uploads = True
    cfg.send_mode = mode
    assert isinstance(cfg, ClientConfig)
    return cfg


def test_a_batch_waits_and_then_goes_in_one_request(app, home, monkeypatch):
    """27 events was 27 requests as-it-happens and is 3 like this. Waiting risks nothing:
    the record is on disk before any network attempt either way, so a batch defers only the
    SEND."""
    import json

    from wddrop_client.config import SEND_BATCH, spool_path
    from wddrop_client.ui import MainWindow

    window = MainWindow(_sharing_config(SEND_BATCH), data=home)
    window.cfg.send_batch_size = 5
    sent = []
    monkeypatch.setattr(MainWindow, "_upload", lambda self, quiet=False: sent.append(1))

    for i in range(4):
        with spool_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_id": f"e{i}", "contents": []}) + "\n")
        window._chest({"event_id": f"e{i}", "contents": [], "dive": {}})
    assert sent == [], "nothing should go before the batch is full"

    with spool_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_id": "e4", "contents": []}) + "\n")
    window._chest({"event_id": "e4", "contents": [], "dive": {}})
    assert len(sent) == 1, "the fifth fills the batch and it goes"


def test_a_part_filled_batch_still_goes_when_the_session_ends(app, home, monkeypatch):
    """Otherwise a player who stops at nine records has them sit there — and the dive's
    stop_reason, which is queued behind them, never arrives either."""
    from wddrop_client.config import SEND_BATCH
    from wddrop_client.ui import MainWindow

    window = MainWindow(_sharing_config(SEND_BATCH), data=home)
    sent = []
    monkeypatch.setattr(MainWindow, "_upload", lambda self, quiet=False: sent.append(1))
    window._finished({"stop_reason": "user_stop", "dive_id": "d"})
    assert len(sent) == 1


def test_manual_mode_sends_nothing_by_itself(app, home, monkeypatch):
    """"Send when I press Upload" is an instruction, and the end of a session is not a press."""
    from wddrop_client.config import SEND_MANUAL
    from wddrop_client.ui import MainWindow

    window = MainWindow(_sharing_config(SEND_MANUAL), data=home)
    sent = []
    monkeypatch.setattr(MainWindow, "_upload", lambda self, quiet=False: sent.append(1))
    window._chest({"event_id": "e", "contents": [], "dive": {}})
    window._finished({"stop_reason": "user_stop", "dive_id": "d"})
    assert sent == []


def test_the_worker_builds_for_the_window_it_captures_not_the_last_calibration(
        app, home, monkeypatch):
    """The window is where this was reported: two resolutions calibrated, the game at
    1920x1080, and the client built its recogniser from the 704x1241 fit — then reported the
    mismatch as though nothing were calibrated for the size. The size the capture will
    produce has to reach `_build_runner`, or the whole ProfileStore is decoration."""
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli
    from wddrop_client.calibration import Profile, ProfileStore
    from wddrop_client.ui import CaptureWorker

    store = ProfileStore()
    for size, font_size in (((704, 1241), 25), ((1920, 1080), 22)):
        store.put(Profile(frame_size=size, message_band=(0, 1), font_path="x",
                          font_size=font_size, offset=(0, 0), calibration_score=0.9))
    store.save(home)
    store.get((704, 1241)).save(home / "profile.json")          # calibrated last

    monkeypatch.setattr(cli, "_live_size", lambda args: (1920, 1080))
    opened = {}

    def fake_source(spec, **kw):
        opened["expect_size"] = kw.get("expect_size")
        return object()

    monkeypatch.setattr("wddrop_client.capture.source.open_source", fake_source)
    seen = {}

    def spy(cfg, args, size=None):
        seen["size"] = size
        raise RuntimeError("stop here")

    monkeypatch.setattr(cli, "_build_runner", spy)

    worker = CaptureWorker(make_config(accepted=True),
                           SimpleNamespace(source="window", fps=8, record=None,
                                           data=str(home), dungeon=7015, floor=None))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()                                    # on this thread; it is a plain method

    assert seen.get("size") == (1920, 1080), "the capture size never reached the builder"
    # The source is told the same size, so window matching scores on the right one too.
    assert opened.get("expect_size") == (1920, 1080)
    assert failures and "stop here" in failures[0]


def test_the_stats_page_offers_the_days_recorded_and_keeps_the_overall(app, home):
    """A day on its own says nothing about whether it was a good one, so the all-time line
    stays on screen whichever day is chosen."""
    import json

    from wddrop_client.config import records_path
    from wddrop_client.ui import MainWindow

    rows = [
        {"event_id": "a", "provenance": "chest_direct", "occurred_at": "2026-08-10T01:00:00+00:00",
         "dive": {"dungeon_id": 7015}, "contents": [{"item_name": "X", "quantity": 2}]},
        {"event_id": "b", "provenance": "mining", "occurred_at": "2026-08-11T01:00:00+00:00",
         "dive": {"dungeon_id": 7015}, "contents": [{"item_name": "Y", "quantity": 1}]},
    ]
    records_path().parent.mkdir(parents=True, exist_ok=True)
    records_path().write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    window = MainWindow(make_config(accepted=True), data=home)
    window._refresh_stats_page()

    labels = [window.stats_day.itemText(i) for i in range(window.stats_day.count())]
    assert window.stats_day.itemData(0) is None, "the first entry must be every day"
    assert any("2026-08-11" in label for label in labels)
    assert "2" in window.stats_overall.text()

    # Pick the older day: the table narrows, the all-time line does not.
    window.stats_day.setCurrentIndex(window.stats_day.findData("2026-08-10"))
    # One item, plus the TOTAL row the page ends with.
    assert window.stats_table.rowCount() == 2
    assert window.stats_table.item(0, 0).text() == "X"
    assert window.stats_table.item(1, 1).text() == "×2"
    assert "2" in window.stats_overall.text(), "the all-time total followed the filter"


def test_the_day_picker_survives_a_refresh_without_losing_the_choice(app, home):
    """Rebuilt on every refresh, because a session running alongside adds days as it goes."""
    import json

    from wddrop_client.config import records_path
    from wddrop_client.ui import MainWindow

    records_path().parent.mkdir(parents=True, exist_ok=True)
    records_path().write_text(json.dumps(
        {"event_id": "a", "provenance": "chest_direct",
         "occurred_at": "2026-08-10T01:00:00+00:00", "dive": {"dungeon_id": 7015},
         "contents": [{"item_name": "X", "quantity": 1}]}) + "\n", encoding="utf-8")

    window = MainWindow(make_config(accepted=True), data=home)
    window._refresh_stats_page()
    window.stats_day.setCurrentIndex(window.stats_day.findData("2026-08-10"))
    window._refresh_stats_page()
    assert window.stats_day.currentData() == "2026-08-10"


def test_sharing_chosen_on_the_disclaimer_shows_up_in_settings(app, home):
    """Sharing is asked on the disclaimer and shown in Settings — two controls, one setting.

    Settings is built before the disclaimer is answered, so its checkbox held whatever the
    config said at construction: a player who agreed to share on the first page found it
    switched off in Settings, with only the config knowing which was true.
    """
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=False)
    cfg.share_uploads = False
    window = MainWindow(cfg, data=home)
    assert not window.share.isChecked()

    window.consent_page.agree.setChecked(True)
    window.consent_page.share.setChecked(True)
    window.consent_page._accept()

    assert window.cfg.share_uploads is True
    assert window.share.isChecked(), "Settings still shows the answer from before consent"
    assert window.cfg.asked_sharing is True


def test_declining_to_share_on_the_disclaimer_also_shows_up(app, home):
    """The same in the other direction: a config that already had sharing on, answered no."""
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=False)
    cfg.share_uploads = True
    window = MainWindow(cfg, data=home)
    assert window.share.isChecked()

    window.consent_page.agree.setChecked(True)
    window.consent_page.share.setChecked(False)
    window.consent_page._accept()

    assert window.cfg.share_uploads is False
    assert not window.share.isChecked()


def test_syncing_settings_does_not_write_the_value_back(app, home, monkeypatch):
    """The setters are what the handlers listen to; letting them fire would save on every
    refresh, and a handler that ever does more than save would loop."""
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    saves = []
    monkeypatch.setattr(type(window.cfg), "save", lambda self: saves.append(1))
    window._sync_settings_from_config()
    assert not saves


def test_a_break_takes_one_off_the_pickaxe_box(app, home):
    """The box is what the player reads. A count that only lived in a label beside it left
    two numbers disagreeing — the one they typed, and the one derived from it."""
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=True)
    cfg.pickaxes = 3
    window = MainWindow(cfg, data=home)
    assert window.pickaxes.value() == 3

    window._pickaxe_broke()

    assert window.cfg.pickaxes == 2
    assert window.pickaxes.value() == 2, "the box still shows the number before the break"
    assert "2" in window.pickaxe_label.text()


def test_breaks_never_count_below_nothing(app, home):
    """"not sure" is zero. Counting down from it would invent a stock never held."""
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=True)
    cfg.pickaxes = 1
    window = MainWindow(cfg, data=home)
    for _ in range(3):
        window._pickaxe_broke()
    assert window.cfg.pickaxes == 0 and window.pickaxes.value() == 0


def test_the_box_moving_on_a_break_is_not_treated_as_the_player_typing(app, home, monkeypatch):
    """`valueChanged` is how a restock is recorded. If a break fired it, every break would
    also look like the player editing the number."""
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=True)
    cfg.pickaxes = 5
    window = MainWindow(cfg, data=home)
    typed = []
    monkeypatch.setattr(window, "_pickaxes_changed", lambda value: typed.append(value))

    window._pickaxe_broke()

    assert window.pickaxes.value() == 4
    assert not typed, "the break was reported as a manual edit"


def test_terms_that_have_changed_are_asked_again_not_refused(app, home):
    """Acceptance is stored as a hash OF THE DISCLAIMER, so editing the terms re-prompts
    instead of inheriting agreement to something the player never read. That only works if
    the window asks the same question capture does.

    It did not: the window skipped the disclaimer whenever a hash was PRESENT, while capture
    required it to MATCH. Rewriting the disclaimer put a real player in the gap — never shown
    the new terms, refused on Start, and looking at a Settings page that said they agreed.
    """
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=True)
    cfg.consent.accepted_hash = "0000000000000000"      # agreed to some older wording
    window = MainWindow(cfg, data=home)

    assert window.stack.currentIndex() == 0, "the new terms were never shown"


def test_the_shell_opens_when_the_terms_are_the_ones_agreed_to(app, home):
    from wddrop_client.ui import MainWindow

    window = MainWindow(make_config(accepted=True), data=home)
    assert window.stack.currentIndex() == 1


def test_accepting_the_new_terms_gets_you_in(app, home):
    """The other half: re-prompting is only right if answering it works."""
    from wddrop_client.consent import disclaimer_hash
    from wddrop_client.ui import MainWindow

    cfg = make_config(accepted=True)
    cfg.consent.accepted_hash = "0000000000000000"
    window = MainWindow(cfg, data=home)

    window.consent_page.agree.setChecked(True)
    window.consent_page._accept()

    assert window.cfg.consent.accepted_hash == disclaimer_hash()
    assert window.cfg.consent.general_ok
    assert window.stack.currentIndex() == 1


def test_a_returning_player_is_told_the_terms_changed_not_greeted_as_new(app, home):
    """Coming back because the wording was edited looks identical to a first run otherwise,
    and "before anything is recorded" on someone's tenth session reads as "your data is
    gone". Their existing sharing answer is shown, because they are updating it, not
    choosing for the first time."""
    from wddrop_client.ui import ConsentPage

    cfg = make_config(accepted=True)
    cfg.consent.accepted_hash = "0000000000000000"
    cfg.share_uploads = True
    page = ConsentPage(cfg)

    labels = [w.text() for w in page.findChildren(QtWidgets.QLabel)]
    assert any("changed" in text or "更新" in text or "変更" in text for text in labels)
    assert any("already recorded" in text or "不受影響" in text for text in labels)
    assert page.share.isChecked(), "their existing answer was not carried in"


def test_a_first_run_is_not_told_anything_changed(app, home):
    from wddrop_client.ui import ConsentPage

    cfg = make_config(accepted=True)
    cfg.consent.accepted_hash = None
    # Held in a name: an unparented widget built inside the expression is collected before
    # its children are read, and Qt then raises about an object it has already deleted.
    page = ConsentPage(cfg)
    labels = [w.text() for w in page.findChildren(QtWidgets.QLabel)]
    assert not any("changed" in text for text in labels)

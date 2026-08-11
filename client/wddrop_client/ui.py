"""
The window. Everything the CLI does, for a player who should not have to open a terminal.

WHY THIS EXISTS AT ALL
----------------------
The binding constraint on this study is recruitment, not code: detecting a 25% relative
drop on a 5% baseline needs ~8,400 chests, which is ~280 player-hours. A tool that asks
people to install Python, edit a JSON file and read a traceback does not get 280 hours of
anyone's time. So the window is not decoration; it is the sample size.

THE THREADING RULE, AND WHY IT IS NOT NEGOTIABLE
-----------------------------------------------
Capture runs for hours and must not freeze the window, so it runs on a QThread. Qt widgets
may only be touched from the GUI thread — doing otherwise does not raise, it corrupts or
crashes at random later, which in a capture session means losing the data the player just
spent an hour collecting.

So the worker NEVER touches a widget. It emits signals; Qt queues them across the thread
boundary and delivers them on the GUI thread. That queue IS the thread-safe channel, and it
is why `runner.on_event` (which fires on the worker) does nothing but `emit`.

Stopping goes the other way: the Stop button calls `CaptureRunner.stop()`, which sets a
threading.Event the loop reads once per frame. It is deliberately cooperative — the loop
finishes the frame it is on and lets the episode machine close cleanly, because a chest
still open at that moment is emitted as truncated rather than lost.

WHAT THE WINDOW REFUSES TO DO
-----------------------------
  * Collect before consent. The gate is a page, not a checkbox on a form.
  * Start without a calibration for the CURRENT resolution. Every profile region is
    absolute pixels, so running on a mismatched one silently reads the wrong strip.
  * Hide a bad session. Frames, chests, HUD hits and the sample rate are on screen the
    whole time, because "it looks like it is working" is how a live run once spent 182
    frames recording nothing.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets

from datetime import datetime, timezone
from uuid import uuid4

from . import theme
from .config import (AUTOMATIC_MODES, CLIENT_VERSION, SEND_BATCH, SEND_EACH, SEND_MANUAL,
                     ClientConfig, config_dir,
                     data_dir, program_dir, records_path, spool_path)
from .i18n import LOCALES, NATIVE_NAMES, Translator, system_locale

log = logging.getLogger("wddrop.ui")

# Sampling. A message dismissed between two samples is never captured and no later fix can
# recover it, so the floor is a warning the player sees rather than a silent default.
DEFAULT_FPS = 20.0
MIN_RECOMMENDED_FPS = 16.0
# How often the live counters refresh. The stats dict is read from the worker thread while
# the loop mutates it; every value in it is a plain int written by one thread, so a torn
# read is not possible in CPython and a slightly stale count is harmless.
STATS_INTERVAL_MS = 500


def find_data(pattern: str, locale: str) -> Path | None:
    # The program's folder is searched explicitly. It used to be reached only because state
    # lived there too and `config_dir()` happened to point at it; now that the two are
    # separate, leaving it out would mean a client launched from a shortcut — with some
    # other working directory — could not find its own vocabulary.
    from .config import bundled_dir

    # `bundled_dir` last: a file the player put beside the exe beats the copy inside it,
    # which is the only way to replace a stale vocabulary without a new build.
    roots = (Path.cwd(), data_dir(), program_dir(), config_dir(), bundled_dir())
    for root in roots:
        if root is None:
            continue
        hit = root / pattern.format(locale=locale)
        if hit.exists():
            return hit
    return None


class CaptureWorker(QtCore.QThread):
    """Runs one capture session off the GUI thread.

    Every signal here crosses a thread boundary, so each carries plain data — dicts, strings
    — and never a widget, a Qt object owned elsewhere, or anything the worker will keep
    mutating.
    """

    chest = QtCore.Signal(dict)
    mining = QtCore.Signal(int)                # pickaxes left, or -1 when not counting
    pickaxe = QtCore.Signal(str, str, int)     # kind, pickaxe name, total broken
    warning = QtCore.Signal(str)
    ready = QtCore.Signal()          # the render index is built; capture has actually begun
    done = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, cfg: ClientConfig, args, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.args = args
        self.runner = None
        self.stats: dict = {}

    def run(self) -> None:                     # noqa: D102  (QThread entry point)
        try:
            from .__main__ import (_build_runner, _capture_strips, _live_size,
                                   _select_profile)
            from .capture.source import open_source
            from .consent import require

            # The same gate the CLI passes through. A second front end must not become a
            # second way in: consent is checked here, on the path that actually collects.
            require(self.cfg.consent)
            # By the size being captured, not by whichever calibration was saved last.
            size = _live_size(self.args)
            profile = _select_profile(self.args, size)
            source = open_source(
                self.args.source, fps=self.args.fps,
                strips=_capture_strips(profile, bool(self.args.record)),
                expect_size=tuple(profile.frame_size),
            )
            # Building the render index takes several seconds over a few thousand
            # candidates. It happens HERE rather than on the GUI thread, which is why the
            # window stays alive while "Preparing…" is showing.
            runner, _ = _build_runner(self.cfg, self.args, size)
            self.runner = runner
            self.stats = runner.stats
            # Fires on THIS thread. It may not touch a widget; emitting is the whole job.
            runner.on_event = self._on_event
            runner.on_pickaxe = lambda kind, name, watch: self.pickaxe.emit(
                kind, name, watch.total_broken)
            runner.on_mining = lambda event, left: self.mining.emit(
                -1 if left is None else left)
            self.ready.emit()
            stats = runner.run(source, dungeon_id=self.args.dungeon, floor_id=self.args.floor)
        except (Exception, SystemExit) as exc:          # noqa: BLE001
            # SystemExit too: the CLI helpers report a missing calibration that way, and on
            # a worker thread it would otherwise end the thread with nothing shown.
            log.exception("wddrop: capture failed")
            self.failed.emit(str(exc))
            return
        reason = runner.stop_reason or "game_closed"
        from .runner import record_stop_reason
        from .uploader import record_marker, record_close

        # Stamp both copies: the outbox, for events that have not gone yet, and the
        # player's own file, so their export says the same thing the server was told.
        record_stop_reason(runner.dive_id, reason)
        record_stop_reason(runner.dive_id, reason, records_path())
        # And queue the same fact for the rows already uploaded, which the stamps above
        # cannot reach — in per-record mode that is all of them.
        record_close(runner.dive_id, reason)
        self.done.emit({**stats, "stop_reason": reason, "dive_id": runner.dive_id})

    def _on_event(self, event: dict) -> None:
        from .runner import CaptureRunner

        CaptureRunner._spool(event)
        self.chest.emit(event)

    def stop(self, reason: str = "user_stop") -> None:
        """Called from the GUI thread. Safe by construction — it sets an Event."""
        if self.runner is not None:
            self.runner.stop(reason)


class ShotWorker(QtCore.QThread):
    """Takes one screenshot of the game window after a countdown.

    On its own thread because the countdown is the point: the player has to switch back to
    the game, so the window must keep repainting while it runs. Sleeping on the GUI thread
    would freeze the very countdown it is displaying.
    """

    tick = QtCore.Signal(int)
    shot = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, delay: float, path: Path, parent=None):
        super().__init__(parent)
        self.delay = delay
        self.path = path

    def run(self) -> None:                     # noqa: D102
        import time

        try:
            from .__main__ import _grab_window

            for remaining in range(int(self.delay), 0, -1):
                self.tick.emit(remaining)
                time.sleep(1)
            image = _grab_window(0)
            image.save(self.path)
        except Exception as exc:                       # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.shot.emit(str(self.path))


class FitWorker(QtCore.QThread):
    """Fits the profile from the two screenshots. Seconds of work, so not on the GUI thread."""

    done = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, args, name: str, drop: Path, walk: Path | None, parent=None):
        super().__init__(parent)
        self.args = args
        self.name = name
        self.drop = drop
        self.walk = walk

    def run(self) -> None:                     # noqa: D102
        try:
            from PIL import Image

            from .__main__ import _font_candidates, _load_vocab, _prefix_from
            from .calibration import ProfileStore, fit_hud, fit_message_profile

            vocab, fmt, _ = _load_vocab(self.args)
            profile = fit_message_profile(
                Image.open(self.drop), self.name, _prefix_from(fmt),
                _font_candidates(self.args), [e.name for e in vocab.entries],
                locale=self.args.locale,
            )
            if self.walk:
                profile = fit_hud(profile, Image.open(self.walk),
                                  template_path=Path(self.args.data) / "hud_template.png")
            root = Path(self.args.data)
            store = ProfileStore.load(root)
            store.put(profile)
            store.save(root)
            profile.save(root / "profile.json")
        except Exception as exc:                       # noqa: BLE001
            log.exception("wddrop: calibration failed")
            self.failed.emit(str(exc))
            return
        self.done.emit({
            "size": ProfileStore.key_for(profile.frame_size),
            "font_size": profile.font_size,
            "score": profile.calibration_score,
            "notes": dict(profile.notes or {}),
            "hud": bool(profile.hud_template_b64 or profile.hud_template_path),
        })


class ReadWorker(QtCore.QThread):
    """Reads the item name out of the calibration shot, to fill the box in for the player.

    On a thread because it fits a font sweep and then indexes the whole vocabulary — around
    eight seconds on a real shot, which would freeze the dialog it is meant to help.
    """

    read = QtCore.Signal(str, float)
    blank = QtCore.Signal()
    # Emitted as soon as the vocabulary is loaded, which is long before the fit finishes —
    # so the player can start picking a name while the proposal is still being worked out.
    vocabulary = QtCore.Signal(list)

    def __init__(self, args, drop: Path, parent=None):
        super().__init__(parent)
        self.args = args
        self.drop = drop

    def run(self) -> None:                     # noqa: D102
        try:
            from PIL import Image

            from .__main__ import _font_candidates, _load_vocab, _prefix_from
            from .calibration import propose_item_name

            vocab, fmt, _ = _load_vocab(self.args)
            names = [e.name for e in vocab.entries]
            self.vocabulary.emit(names)
            guess = propose_item_name(
                Image.open(self.drop), _prefix_from(fmt), _font_candidates(self.args), names)
        except Exception:                              # noqa: BLE001
            # Filling the box in is a convenience. Failing to do it must cost nothing but
            # the typing it would have saved.
            log.exception("wddrop: could not propose an item name")
            self.blank.emit()
            return
        if guess is None:
            self.blank.emit()
        else:
            self.read.emit(guess[0], guess[2])


class ConsentPage(QtWidgets.QWidget):
    """The gate. A page, not a checkbox on a form.

    Collection cannot start until this is accepted, and the accepted text is hashed into the
    config so a later change to the terms is detectable rather than assumed-agreed.
    """

    accepted = QtCore.Signal()

    def __init__(self, cfg: ClientConfig, t=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.t = t or Translator(cfg.ui_locale)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)

        # A RETURNING player is not a new one. Coming back to this page because the terms
        # were edited looks identical to a first run otherwise, and a player who reads
        # "before anything is recorded" on their tenth session reasonably concludes their
        # data is gone. Say which it is, and what changed the answer they already gave.
        again = bool(cfg.consent.accepted_hash) and not cfg.consent.general_ok
        title = QtWidgets.QLabel(self.t("These terms have changed") if again
                                 else self.t("Before anything is recorded"))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)
        if again:
            changed = QtWidgets.QLabel(self.t(
                "You agreed to an earlier version. Nothing you have already recorded is "
                "affected — read this one and answer again, including whether you share."))
            changed.setObjectName("hint")
            changed.setWordWrap(True)
            layout.addWidget(changed)

        text = QtWidgets.QTextBrowser()
        text.setMarkdown(self._disclaimer())
        text.setOpenExternalLinks(True)
        layout.addWidget(text, 1)

        self.agree = QtWidgets.QCheckBox(self.t("I have read this and agree"))
        layout.addWidget(self.agree)
        # ASKED HERE, ONCE, rather than left as a switch in Settings that may never be
        # found — so it is an answer rather than an unnoticed default. Still changeable in
        # Settings afterwards.
        self.share = QtWidgets.QCheckBox(self.t("Share my drop records"))
        self.share.setChecked(self.cfg.share_uploads)
        layout.addWidget(self.share)
        note = QtWidgets.QLabel(self.t(
            "Your records are pooled with other players' to work out the drop rates for "
            "each dungeon. Taking part is your choice — everything is recorded and kept on "
            "this computer either way, and this only decides whether it is also sent."))
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.button = QtWidgets.QPushButton(self.t("Continue"))
        self.button.setObjectName("primary")
        self.button.setEnabled(False)
        self.agree.toggled.connect(self.button.setEnabled)
        self.button.clicked.connect(self._accept)
        row.addWidget(self.button)
        layout.addLayout(row)

    @staticmethod
    def _disclaimer() -> str:
        """The one canonical text, from consent.py — never a copy found elsewhere.

        The acceptance is stored as a hash OF THIS TEXT so that editing the terms re-prompts
        instead of silently inheriting agreement. Showing one file and hashing another would
        quietly break that, so both come from the same function.
        """
        from .consent import disclaimer_text

        return disclaimer_text()

    def _accept(self) -> None:
        from .consent import disclaimer_hash

        self.cfg.consent.accepted_hash = disclaimer_hash()
        self.cfg.share_uploads = self.share.isChecked()
        self.cfg.asked_sharing = True
        self.cfg.save()
        self.accepted.emit()


class CalibrateDialog(QtWidgets.QDialog):
    """The guided fit: two screenshots the client takes itself, then the answer key.

    The client takes the shots rather than accepting the player's, because the commonest
    setup failure is a scaled or cropped screenshot — its geometry no longer matches the
    live window, and every region in a profile is absolute pixels.
    """

    def __init__(self, args, parent=None, t=None):
        super().__init__(parent)
        self.args = args
        # Calibration is the FIRST thing a new player is sent to and the one step they
        # cannot skip, so leaving it in English undid the point of translating the window
        # at all: the page that explains what to photograph was the page nobody could read.
        self.t = t or Translator(None)
        self.setWindowTitle(self.t("Calibrate…").rstrip("…"))
        self.resize(520, 300)
        self.walk: Path | None = None
        self.drop: Path | None = None
        self.result: dict | None = None
        self._worker = None

        layout = QtWidgets.QVBoxLayout(self)
        self.step = QtWidgets.QLabel(self.t(
            "Step 1 of 2 — stand in a dungeon with the minimap visible, then press Capture.\n"
            "You will have a few seconds to switch back to the game."))
        self.step.setWordWrap(True)
        layout.addWidget(self.step)

        self.preview = QtWidgets.QLabel()
        self.preview.setMinimumHeight(120)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet("border: 1px solid palette(mid);")
        layout.addWidget(self.preview, 1)

        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText(
            self.t("the item name in that message — calibration's answer key"))
        self.name.setVisible(False)
        layout.addWidget(self.name)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        self.skip = QtWidgets.QPushButton(self.t("Skip this shot"))
        self.skip.clicked.connect(self._skip)
        row.addWidget(self.skip)
        row.addStretch(1)
        self.action = QtWidgets.QPushButton(self.t("Capture"))
        self.action.clicked.connect(self._capture)
        row.addWidget(self.action)
        layout.addLayout(row)

    def showEvent(self, event: QtGui.QShowEvent) -> None:        # noqa: N802 (Qt)
        super().showEvent(event)
        theme.apply_titlebar(self)

    # -- steps --------------------------------------------------------------------
    def _capture(self) -> None:
        target = "walk.png" if self.walk is None and self.drop is None else "drop.png"
        path = Path(self.args.data) / target
        self.action.setEnabled(False)
        self.skip.setEnabled(False)
        self._worker = ShotWorker(self.args.delay, path, self)
        self._worker.tick.connect(
            lambda n: self.status.setText(self.t("switching back to the game… {n}", n=n)))
        self._worker.shot.connect(self._got_shot)
        self._worker.failed.connect(self._error)
        self._worker.start()

    def _skip(self) -> None:
        if self.walk is None and self.drop is None:
            # Skipping the walk shot means no HUD template, and without one episodes never
            # close on the HUD returning — they fall back to the idle timeout, which does
            # not bracket chests. Say so plainly instead of failing later.
            self.status.setText(self.t(
                "No HUD template will be made — chest bracketing will be poor."))
            self._advance_to_drop()
        else:
            self.reject()

    def _got_shot(self, path: str) -> None:
        self.action.setEnabled(True)
        self.skip.setEnabled(True)
        pix = QtGui.QPixmap(path)
        self.preview.setPixmap(pix.scaled(480, 160, QtCore.Qt.KeepAspectRatio,
                                          QtCore.Qt.SmoothTransformation))
        if self.walk is None and self.drop is None:
            self.walk = Path(path)
            self._advance_to_drop()
        else:
            self.drop = Path(path)
            self.step.setText(self.t("Which item does that message name? Type it exactly."))
            self.name.setVisible(True)
            self.name.setFocus()
            self.action.setText(self.t("Fit"))
            self.action.clicked.disconnect()
            self.action.clicked.connect(self._fit)
            # Try to fill it in. The player still confirms — see propose_item_name.
            self.status.setText(self.t("reading the item name…"))
            self._reader = ReadWorker(self.args, self.drop, self)
            self._reader.vocabulary.connect(self._offer_names)
            self._reader.read.connect(self._proposed)
            self._reader.blank.connect(
                lambda: self.status.setText(self.t("Could not read it — please type it.")))
            self._reader.start()

    def _offer_names(self, names: list) -> None:
        """Complete against the vocabulary rather than asking anyone to spell it.

        Two things follow from picking instead of typing. The obvious one: nobody has to
        reproduce 莫尼翁銀幣 on a keyboard that may not have it. The one that matters more:
        the fit REFUSES a name the vocabulary does not contain, and a typo is exactly how
        you get one — so completing from the same list the fit checks against turns a
        confusing failure into an impossible one.

        Contains, not starts-with: a player recognises a character from the middle of a name
        far more often than they can produce its first one.
        """
        completer = QtWidgets.QCompleter(names, self.name)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        completer.setMaxVisibleItems(12)
        theme.square_corners(completer.popup().window())
        self.name.setCompleter(completer)

    def _proposed(self, name: str, margin: float) -> None:
        """Offer what was read. The box is filled in; agreeing to it is still an act."""
        if self.name.text().strip():
            return                                     # the player got there first
        self.name.setText(name)
        self.name.selectAll()
        self.step.setText(self.t("Is this the item in the message? Correct it if not."))
        self.status.setText(self.t("read from your screenshot (margin {margin})",
                                   margin=f"{margin:+.3f}"))

    def _advance_to_drop(self) -> None:
        self.step.setText(self.t(
            "Step 2 of 2 — open a chest and leave the 「獲得了…」 message on screen, then "
            "press Capture."))

    def _fit(self) -> None:
        name = self.name.text().strip()
        if not name or self.drop is None:
            self.status.setText(
                self.t("The item name is calibration's answer key; it cannot be blank."))
            return
        self.action.setEnabled(False)
        self.status.setText(self.t("fitting…"))
        self._worker = FitWorker(self.args, name, self.drop, self.walk, self)
        self._worker.done.connect(self._fitted)
        self._worker.failed.connect(self._error)
        self._worker.start()

    def _fitted(self, result: dict) -> None:
        self.result = result
        notes = result.get("notes") or {}
        # The self-check is the part worth showing: it re-reads the player's OWN drop shot
        # against the whole vocabulary and must come back with the name they typed. A
        # profile that cannot read the frame it was built from is not a profile.
        checked = notes.get("self_check_name")
        margin = notes.get("self_check_margin")
        self.status.setText(
            f"{result['size']} at {result['font_size']}px, fit {result['score']:.3f}\n"
            + self.t("self-check read back:") + f" {checked!r}"
            + (f" ({self.t('margin')} {margin:+.4f})" if margin is not None else "")
            + ("" if result["hud"]
               else "\n" + self.t("No HUD template — chest bracketing will be poor.")))
        self.action.setText(self.t("Done"))
        self.action.setEnabled(True)
        self.action.clicked.disconnect()
        self.action.clicked.connect(self.accept)

    def _error(self, message: str) -> None:
        self.action.setEnabled(True)
        self.skip.setEnabled(True)
        self.status.setText(f"[!] {message}")


class UploadWorker(QtCore.QThread):
    """Drains the spool. On a thread because it is network I/O of unbounded duration."""

    done = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, cfg: ClientConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self) -> None:                     # noqa: D102
        try:
            from wddrop_schema.models import CaptureMode

            from .uploader import upload_spool

            result = upload_spool(self.cfg, CaptureMode.OCR)
        except Exception as exc:                       # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


class Combo(QtWidgets.QComboBox):
    """A combobox that squares its own dropdown, and does not build it before it is needed.

    The list is its own native window, so Windows 11 rounds it — soft corners inside a
    window whose every other edge is a hard rule. The style sheet cannot reach that: the
    rounding is the compositor's, applied to the window rather than to the widget, and it
    has to be applied per show because the popup's handle does not exist until then.

    Doing it here rather than by filtering `view()` matters. Asking a combobox for its view
    CONSTRUCTS the popup container there and then, so attaching the filter at build time
    quietly moved the cost — and a Qt font warning that comes with it — into window startup,
    for 56 items nobody had asked to see yet.
    """

    def showPopup(self) -> None:                       # noqa: N802 (Qt)
        # BEFORE and after. Windows decides a window's corners as it is shown, so setting the
        # attribute only afterwards can leave the first showing of a given popup rounded —
        # and a list that is rebuilt gets a new window, which makes every showing the first
        # one. `winId()` forces the native handle to exist so the attribute has something to
        # apply to; without it there is nothing there yet to square.
        popup = self.view().window()
        popup.winId()
        theme.square_corners(popup)
        super().showPopup()
        theme.square_corners(self.view().window())


class ShareBar(QtWidgets.QStyledItemDelegate):
    """A bar drawn in the cell, from a 0..1 in the item's data.

    Painted rather than assembled from widgets: a table of thirty rows with a widget each
    rebuilds them on every refresh, and painting is what a table already does. No image is
    involved — the game's own icons are its artwork, and this page is not going to carry it.
    """

    def paint(self, painter, option, index) -> None:                # noqa: N802 (Qt)
        fraction = index.data(QtCore.Qt.UserRole)
        super().paint(painter, option, index)
        if not isinstance(fraction, float):
            return
        room = option.rect.adjusted(6, 0, -6, 0)
        height = 4
        top = room.center().y() - height // 2
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(theme.RULE))
        painter.drawRect(room.left(), top, room.width(), height)
        painter.setBrush(QtGui.QColor(theme.INK))
        painter.drawRect(room.left(), top, max(1, int(room.width() * fraction)), height)
        painter.restore()


class WheelGuard(QtCore.QObject):
    """Stops the wheel editing a control the player is only scrolling past.

    Qt gives comboboxes and spin boxes `WheelFocus`, so they consume wheel events whether
    or not anyone chose them. Scrolling the Settings page therefore rewrote whichever
    control happened to pass under the cursor — silently, and on this form that means the
    game language or the sample rate. Both decide whether a session records anything at
    all, and neither announces that it changed.

    So: focus first, then the wheel adjusts. Otherwise the event goes to the scroll area,
    which is what turning the wheel meant.
    """

    def eventFilter(self, watched, event) -> bool:     # noqa: N802 (Qt)
        if event.type() != QtCore.QEvent.Type.Wheel or watched.hasFocus():
            return False
        area = self._scroll_area(watched)
        if area is not None:
            QtWidgets.QApplication.sendEvent(area.viewport(), event)
        # Consumed even with no scroll area to hand it to: a control outside one still must
        # not change value because the page moved under the pointer.
        return True

    @staticmethod
    def _scroll_area(widget) -> QtWidgets.QScrollArea | None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None


class Nav(QtWidgets.QLabel):
    """One word in the top-right navigation. A label rather than a tab bar, because three
    destinations do not need chrome and the ribbon is where the eye already is."""

    clicked = QtCore.Signal()

    def __init__(self, text: str):
        super().__init__(text)
        self.setObjectName("nav")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)

    def mousePressEvent(self, event):        # noqa: N802 (Qt)
        self.clicked.emit()

    def keyPressEvent(self, event):          # noqa: N802 (Qt)
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def set_current(self, current: bool) -> None:
        self.setProperty("current", "true" if current else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QtWidgets.QMainWindow):
    """Three pages under one ribbon: Record, Guide, Settings.

    The ribbon's state line is the single source of truth about whether recording is healthy,
    because that is the one question this window exists to answer at a glance.
    """

    def __init__(self, cfg: ClientConfig, data: Path | None = None):
        super().__init__()
        self.cfg = cfg
        self.data = Path(data) if data else config_dir()
        self.t = Translator(cfg.ui_locale)
        self.worker: CaptureWorker | None = None
        self.uploader: UploadWorker | None = None
        self._upload_again = False
        self._titlebar_themed = False
        self._wheel_guard = WheelGuard(self)
        self.chests = 0
        self.mined = 0
        self._ready = False
        self._hints = None
        self._catalog: list = []
        # Dive markers and pickaxe-break notes. LOCAL ONLY — the server is never told, so
        # they live beside the spool rather than in it.
        self.markers: list[dict] = []
        self._started_at: datetime | None = None
        self._swings_since_break = 0
        self._pickaxe_lives: list[int] = []

        self.setWindowTitle(f"wddrop {CLIENT_VERSION}")
        self.resize(820, 700)
        # WDDROP_NO_STYLE=1 starts the window unstyled. It is here to settle one question in
        # a single run — whether a Qt warning comes from this sheet or from Qt's own popup
        # handling — because the alternative is guessing at Qt internals across a machine
        # this code cannot be run on.
        if os.environ.get("WDDROP_NO_STYLE") != "1":
            self.setStyleSheet(theme.stylesheet())

        self.stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stack)
        self.consent_page = ConsentPage(cfg, self.t)
        self.consent_page.accepted.connect(self._consented)
        self.stack.addWidget(self.consent_page)
        self.stack.addWidget(self._build_shell())

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(STATS_INTERVAL_MS)
        self.timer.timeout.connect(self._refresh_stats)

        # `general_ok`, NOT "is there a hash". They are different questions the moment the
        # disclaimer text changes: acceptance is stored as a hash OF THE TEXT so that editing
        # the terms re-prompts, and capture checks that hash matches. A window that skipped
        # the disclaimer on any hash at all therefore never re-asked — and refused to start,
        # with the player looking at a Settings page that said they had agreed.
        if cfg.consent.general_ok:
            self.stack.setCurrentIndex(1)
        self._refresh_setup()

        # Anything the last run could not send — including how a session that ended by the
        # window being closed ended, which by definition had no chance to go then. Only in
        # per-record mode: "send when I press Upload" means exactly that.
        if (cfg.consent.general_ok and cfg.share_uploads
                and cfg.send_mode in AUTOMATIC_MODES):
            QtCore.QTimer.singleShot(0, lambda: self._upload(quiet=True))

    # -- shell ---------------------------------------------------------------------
    def _build_shell(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        ribbon = QtWidgets.QFrame()
        ribbon.setObjectName("ribbon")
        rl = QtWidgets.QVBoxLayout(ribbon)
        rl.setContentsMargins(20, 14, 20, 14)
        rl.setSpacing(3)
        top = QtWidgets.QHBoxLayout()
        # No wordmark here. The title bar above already says what this is, and now that it
        # is painted to match the ribbon the two sat one above the other saying it twice.
        top.addStretch(1)
        self.nav = {}
        for index, key in ((0, "Record"), (1, "Stats"), (2, "Guide"), (3, "Settings")):
            item = Nav(self.t(key))
            item.clicked.connect(lambda i=index: self._show_page(i))
            self.nav[index] = item
            top.addWidget(item)
            if index < 3:
                dot = QtWidgets.QLabel("·")
                dot.setObjectName("meta")
                top.addWidget(dot)
        rl.addLayout(top)
        # The one line that answers "is this working?". Everything else is detail.
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("state")
        self.status.setWordWrap(True)
        rl.addWidget(self.status)
        outer.addWidget(ribbon)

        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self._build_record())
        self.pages.addWidget(self._build_stats())
        self.pages.addWidget(self._build_guide())
        self.pages.addWidget(self._build_settings())
        outer.addWidget(self.pages, 1)
        self._show_page(0)
        return page

    def _show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, item in self.nav.items():
            item.set_current(i == index)
        if index == 1:
            self._refresh_stats_page()

    def _no_wheel(self, *widgets) -> None:
        """Make these take the wheel only once focused. See WheelGuard.

        Nothing here touches `view()`: that would build the dropdown to attach something to
        it. Squaring the popup belongs to `Combo`, which does it when one actually opens.
        """
        for widget in widgets:
            widget.setFocusPolicy(QtCore.Qt.StrongFocus)
            widget.installEventFilter(self._wheel_guard)

    # -- record page ---------------------------------------------------------------
    def _build_record(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The session's settings, one line, with the controls beside them. During a run the
        # controls disable and the line becomes a summary — setup is a before-concern.
        bar = QtWidgets.QFrame()
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(20, 12, 20, 12)
        bl.setSpacing(10)
        self.dungeon = Combo()
        self.dungeon.setMinimumWidth(240)
        self.dungeon.currentIndexChanged.connect(self._dungeon_changed)
        bl.addWidget(self.dungeon)
        # HIDDEN, NOT REMOVED. Floor is optional in the schema on purpose: players will not
        # keep a floor dropdown current through a dive, and a stale label is worse than an
        # honest null — it would file chests under a floor they did not come from, in the
        # one field the analysis strata are built on. It stays here, unshown and therefore
        # always null, so the day something can set it reliably there is a control to reveal
        # rather than a page to rebuild.
        self.floor = Combo()
        self.floor.setMinimumWidth(150)
        self.floor.setVisible(False)
        bl.addWidget(self.floor)
        # Here rather than in Settings: it is changed every time the player restocks, which
        # is far too often for a page you have to go and find. It is also the only setting
        # that belongs to the dive rather than to the client.
        self.pickaxe_caption = QtWidgets.QLabel(self.t("Pickaxes carried"))
        self.pickaxe_caption.setObjectName("hint")
        bl.addWidget(self.pickaxe_caption)
        self.pickaxes = QtWidgets.QSpinBox()
        self.pickaxes.setRange(0, 999)
        self.pickaxes.setSpecialValueText(self.t("not sure"))
        self.pickaxes.setValue(self.cfg.pickaxes)
        self.pickaxes.valueChanged.connect(self._pickaxes_changed)
        bl.addWidget(self.pickaxes)
        bl.addStretch(1)
        self._no_wheel(self.dungeon, self.floor, self.pickaxes)
        self.counters = QtWidgets.QLabel("")
        self.counters.setObjectName("meta")
        bl.addWidget(self.counters)
        layout.addWidget(bar)

        self.pickaxe_label = QtWidgets.QLabel("")
        self.pickaxe_label.setObjectName("hint")
        self.pickaxe_label.setContentsMargins(20, 0, 20, 8)
        layout.addWidget(self.pickaxe_label)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [self.t("at"), self.t("from"), self.t("what it recorded")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.table.setColumnWidth(0, 74)
        self.table.setColumnWidth(1, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        theme.apply_item_highlight(self.table)
        layout.addWidget(self.table, 1)

        foot = QtWidgets.QFrame()
        foot.setObjectName("footer")
        fl = QtWidgets.QHBoxLayout(foot)
        fl.setContentsMargins(20, 14, 20, 14)
        fl.setSpacing(10)
        self.start = QtWidgets.QPushButton(self.t("Start recording"))
        self.start.setObjectName("primary")
        self.start.clicked.connect(self._toggle)
        fl.addWidget(self.start)
        self.mark = QtWidgets.QPushButton(self.t("Mark next dive"))
        self.mark.clicked.connect(self._mark_dive)
        self.mark.setEnabled(False)
        fl.addWidget(self.mark)
        self.broke = QtWidgets.QPushButton(self.t("A pickaxe broke"))
        self.broke.clicked.connect(self._pickaxe_broke)
        self.broke.setEnabled(False)
        fl.addWidget(self.broke)
        fl.addStretch(1)
        self.spool_label = QtWidgets.QLabel("")
        self.spool_label.setObjectName("hint")
        fl.addWidget(self.spool_label)
        self.upload = QtWidgets.QPushButton(self.t("Upload"))
        self.upload.clicked.connect(self._upload)
        fl.addWidget(self.upload)
        layout.addWidget(foot)
        return page

    # -- stats page ----------------------------------------------------------------
    def _build_stats(self) -> QtWidgets.QWidget:
        """What this player has recorded — from their own file, never from the server.

        The numbers are COUNTS, never rates. A drop rate needs a denominator that survives
        scrutiny, and one player's dives are not a random sample of anything; the study
        answers that with a pre-registered analysis over pooled data. This page answers the
        much weaker "what have I seen so far", and it should look like the weaker claim.
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QtWidgets.QFrame()
        hl = QtWidgets.QVBoxLayout(head)
        hl.setContentsMargins(20, 16, 20, 12)
        hl.setSpacing(4)
        self.stats_headline = QtWidgets.QLabel("")
        self.stats_headline.setObjectName("state")
        hl.addWidget(self.stats_headline)
        self.stats_detail = QtWidgets.QLabel("")
        self.stats_detail.setObjectName("hint")
        self.stats_detail.setWordWrap(True)
        hl.addWidget(self.stats_detail)

        # A day picker, filled from the days there ARE — never a calendar. A date with
        # nothing behind it looks like a day the player recorded nothing, which is a
        # different claim from "you did not play".
        pick = QtWidgets.QHBoxLayout()
        pick.setSpacing(8)
        self.stats_day = Combo()
        self.stats_day.setMinimumWidth(180)
        self._no_wheel(self.stats_day)
        self.stats_day.currentIndexChanged.connect(self._refresh_stats_page)
        pick.addWidget(self.stats_day)

        # Chests and veins are different questions. Pooling them puts 582 pebbles beside 32
        # shells and calls the result a distribution.
        self.stats_source = Combo()
        self.stats_source.setMinimumWidth(140)
        self._no_wheel(self.stats_source)
        for label, value in ((self.t("Chests and veins"), None),
                             (self.t("Chests"), "chest"), (self.t("Veins"), "vein")):
            self.stats_source.addItem(label, value)
        self.stats_source.currentIndexChanged.connect(self._refresh_stats_page)
        pick.addWidget(self.stats_source)
        self.stats_overall = QtWidgets.QLabel("")
        self.stats_overall.setObjectName("hint")
        pick.addWidget(self.stats_overall, 1)
        self.stats_pickaxes = QtWidgets.QLabel("")
        self.stats_pickaxes.setObjectName("hint")
        hl.addWidget(self.stats_pickaxes)
        hl.addLayout(pick)
        layout.addWidget(head)

        self.stats_table = QtWidgets.QTableWidget(0, 4)
        self.stats_table.setHorizontalHeaderLabels(
            [self.t("what it recorded"), self.t("total"), self.t("share"), ""])
        self.stats_table.setItemDelegateForColumn(3, ShareBar(self.stats_table))
        self.stats_table.horizontalHeader().setStretchLastSection(False)
        self.stats_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch)
        self.stats_table.horizontalHeader().setDefaultAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        for column, width in ((1, 90), (2, 80), (3, 170)):
            self.stats_table.setColumnWidth(column, width)
        # Right-aligned headers over right-aligned numbers, so the eye scans one edge.
        for column in (1, 2):
            self.stats_table.horizontalHeaderItem(column).setTextAlignment(
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setShowGrid(False)
        self.stats_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.stats_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        theme.apply_item_highlight(self.stats_table)
        layout.addWidget(self.stats_table, 1)

        foot = QtWidgets.QFrame()
        foot.setObjectName("footer")
        fl = QtWidgets.QHBoxLayout(foot)
        fl.setContentsMargins(20, 14, 20, 14)
        self.stats_note = QtWidgets.QLabel("")
        self.stats_note.setObjectName("hint")
        self.stats_note.setWordWrap(True)
        fl.addWidget(self.stats_note, 1)
        refresh = QtWidgets.QPushButton(self.t("Refresh"))
        refresh.clicked.connect(self._refresh_stats_page)
        fl.addWidget(refresh)
        layout.addWidget(foot)
        return page

    def _fill_days(self, data: dict, chosen) -> None:
        """Keep the day list current without disturbing the choice.

        Rebuilt on every refresh because a session running in another window adds days as it
        goes. Signals are blocked while it happens: repopulating fires currentIndexChanged,
        and refreshing from inside a refresh is an endless one.
        """
        wanted = [(self.t("All days"), None)] + [
            (f"{row['day']}   {row['openings']}", row["day"]) for row in data["days"]]
        current = [(self.stats_day.itemText(i), self.stats_day.itemData(i))
                   for i in range(self.stats_day.count())]
        if current == wanted:
            return
        self.stats_day.blockSignals(True)
        self.stats_day.clear()
        for label, value in wanted:
            self.stats_day.addItem(label, value)
        at = self.stats_day.findData(chosen)
        self.stats_day.setCurrentIndex(at if at >= 0 else 0)
        self.stats_day.blockSignals(False)

    def _place_names(self) -> dict:
        """id -> name, for dungeons and their floors.

        Everything the player sees is named through this. Ids are how the data is filed and
        how the server strata are keyed; they are not something to put in front of someone
        deciding which dungeon they are standing in, or opening their own spreadsheet.
        """
        names = {}
        for dungeon in self._catalog:
            names[dungeon["id"]] = dungeon["name"]
            for floor in dungeon.get("floors", []):
                names[floor["id"]] = floor["name"]
        return names

    def _refresh_stats_page(self) -> None:
        from .stats import summarise

        chosen = self.stats_day.currentData()
        data = summarise(records_path(), spool_path(), day=chosen,
                         source=self.stats_source.currentData())
        self._fill_days(data, chosen)
        names = self._place_names()

        overall = data["overall"]
        self.stats_overall.setText(self.t(
            "all time: {openings} openings · {lines} item lines · {days} days",
            openings=overall["openings"], lines=overall["lines"], days=len(data["days"])))

        self.stats_headline.setText(
            f"{data['openings']} {self.t('openings')} · "
            f"{data['chests']} {self.t('chest')} · {data['veins']} {self.t('vein')} · "
            f"{data['lines']} {self.t('item lines')}")
        # What the ore cost. A share of the ore says nothing without it, and it is the one
        # number a player cannot reconstruct afterwards from the items alone.
        # Only where it means something. Pickaxes are a mining cost, and on the chest view
        # the number is true but answers a question nobody asked there.
        broken, veins = data["broken"], data["veins"]
        looking_at_veins = self.stats_source.currentData() in (None, "vein")
        if looking_at_veins and (broken or veins):
            share = f" ({broken / veins * 100:.1f}%)" if veins else ""
            self.stats_pickaxes.setText(f"{self.t('pickaxes broken')} ×{broken}{share}")
        else:
            self.stats_pickaxes.setText("")

        detail = []
        if data["dungeons"]:
            # Named or not at all. An id here would be a number the player cannot place.
            detail.append(" · ".join(
                f"{names.get(dungeon) or self.t('not sure')} {count}"
                for dungeon, count in list(data["dungeons"].items())[:4]))
        if data["empty"]:
            # An empty chest is the WORST outcome and a real observation, so it is stated
            # rather than left to look like a gap in the data.
            detail.append(self.t("{n} of them were empty", n=data["empty"]))
        if data["first"]:
            from .stats import jst_day

            span = jst_day(data["first"]), jst_day(data["last"])
            detail.append(span[0] if span[0] == span[1] else f"{span[0]} — {span[1]}")
        detail.append(self.t("days reset at 00:00 JST, as the game does"))
        self.stats_detail.setText("   ".join(detail))

        rows = data["by_item"]
        # CLEARED, not just resized. Shrinking a table leaves the cells that were there —
        # switching from every source to veins alone left the previous row's share and bar
        # sitting on the TOTAL line, which read as a total having a percentage of itself.
        self.stats_table.clearContents()
        # A TOTAL row at the end, as the game's own tally screen has: a column of shares is
        # not readable without the number they are shares OF.
        self.stats_table.setRowCount(len(rows) + (1 if rows else 0))
        for index, row in enumerate(rows):
            cells = (row["item"], f"×{row['quantity']}", f"{row['share'] * 100:.1f}%", "")
            for column, value in enumerate(cells):
                cell = QtWidgets.QTableWidgetItem(value)
                if column:
                    cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if column == 3:
                    cell.setData(QtCore.Qt.UserRole, float(row["of_top"]))
                    cell.setToolTip(self.t("{n} openings gave this", n=row["openings"]))
                self.stats_table.setItem(index, column, cell)
        if rows:
            total = QtWidgets.QTableWidgetItem(
                self.t("total of {n} kinds", n=len(rows)))
            total.setForeground(QtGui.QColor(theme.VELLUM))
            amount = QtWidgets.QTableWidgetItem(f"×{data['total_quantity']}")
            amount.setForeground(QtGui.QColor(theme.VELLUM))
            amount.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.stats_table.setItem(len(rows), 0, total)
            self.stats_table.setItem(len(rows), 1, amount)

        note = [self.t("Counted from what was recorded on this computer, not from what was "
                       "sent. These are counts, not drop rates.")]
        if data["unsent"]:
            note.append(self.t("{n} not sent yet", n=data["unsent"]))
        self.stats_note.setText("  ".join(note))

    # -- settings page -------------------------------------------------------------
    def _build_settings(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(body)
        form.setContentsMargins(24, 22, 24, 22)
        form.setVerticalSpacing(16)

        def row(label, widget, hint=None):
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(3)
            box.addWidget(widget)
            if hint:
                note = QtWidgets.QLabel(hint)
                note.setObjectName("hint")
                note.setWordWrap(True)
                box.addWidget(note)
            holder = QtWidgets.QWidget()
            holder.setLayout(box)
            form.addRow(label, holder)

        self.share = QtWidgets.QCheckBox(self.t("Share my drop records"))
        self.share.setChecked(self.cfg.share_uploads)
        self.share.toggled.connect(self._sharing_changed)
        row(self.t("Sharing"), self.share,
            self.t("Your records are pooled with other players' to work out the drop rates "
                   "for each dungeon. Taking part is your choice — everything is recorded "
                   "and kept on this computer either way, and this only decides whether it "
                   "is also sent."))

        self.send_mode = Combo()
        self.send_mode.addItem(
            self.t("Send every {n} records", n=self.cfg.send_batch_size), SEND_BATCH)
        self.send_mode.addItem(self.t("Send each record as it happens"), SEND_EACH)
        self.send_mode.addItem(self.t("Send when I press Upload"), SEND_MANUAL)
        self.send_mode.setCurrentIndex(max(0, self.send_mode.findData(self.cfg.send_mode)))
        self.send_mode.currentIndexChanged.connect(self._send_mode_changed)
        row(self.t("When to send"), self.send_mode)

        # SHOWN, NOT EDITABLE. Where a player's records go is not a preference: a wrong
        # value here sends them nowhere, or somewhere nobody intended, and "change your
        # server address to..." is exactly the instruction someone else would give. It stays
        # visible because a client that quietly decides where data goes is worse than one
        # that shows you and does not let you break it.
        self.server = QtWidgets.QLabel(self.cfg.server_url)
        self.server.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row(self.t("Server"), self.server)

        self.ui_locale = Combo()
        self.ui_locale.addItem(
            f"{self.t('Follow Windows')} ({NATIVE_NAMES.get(system_locale(), 'English')})", None)
        for code in LOCALES:
            self.ui_locale.addItem(NATIVE_NAMES[code], code)
        current = self.ui_locale.findData(self.cfg.ui_locale)
        self.ui_locale.setCurrentIndex(max(0, current))
        self.ui_locale.currentIndexChanged.connect(self._ui_locale_changed)
        row(self.t("Interface language"), self.ui_locale)

        self.locale = Combo()
        for code in LOCALES:
            self.locale.addItem(NATIVE_NAMES[code], code)
        self.locale.setCurrentIndex(max(0, self.locale.findData(self.cfg.locale)))
        self.locale.currentTextChanged.connect(self._locale_changed)
        row(self.t("Game language"), self.locale,
            self.t("The language the game itself is in. It decides which item names can be "
                   "read."))

        self.data_label = QtWidgets.QLabel()
        self.data_label.setWordWrap(True)
        self.data_label.setObjectName("hint")
        cal = QtWidgets.QHBoxLayout()
        self.cal_label = QtWidgets.QLabel()
        self.cal_label.setWordWrap(True)
        cal.addWidget(self.cal_label, 1)
        self.cal_button = QtWidgets.QPushButton(self.t("Calibrate…"))
        self.cal_button.clicked.connect(self._calibrate)
        cal.addWidget(self.cal_button)
        holder = QtWidgets.QWidget()
        holder.setLayout(cal)
        form.addRow(self.t("Calibrate…").rstrip("…"), holder)
        form.addRow("", self.data_label)

        self.fps = QtWidgets.QDoubleSpinBox()
        self.fps.setRange(1.0, 60.0)
        self.fps.setValue(DEFAULT_FPS)
        self.fps.setSuffix(" fps")
        self.fps.valueChanged.connect(self._check_fps)
        row(self.t("Sample rate"), self.fps)

        # Where everything is, and how to get rid of all of it. A promise that data stays on
        # the player's computer is only worth something if they can find it and delete it.
        folder = QtWidgets.QHBoxLayout()
        self.folder_label = QtWidgets.QLabel(str(self.data))
        self.folder_label.setObjectName("hint")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        folder.addWidget(self.folder_label, 1)
        open_folder = QtWidgets.QPushButton(self.t("Open folder"))
        open_folder.clicked.connect(self._open_data_folder)
        folder.addWidget(open_folder)
        holder = QtWidgets.QWidget()
        holder.setLayout(folder)
        row(self.t("Your data"), holder,
            self.t("Everything this client keeps is in that one folder. Deleting it removes "
                   "all of it from this computer; nothing is kept anywhere else."))

        self.record = QtWidgets.QCheckBox(self.t("Keep the frames"))
        self.record_all = QtWidgets.QCheckBox(self.t(
            "…including the walking frames (much bigger; for debugging a miss)"))
        self.record_all.setEnabled(False)
        self.record.toggled.connect(self.record_all.setEnabled)
        box = QtWidgets.QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(self.record)
        box.addWidget(self.record_all)
        note = QtWidgets.QLabel(self.t("Lets a mistake be re-read later. Uses disk."))
        note.setObjectName("hint")
        box.addWidget(note)
        holder = QtWidgets.QWidget()
        holder.setLayout(box)
        form.addRow(self.t("Keep the frames"), holder)

        self._no_wheel(self.send_mode, self.ui_locale, self.locale, self.fps)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        foot = QtWidgets.QFrame()
        foot.setObjectName("footer")
        fl = QtWidgets.QHBoxLayout(foot)
        fl.setContentsMargins(20, 14, 20, 14)
        export = QtWidgets.QPushButton(self.t("Export my data…"))
        export.clicked.connect(self._export)
        fl.addWidget(export)
        fl.addStretch(1)
        ident = QtWidgets.QLabel(self.t(
            "Your id is {id} — quote it to have your data erased.",
            id=self.cfg.install_id))
        ident.setObjectName("erasure")
        ident.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        fl.addWidget(ident)
        layout.addWidget(foot)
        return page

    # -- guide page ----------------------------------------------------------------
    def _build_guide(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        view = QtWidgets.QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setStyleSheet("border: none; padding: 8px 24px;")
        view.setHtml(guide_html(self.t, self.data))
        layout.addWidget(view)
        return page

    # -- state ---------------------------------------------------------------------
    def _consented(self) -> None:
        self.stack.setCurrentIndex(1)
        # Settings was built BEFORE the disclaimer was answered, so its widgets still hold
        # what the config said then. Sharing is asked on both pages and answered on one, and
        # a player who ticked it there found it unticked here — two controls for one setting,
        # disagreeing, with only the config knowing which was true.
        self._sync_settings_from_config()
        self._refresh_setup()

    def _sync_settings_from_config(self) -> None:
        """Put the config back into the Settings widgets.

        Signals blocked: these setters are what the handlers listen to, and letting them fire
        would write the value straight back — harmless today, and the kind of loop that stops
        being harmless the moment a handler does anything besides save.
        """
        for widget, value in ((self.share, self.cfg.share_uploads),):
            widget.blockSignals(True)
            widget.setChecked(bool(value))
            widget.blockSignals(False)
        at = self.send_mode.findData(self.cfg.send_mode)
        if at >= 0 and at != self.send_mode.currentIndex():
            self.send_mode.blockSignals(True)
            self.send_mode.setCurrentIndex(at)
            self.send_mode.blockSignals(False)
        self._refresh_spool()

    def _say(self, text: str, tone: str = "") -> None:
        """The ribbon's state line — plain words, and a colour only when it means something."""
        self.status.setText(text)
        self.status.setProperty("tone", tone)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _sharing_changed(self, on: bool) -> None:
        self.cfg.share_uploads = bool(on)
        self.cfg.save()
        self._refresh_spool()

    def _send_mode_changed(self, _index: int) -> None:
        self.cfg.send_mode = self.send_mode.currentData()
        self.cfg.save()
        self._refresh_spool()

    def _ui_locale_changed(self, _index: int) -> None:
        self.cfg.ui_locale = self.ui_locale.currentData()
        self.cfg.save()
        self._say(self.t("Settings save as you change them."))

    def _pickaxes_changed(self, value: int) -> None:
        self.cfg.pickaxes = int(value)
        self.cfg.save()
        self._refresh_pickaxes()

    def _locale_changed(self, _text: str) -> None:
        self.cfg.locale = self.locale.currentData() or self.cfg.locale
        self.cfg.save()
        self._refresh_setup()

    def args_for(self, **extra) -> SimpleNamespace:
        """The same argument object the CLI builds, so both drive identical code."""
        locale = self.locale.currentData() if hasattr(self, "locale") else self.cfg.locale
        locale = locale or self.cfg.locale
        vocab = find_data("vocab.{locale}.json", locale)
        atlas = find_data("atlas.{locale}.json", locale)
        base = dict(
            data=str(self.data), locale=locale,
            vocab=str(vocab) if vocab else f"vocab.{locale}.json",
            fonts=str(atlas) if atlas else None,
            open_prompt="打開", record=None, record_mode="episodes", pickaxes=None,
            dungeon=None, floor=None, fps=self.fps.value(), delay=4.0, source="window",
        )
        base.update(extra)
        return SimpleNamespace(**base)

    def _refresh_setup(self) -> None:
        locale = self.cfg.locale
        found = {
            "vocabulary": find_data("vocab.{locale}.json", locale),
            "atlas": find_data("atlas.{locale}.json", locale),
            "catalogue": find_data("catalog.{locale}.json", locale),
        }
        # The catalogue is OPTIONAL — the dungeon list is built in, and a file only overrides
        # it. The other two are not: without them nothing can be recognised at all.
        missing = [name for name, path in found.items()
                   if path is None and name != "catalogue"]
        self.data_label.setText(
            "\n".join(f"{name}: {path.name}  ({path.parent})"
                      for name, path in found.items() if path)
            + (f"\nmissing: {', '.join(missing)} — build them with tools/" if missing else ""))
        self._load_catalog(found["catalogue"])

        from .calibration import ProfileStore

        # The SHIPPED calibrations count. Without this the window asked a player to
        # calibrate a resolution the client already had a tested fit for, and refused to
        # start until they did — which is what a fresh install looked like: the fits were
        # inside the exe and only the command line ever consulted them.
        store = ProfileStore.load(self.data)
        shipped = ProfileStore.shipped()
        sizes = sorted(set(store.keys()) | set(shipped.keys()))
        if store.keys():
            self.cal_label.setText(self.t("calibrated for {sizes}",
                                          sizes=", ".join(sorted(store.keys()))))
        elif sizes:
            # Named as what it is. A player who plays at one of these needs to do nothing;
            # one who does not should understand why the window is offering them anyway.
            self.cal_label.setText(self.t("ready for {sizes} — the calibration that came "
                                          "with the client", sizes=", ".join(sizes)))
        else:
            self.cal_label.setText(self.t("not calibrated — capture cannot start without it"))
        self._ready = bool(sizes) and not missing
        # The catalogue is populated with signals blocked, so the dungeon-changed handler
        # does not run at startup and the mining controls would show on the placeholder.
        self._refresh_mining()
        self._refresh_start_enabled()
        self._refresh_spool()
        self._refresh_pickaxes()
        if not self._ready:
            self._say(self.t("Calibrate before recording."), "attention")
        elif self.dungeon.currentData() is None:
            self._say(self.t("Ready. Pick the dungeon you are in, then start."))

    def _load_catalog(self, path) -> None:
        import json

        self.dungeon.blockSignals(True)
        self.dungeon.clear()
        # A PLACEHOLDER FIRST, carrying no dungeon id: without it "I never touched this" and
        # "I chose the first entry" produce identical data, and five real chests were
        # mislabelled exactly that way.
        self.dungeon.addItem(f"— {self.t('Choose the dungeon you are in')} —", None)
        # The file if there is one, the built-in list otherwise. It used to be the file or
        # NOTHING, which made a generated file a hard dependency of a client that otherwise
        # only reads the screen — and an empty picker blocks recording entirely.
        from .dungeons import catalog as built_in

        self._catalog = built_in()
        if path is not None:
            self._catalog = json.loads(path.read_text(encoding="utf-8")).get("dungeons",
                                                                            self._catalog)
        if True:
            for d in self._catalog:
                # The name only. The id is what the data is filed under, not something the
                # player picking a dungeon has any use for.
                self.dungeon.addItem(d["name"], d["id"])
        # Restore last time's choice, if that dungeon is still in the catalogue. Done with
        # signals still blocked so it does not count as a fresh choice and re-save.
        if self.cfg.dungeon_id is not None:
            at = self.dungeon.findData(self.cfg.dungeon_id)
            if at >= 0:
                self.dungeon.setCurrentIndex(at)
        self.dungeon.blockSignals(False)
        self._dungeon_changed()

    def _dungeon_changed(self) -> None:
        chosen = self.dungeon.currentData()
        if chosen != self.cfg.dungeon_id:
            self.cfg.dungeon_id = chosen
            self.cfg.save()
        self.floor.clear()
        self.floor.addItem(self.t("not sure"), None)
        index = self.dungeon.currentIndex() - 1
        if 0 <= index < len(self._catalog):
            for f in self._catalog[index].get("floors", []):
                self.floor.addItem(f["name"], f["id"])
        self._refresh_mining()
        self._refresh_start_enabled()

    def _refresh_mining(self) -> None:
        """Show the pickaxe controls only where there is anything to mine.

        Veins exist in one dungeon. Everywhere else the panel reader is off entirely — which
        is what makes an invented mining event impossible rather than merely unlikely — so a
        pickaxe count on those floors is a control that cannot do anything, sitting next to
        a button that would record a break that could not have happened.
        """
        from .runner import MINING_DUNGEON_IDS

        mining = self.dungeon.currentData() in MINING_DUNGEON_IDS
        for widget in (self.pickaxe_caption, self.pickaxes, self.pickaxe_label):
            widget.setVisible(mining)
        self.broke.setVisible(mining)
        if mining:
            self._refresh_pickaxes()

    def _refresh_start_enabled(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            self.start.setEnabled(bool(self._ready) and self.dungeon.currentData() is not None)

    def _check_fps(self, value: float) -> None:
        if value < MIN_RECOMMENDED_FPS:
            self._say(f"{value:g} fps is below {MIN_RECOMMENDED_FPS:g}. A message dismissed "
                      f"between two samples is never captured.", "attention")

    def _waiting(self) -> int:
        """How many records are in the outbox — the number the send mode counts to."""
        path = spool_path()
        if not path.exists():
            return 0
        return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])

    def _refresh_spool(self) -> None:
        count = self._waiting()
        if not self.cfg.share_uploads:
            where = self.t("kept on this computer, not shared")
        elif self.cfg.send_mode == SEND_EACH:
            where = self.t("sending as it happens")
        elif self.cfg.send_mode == SEND_BATCH:
            where = self.t("sending every {n}", n=self.cfg.send_batch_size)
        else:
            where = ""
        self.spool_label.setText(f"{count} · {where}" if where else f"{count}")
        self.upload.setEnabled(count > 0 and self.cfg.share_uploads)

    def _refresh_pickaxes(self) -> None:
        """What is being counted, said plainly enough that nothing looks broken.

        THE COUNT MOVES ON A BREAK, NOT ON SWINGS. One vein is many swings for one pickaxe —
        the single vein ever measured gave five yields for one — so spending a pickaxe per
        swing over-counts breakage by an unknown factor, and a pickaxe the player still has
        must not be spent on screen. The swing count is shown beside it because that is the
        number that moves while mining, and seeing it move is what says the client is
        watching.

        The number in the box IS the count. It goes down when a pickaxe breaks, so there is
        one number rather than a figure the player typed and a different one derived from it.
        """
        parts = []
        if self.cfg.pickaxes or self._pickaxe_lives:
            parts.append(self.t("{n} pickaxes left", n=self.cfg.pickaxes))
        if self._swings_since_break or self._pickaxe_lives:
            parts.append(self.t("{n} swings on this one", n=self._swings_since_break))
        if self._pickaxe_lives:
            mean = sum(self._pickaxe_lives) / len(self._pickaxe_lives)
            parts.append(self.t("one lasts about {n} swings", n=f"{mean:.0f}"))
        elif parts:
            # The first pickaxe's lifetime is not known until it breaks. Saying so beats
            # showing a number that was assumed rather than counted.
            parts.append(self.t("not enough data yet"))
        self.pickaxe_label.setText(" · ".join(parts))

    # -- actions -------------------------------------------------------------------
    def _calibrate(self) -> None:
        dialog = CalibrateDialog(self.args_for(), self, t=self.t)
        dialog.exec()
        self._refresh_setup()

    def _toggle(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.start.setEnabled(False)
            self.worker.stop("user_stop")
            return
        self._begin()

    def _begin(self) -> None:
        record = str(self.data / "capture") if self.record.isChecked() else None
        args = self.args_for(dungeon=self.dungeon.currentData(),
                             floor=self.floor.currentData(), record=record,
                             record_mode="all" if self.record_all.isChecked() else "episodes",
                             pickaxes=self.cfg.pickaxes or None)
        if args.dungeon is None:
            self._say(self.t("Ready. Pick the dungeon you are in, then start."), "attention")
            return
        from .labels import DungeonHints

        self._hints = DungeonHints.load(args.vocab)
        self.table.setRowCount(0)
        self.chests = self.mined = 0
        self._started_at = datetime.now(timezone.utc)
        self._say(self.t("Preparing. This takes a few seconds."))
        self.start.setText(self.t("Stop recording"))
        self.start.setProperty("running", "true")
        self.start.style().unpolish(self.start)
        self.start.style().polish(self.start)
        self._set_setup_enabled(False)
        self.mark.setEnabled(True)
        self.broke.setEnabled(True)
        self.worker = CaptureWorker(self.cfg, args, self)
        self.worker.chest.connect(self._chest)
        self.worker.pickaxe.connect(self._pickaxe)
        self.worker.mining.connect(self._mined)
        self.worker.ready.connect(lambda: self._say(self.t("Recording. Play normally."),
                                                    "recording"))
        self.worker.done.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.start()
        self.timer.start()

    def _set_setup_enabled(self, enabled: bool) -> None:
        for widget in (self.locale, self.dungeon, self.floor, self.fps, self.record,
                       self.record_all, self.pickaxes, self.cal_button, self.upload,
                       self.ui_locale, self.share, self.send_mode):
            widget.setEnabled(enabled)
        if enabled:
            self._refresh_spool()

    def _elapsed(self) -> str:
        if self._started_at is None:
            return "0:00"
        seconds = int((datetime.now(timezone.utc) - self._started_at).total_seconds())
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _add_row(self, at: str, kind: str, what: str, marker: bool = False) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        when = QtWidgets.QTableWidgetItem(at)
        when.setForeground(QtGui.QColor(theme.MUTED))
        when.setFont(theme.data_font())
        source = QtWidgets.QTableWidgetItem(kind)
        source.setForeground(QtGui.QColor(theme.MUTED))
        body = QtWidgets.QTableWidgetItem(what)
        if marker:
            body.setForeground(QtGui.QColor(theme.MUTED))
            font = body.font()
            font.setItalic(True)
            body.setFont(font)
        self.table.setItem(row, 0, when)
        self.table.setItem(row, 1, source)
        self.table.setItem(row, 2, body)
        self.table.scrollToBottom()
        return row

    def _chest(self, event: dict) -> None:
        dive = event.get("dive") or {}
        mining = event.get("provenance") == "mining"
        self.mined += mining
        self.chests += not mining
        items = " · ".join(
            f"{c['item_name']} ×{c['quantity']}" + ("?" if c.get("qty_unknown") else "")
            for c in event.get("contents", []))
        qc = event.get("qc") or {}
        unread = qc.get("panel_lines_unread")
        if unread:
            items += f"   [{unread} unread]"
        row = self._add_row(f"{dive.get('elapsed_seconds', 0)}s",
                            self.t("vein") if mining else self.t("chest"),
                            items or "(nothing)")
        if qc.get("label_conflict") and self._hints is not None:
            self.table.item(row, 2).setForeground(QtGui.QColor(theme.EMBER))
            names = self._hints.conflict_names(dive.get("dungeon_id"), qc)
            if names and all(names):
                self._say(self.t("You chose {chosen}, but this chest's junk comes from "
                                 "{actual}. Check the dungeon.",
                                 chosen=names[0], actual=names[1]), "attention")
        self._refresh_spool()
        # Per record, or once a batch has gathered. Waiting is free: the record is already
        # on disk, so a batch defers the SEND and risks nothing — and one recorded session
        # of 27 events is 27 requests one way and 3 the other.
        if self.cfg.share_uploads:
            if self.cfg.send_mode == SEND_EACH:
                self._upload(quiet=True)
            elif (self.cfg.send_mode == SEND_BATCH
                    and self._waiting() >= self.cfg.send_batch_size):
                self._upload(quiet=True)

    def _mark_dive(self) -> None:
        """A note to yourself, kept out of the data that is sent."""
        self._add_row(self._elapsed(), "", self.t("next dive"), marker=True)
        self.markers.append({
            "event_id": f"marker-{uuid4()}",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provenance": "marker", "note": self.t("next dive"), "contents": [],
            "dive": {"dungeon_id": self.dungeon.currentData(),
                     "elapsed_seconds": int((datetime.now(timezone.utc)
                                             - (self._started_at or datetime.now(timezone.utc))
                                             ).total_seconds())},
        })

    def _pickaxe_broke(self) -> None:
        """The swings since the last break ARE that pickaxe's lifetime — the rate follows
        from counting them, not from assuming one pickaxe per swing."""
        self._pickaxe_lives.append(self._swings_since_break)
        self._swings_since_break = 0
        # The box the player types into is the count, so it is what moves. Two numbers — one
        # entered, one derived — is how a player ends up trusting neither.
        self._spend_a_pickaxe()
        self._add_row(self._elapsed(), "",
                      f"{self.t('A pickaxe broke')} — {self.cfg.pickaxes}", marker=True)
        marker = {
            "event_id": f"marker-{uuid4()}",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provenance": "marker", "note": "pickaxe broke", "contents": [],
            "dive": {"dungeon_id": self.dungeon.currentData()},
        }
        self.markers.append(marker)
        # To the player's own file as well, not only to this window's list. A pickaxe broken
        # is the denominator for everything mining — how many ore that pickaxe cost — and
        # kept in memory it was gone when the window closed, so the count could never be
        # anything but "this session". The event id is a uuid for the same reason every other
        # record has one: two sessions both starting at marker-1 would collide.
        from .uploader import record_marker

        record_marker(marker)
        self._refresh_pickaxes()

    def _spend_a_pickaxe(self) -> None:
        """One fewer, in the config and in the box, without the box saving it back.

        Floored at zero: a player who never told the client how many they carried starts at
        "not sure", and counting downwards from there would invent a stock they never had.
        """
        self.cfg.pickaxes = max(0, int(self.cfg.pickaxes) - 1)
        self.cfg.save()
        self.pickaxes.blockSignals(True)
        self.pickaxes.setValue(self.cfg.pickaxes)
        self.pickaxes.blockSignals(False)

    def _mined(self, left: int) -> None:
        self._swings_since_break += 1
        self._refresh_pickaxes()

    def _pickaxe(self, kind: str, name: str, total: int) -> None:
        from .capture.pickaxe import BROKE

        if kind == BROKE:
            self._pickaxe_broke()
        else:
            self._say(self.t("No pickaxes left — restock in town to keep mining."), "attention")

    def _refresh_stats(self) -> None:
        stats = getattr(self.worker, "stats", None) or {}
        if not stats:
            return
        self.counters.setText(
            f"{self._elapsed()}   {stats.get('frames', 0)} frames   "
            f"{self.chests} {self.t('chest')}   {self.mined} {self.t('vein')}")
        if stats.get("frames", 0) > 200 and stats.get("hud_present", 0) == 0:
            self._say(self.t("The minimap has not been seen. Stop and calibrate again."),
                      "attention")

    def _finished(self, stats: dict) -> None:
        self.timer.stop()
        self.start.setText(self.t("Start recording"))
        self.start.setProperty("running", "false")
        self.start.style().unpolish(self.start)
        self.start.style().polish(self.start)
        self.start.setEnabled(True)
        self.mark.setEnabled(False)
        self.broke.setEnabled(False)
        self._set_setup_enabled(True)
        self._say(self.t("Stopped. {chests} {chest}, {mined} {vein}.",
                         chests=self.chests, chest=self.t("chest"),
                         mined=self.mined, vein=self.t("vein")))
        self._refresh_spool()
        # One last drain, so how the session ended reaches the server now rather than
        # whenever this player happens to record again — which may be never. Not in manual
        # mode: "send when I press Upload" is an instruction, and the ending waits with
        # everything else until they do.
        if self.cfg.share_uploads and self.cfg.send_mode in AUTOMATIC_MODES:
            self._upload(quiet=True)

    def _failed(self, message: str) -> None:
        self.timer.stop()
        self.start.setText(self.t("Start recording"))
        self.start.setEnabled(True)
        self.mark.setEnabled(False)
        self.broke.setEnabled(False)
        self._set_setup_enabled(True)
        self._say(message, "attention")

    def _upload(self, quiet: bool = False) -> None:
        if not self.cfg.share_uploads:
            self._say(self.t("Sharing is off. Turn it on in Settings to send anything."),
                      "attention")
            return
        if self.uploader is not None and self.uploader.isRunning():
            # Chests arrive faster than a slow send completes. Dropping this request would
            # leave that chest — and, at the end of a session, the reason it ended — waiting
            # for whenever the player next records, so it is deferred rather than discarded.
            self._upload_again = True
            return
        self.upload.setEnabled(False)
        self.uploader = UploadWorker(self.cfg, self)
        self.uploader.done.connect(lambda r: (
            None if quiet else self._say(
                self.t("Sent {sent}. {waiting} still waiting.",
                       sent=r["uploaded"], waiting=r["remaining"])),
            self._refresh_spool(), self._upload_deferred(quiet)))
        self.uploader.failed.connect(
            lambda m: (self._say(self.t("Could not send: {why}. It stays on this computer "
                                        "and will be retried.", why=m), "attention"),
                       self._refresh_spool(),
                       setattr(self, "_upload_again", False)))
        self.uploader.start()

    def _upload_deferred(self, quiet: bool) -> None:
        """Run the request that arrived while the last upload was still in the air.

        `done` is emitted from inside the worker's `run`, so the thread can still report
        itself as running for a moment afterwards. Starting here would hit the busy check
        and re-defer a request that now has nothing left to re-trigger it, so this waits
        for the thread to actually be finished.
        """
        if not self._upload_again:
            return
        if self.uploader is not None and self.uploader.isRunning():
            QtCore.QTimer.singleShot(100, lambda: self._upload_deferred(quiet))
            return
        self._upload_again = False
        self._upload(quiet=quiet)

    def _open_data_folder(self) -> None:
        """Open it rather than expect anyone to navigate there.

        %LOCALAPPDATA% is inside AppData, which Explorer hides by default — so a path a
        player cannot reach is the same as no path at all.
        """
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.data)))

    def _export(self) -> None:
        from .export import export_csv

        suggested = str(Path.home() / "wddrop-records.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, self.t("Export my data…"), suggested, "CSV (*.csv)")
        if not path:
            return
        rows = export_csv(records_path(), Path(path), self.markers, spool=spool_path(),
                          names=self._place_names())
        self._say(self.t("Exported {rows} rows to {name}.",
                         rows=rows, name=Path(path).name))

    def showEvent(self, event: QtGui.QShowEvent) -> None:        # noqa: N802 (Qt)
        """Paint the native caption. Only possible once the window has a real handle, which
        is why it is here and not in `__init__`."""
        super().showEvent(event)
        if not self._titlebar_themed:
            self._titlebar_themed = True
            theme.apply_titlebar(self)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:      # noqa: N802 (Qt)
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop("app_closed")
            self.worker.wait(5000)
        event.accept()


def guide_html(t, data: Path) -> str:
    """The player-facing guide, in the window rather than in a markdown file nobody opens.

    EVERY SENTENCE GOES THROUGH `t`. Only the headings and two button names used to, so a
    window running in Chinese still explained itself in English — and this is the page that
    exists precisely for the player who does not already know what to do.

    The bold lead of each rule is a separate string from its explanation, so a translator is
    never asked to reproduce markup inside a sentence.
    """
    shots = data / "walk.png", data / "drop.png"
    pictures = "".join(
        f'<p><img src="{p.as_uri()}" width="420" /></p>' for p in shots if p.exists())
    return f"""
    <div style="color:{theme.INK}; font-size:14px; line-height:1.7;">
      <h2 style="color:{theme.VELLUM};">{t('Play in the tall window')}</h2>
      <p><b>704 × 1241</b> — {t('this is the only size that reads reliably today, and the '
            'client already has a calibration for it. You do not have to do anything.')}</p>
      <p>{t('Other sizes, full screen included, are not recommended yet: they sample the '
            'screen more slowly and some item names are still misread. The client will let '
            'you calibrate and record at one, but expect gaps. More sizes are planned — if '
            'you play at a different one, please say so, because a short recording is what '
            'makes it fixable.')}</p>
      <h2 style="color:{theme.VELLUM};">{t('Calibrate…').rstrip('…')}</h2>
      <p>{t('Only needed at a size that is not listed above. The client takes both '
            'screenshots itself. Press {calibrate} in {settings} and '
            'it asks twice, counting down each time so you can switch back to the game:',
            calibrate=f"<b>{t('Calibrate…')}</b>", settings=t('Settings'))}</p>
      <ol>
        <li>{t('Stand in a dungeon with the minimap visible.')}</li>
        <li>{t('Open a chest and leave the 「獲得了…」 message on screen, then type the '
                'item name.')}</li>
      </ol>
      <p>{t('It refuses to save a profile that cannot read back the frame it was built '
            'from, so if it accepts, it works.')}</p>
      {pictures}
      <h2 style="color:{theme.VELLUM};">{t('While you play')}</h2>
      <ol>
        <li><b>{t('Pick the right dungeon.')}</b>
        {t('It is the one thing this window cannot check for you, and every chest is filed '
           'under it.')}</li>
        <li><b>{t('Chests: let each line finish before advancing.')}</b>
        {t('191 item names truncate into a different valid name, so a half-read line is a '
           'confident wrong answer, not a near miss.')}</li>
        <li><b>{t('Veins: wait for the ▼.')}</b>
        {t('It means the panel has finished and the swing has been recorded. Dismiss before '
           'it appears and that swing is lost.')}</li>
        <li><b>{t('Pickaxes are counted when one breaks.')}</b>
        {t('The client reads the break message itself, so the number beside the pickaxe '
           'follows what the game tells you. Set it when you restock.')}</li>
        <li><b>{t('Stop between chests, not during one.')}</b></li>
      </ol>
      <p style="color:{theme.MUTED};">{t('If something records wrongly, turn on {frames} '
        'and do it again — a recording can be re-read after a fix.',
        frames=f"<b>{t('Keep the frames')}</b>")}</p>
      <h2 style="color:{theme.VELLUM};">{t('Your data')}</h2>
      <p>{t('Everything is kept in one folder on this computer. {settings} shows where, '
            'and opens it for you.', settings=f"<b>{t('Settings')}</b>")}</p>
      <p style="color:{theme.MUTED};">{data}</p>
      <p>{t('Everything this client keeps is in that one folder. Deleting it removes all '
            'of it from this computer; nothing is kept anywhere else.')}</p>
    </div>
    """


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    theme.install_message_filter()
    app = QtWidgets.QApplication(list(argv or []))
    theme.apply_font(app)
    theme.apply_icon(app)
    window = MainWindow(ClientConfig.load())
    window.show()
    return app.exec()

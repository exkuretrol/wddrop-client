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
from .items import droppable
from .config import (AUTOMATIC_MODES, CLIENT_VERSION, SEND_BATCH, SEND_EACH, SEND_MANUAL,
                     ClientConfig, config_dir, data_dir, in_checkout,
                     in_development, program_dir,
                     records_path, spool_path)
from .i18n import LOCALES, NATIVE_NAMES, Translator, system_locale

log = logging.getLogger("wddrop.ui")

# The name a player sees, in the title bar and beside the navigation. Keyed by the English
# form and translated like everything else, because the GAME's own name differs per language
# and a tool named after it has to follow — 「辟邪除妖」 and 「ウィザードリィ ヴァリアンツ ダフネ」
# are the same game, and neither player would recognise the other's title.
#
# 「寶箱」 rather than "drops": it is the game's own word for the thing being counted, used in
# its own text (「打開50次寶箱」), and the client already calls a chest that everywhere else.
APP_NAME = "Wizardry Variants Daphne chest log"

# How wide a control on the settings page is allowed to get. Wide enough for the longest
# menu entry any of the six languages puts in one — 「每 10 筆記錄傳送一次」 and
# "Send each record as it happens" — and no wider.
SETTING_WIDTH = 340

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
                # The window's own size, which in borderless fullscreen is the desktop's
                # rather than the calibration's; `profile_size` is what the frames become.
                expect_size=size or tuple(profile.frame_size),
                profile_size=tuple(profile.frame_size),
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
    """Takes screenshots of the game window after a countdown.

    On its own thread because the countdown is the point: the player has to switch back to
    the game, so the window must keep repainting while it runs. Sleeping on the GUI thread
    would freeze the very countdown it is displaying.

    `count` above one takes a BURST, a moment apart. The walking shot needs it: the frames
    are compared with each other to find the part of the minimap panel that is furniture
    rather than map, and one picture cannot show that. See calibration.choose_hud_region.
    """

    tick = QtCore.Signal(int)
    shot = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, delay: float, path: Path, parent=None, count: int = 1):
        super().__init__(parent)
        self.delay = delay
        self.path = path
        self.count = count

    def run(self) -> None:                     # noqa: D102
        import time

        try:
            from .__main__ import WALK_GAP, _burst_paths, _grab_window

            for remaining in range(int(self.delay), 0, -1):
                self.tick.emit(remaining)
                time.sleep(1)
            for i, where in enumerate(_burst_paths(self.path, self.count)):
                if i:
                    time.sleep(WALK_GAP)
                _grab_window(0).save(where)
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

            from .__main__ import (
                _band_font_candidates, _load_vocab, _prefix_from, _separator_from,
                _suffix_from,
            )
            from .calibration import ProfileStore, fit_hud, fit_message_profile

            vocab, fmt, raw = _load_vocab(self.args)
            # SUFFIX AND SEPARATOR, which the CLI has always passed and this had not. Without
            # them the fit is made against a line half of which no candidate can cover, and
            # the second pass — the one that fits the NAME's own pixels — never runs at all.
            # Measured on a real Japanese shot: pass one alone chose 24px/+1.6 and scored
            # 0.509, which then failed the profile's own self-check and refused to save.
            findings: list[str] = []
            profile = fit_message_profile(
                Image.open(self.drop), self.name, _prefix_from(fmt),
                _band_font_candidates(self.args), droppable(vocab.entries),
                locale=self.args.locale,
                suffix=_suffix_from(fmt), separator=_separator_from(raw),
            )
            if self.walk:
                # THE WHOLE BURST, and the drop shot as the negative — the same three things
                # the command line passes. This worker has already drifted from that path once
                # (it fitted without the suffix and separator, see above), and the cost of
                # drifting here is a template cut from the map interior: a HUD detector that
                # never fires, episodes that never close, and a dive recorded as one chest.
                from .__main__ import WALK_BURST, _burst_paths

                walking = [Image.open(p) for p in _burst_paths(Path(self.walk), WALK_BURST)
                           if p.exists()]
                profile = fit_hud(profile, walking, absent=Image.open(self.drop),
                                  template_path=Path(self.args.data) / "hud_template.png")
                # THE CHECKS THE COMMAND LINE HAS ALWAYS PRINTED. They are the ones that
                # catch a template cut from a wall — the failure that records a whole dive
                # as one chest — and this path did not have them for three versions.
                from .calibration import hud_findings

                findings = hud_findings(profile, walking[0], Image.open(self.drop))
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
            "findings": findings,
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

            from .__main__ import (
                _band_font_candidates, _load_vocab, _prefix_from, _suffix_from,
            )
            from .calibration import propose_item_name

            vocab, fmt, _ = _load_vocab(self.args)
            # The same answer space the runner reads with — see items.droppable. The window
            # offers these as the "which item was it" list too, and offering a name that
            # cannot be recognised would be offering a fit that cannot be checked.
            names = droppable(vocab.entries)
            self.vocabulary.emit(names)
            # BOTH ends of the template. Which one carries the invariant text depends on the
            # locale, and passing only the prefix is why this never filled the box in for a
            # Japanese player — see propose_item_name.
            guess = propose_item_name(
                Image.open(self.drop), _prefix_from(fmt),
                _band_font_candidates(self.args), names, suffix=_suffix_from(fmt))
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
        space_out_markdown(text.document())
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
        self.button.setToolTip(self.t('Go on to the next step.'))
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


class Spoiler(QtWidgets.QLabel):
    """A line of text under a bar until the reader asks for it.

    THE QUESTION IS THE SPOILER. Asking someone which endings they have seen means printing
    the endings, and this window puts that question in front of a player who may be three
    chapters short of any of them — one glance at a dialog they did not open answers "does
    the duke live" for the rest of their game. Nothing about the study needs that to happen:
    a player who has seen an ending recognises it after one click, and a player who has not
    only needs to know there is nothing of theirs to tick.

    Painted over rather than blanked, so the row keeps its size and the list does not jump
    when a bar lifts. The bar covers the whole label, which also hides how LONG the sentence
    is — a short one and a long one are different guesses.

    Pointer-only by design: the bars are an obstacle put in the reader's way, and the master
    checkbox above them is the keyboard path that lifts all of them at once.
    """

    # True while the bar is up. Whoever owns the row decides what that means for the rest of
    # it — here, a tick box that cannot be ticked until its question has been read.
    covered_changed = QtCore.Signal(bool)

    def __init__(self, text, hint, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self._covered = True
        self._hint = hint
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(hint)

    def covered(self) -> bool:
        return self._covered

    def set_covered(self, covered: bool) -> None:
        if covered == self._covered:
            return
        self._covered = covered
        # The hand and the tooltip are the invitation to click. Once it is read there is
        # nothing left to click for, and a hand cursor over ordinary text is a lie.
        if covered:
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setToolTip(self._hint)
        else:
            self.unsetCursor()
            self.setToolTip("")
        self.update()
        self.covered_changed.emit(covered)

    def mouseReleaseEvent(self, event) -> None:                  # noqa: N802 (Qt)
        self.set_covered(False)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:                         # noqa: N802 (Qt)
        if not self._covered:
            super().paintEvent(event)
            return
        # The text is NOT drawn and then hidden — it is not drawn at all. Painting it under
        # a fill would leave it in the widget's own pixels, one screenshot away.
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(theme.RULE))


class ProgressDialog(QtWidgets.QDialog):
    """Which story endings this player has seen.

    THE ONE THING THIS STUDY CANNOT READ. Most dungeons change with how far through the story
    a player is — how strong the enemies are, which groups turn up at all, what some quests
    pay. Two players on the same floor can be in different games, and nothing on screen says
    so, so the only way to know is to ask.

    Asked as ENDINGS rather than as a number, because a number is not a thing anyone has ever
    been shown. Grouped by dungeon and worded as what the player SAW — the duke lived, the
    villagers came — because that is what someone remembers a year later. Nothing here names
    a chapter number, an internal id, or which ending the game calls "good": one of the
    endings that counts arrives with the curse lifted and reinforcements on the way, and a
    player asked to sort that into "bad" would answer wrongly and mean well.
    """

    def __init__(self, t, cfg, parent=None):
        super().__init__(parent)
        self.t, self.cfg = t, cfg
        self.setWindowTitle(t("How far are you?"))
        self.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(t(
            "Some dungeons get harder as the story goes on, and that changes what drops. "
            "Tick anything you have seen — it is only ever used to compare like with like, "
            "and you can change it later in Settings."))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        from .dungeons import DUNGEONS
        from .progress import ENDINGS, GRADE_FLOOR, decode, grade_name, released_grades

        def category(text: str) -> None:
            """A heading for one KIND of progress. Two of them, because the two things asked
            here are not the same shape: a rung climbed, and a set of things seen."""
            label = QtWidgets.QLabel(text)
            label.setObjectName("wordmark")
            layout.addSpacing(10)
            layout.addWidget(label)

        # FIRST, because it is the shorter question and the one every player can answer
        # without thinking back: a single rung, and it caps the whole party's level.
        category(t("Your main character's grade"))
        grade_hint = QtWidgets.QLabel(t("The highest promotion exam you have passed. It "
                                        "sets how far your party can level."))
        grade_hint.setObjectName("hint")
        grade_hint.setWordWrap(True)
        layout.addWidget(grade_hint)
        self.grade = Combo()
        for entry in released_grades():
            # The game's own word for it, in the window's language — not one of ours.
            self.grade.addItem(grade_name(entry.id, t.locale), entry.id)
        # NO "not sure" ENTRY. The bottom rung is the game's own starting state — every
        # player is 無階 until they pass the first exam — so an unanswered player and a
        # player who has passed nothing are the same person, and offering both would be
        # asking someone to distinguish between two names for where they already are.
        # Nothing is stored until they press Save; pressing it with this untouched is an
        # answer, not a default.
        at = self.grade.findData(cfg.character_grade if cfg.character_grade is not None
                                 else GRADE_FLOOR)
        self.grade.setCurrentIndex(max(0, at))
        layout.addWidget(self.grade)

        category(t("Main story"))
        already = decode(cfg.progress_bits, cfg.progress_width)
        # COVERED, because the question is itself the spoiler — see `Spoiler`. The dungeon
        # names stay in the open: they are in the picker on the first page and name a place,
        # not what happens there.
        story_hint = QtWidgets.QLabel(t("Each line says how a chapter ends, so they start "
                                        "covered. Click one to read it."))
        story_hint.setObjectName("hint")
        story_hint.setWordWrap(True)
        layout.addWidget(story_hint)
        self.reveal_all = QtWidgets.QCheckBox(t("Show the endings"))
        self.reveal_all.setToolTip(t("Uncover all of them at once. Only do this if you have "
                                     "finished the story, or do not mind knowing how it goes."))
        self.reveal_all.toggled.connect(lambda shown: self._cover_story(not shown))
        layout.addWidget(self.reveal_all)

        self.boxes = {}
        self.spoilers = {}
        seen_chapters = []
        for ending in ENDINGS:
            if ending.dungeon_id not in seen_chapters:
                seen_chapters.append(ending.dungeon_id)
                names = DUNGEONS.get(ending.dungeon_id) or {}
                # The dungeon's own name in the window's language — the same word the picker
                # uses, so the question names a place the player has been rather than a
                # chapter number the game never says out loud. Plain, not a heading: the two
                # CATEGORIES are the headings, and a chapter is one step below.
                title = QtWidgets.QLabel(names.get(t.locale) or names.get("ja") or "")
                layout.addSpacing(4)
                layout.addWidget(title)
            # The tick and the words are two widgets now: the words are what gets covered,
            # and the box waits on them. A box that could be ticked under its own bar is a
            # question answered without being read, and this study would rather have no
            # answer than an invented one.
            box = QtWidgets.QCheckBox("")
            box.setChecked(bool(already.get(ending.key)))
            spoiler = Spoiler(t(ending.label),
                              t("Covered so it cannot spoil the story. Click to read it."))
            spoiler.covered_changed.connect(
                lambda covered, b=box: b.setEnabled(not covered))
            # ALREADY TICKED MEANS ALREADY SEEN. Hiding a player's own answer from them is
            # friction with nothing behind it: they told us they watched this happen — and
            # leaving it covered would also leave it locked, so an answer could never be
            # taken back.
            if already.get(ending.key):
                spoiler.set_covered(False)
            else:
                box.setEnabled(False)
            self.boxes[ending.key] = box
            self.spoilers[ending.key] = spoiler
            line = QtWidgets.QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            line.addWidget(box)
            # NO trailing stretch: the label takes the rest of the row, so every bar is the
            # same length. Bars cut to their own sentence would print the shape of what they
            # cover — a two-word ending and a fifteen-word one are different guesses.
            line.addWidget(spoiler, 1)
            layout.addLayout(line)

        layout.addSpacing(6)
        row = QtWidgets.QHBoxLayout()
        later = QtWidgets.QPushButton(t("Not now"))
        later.setToolTip(t("Close this without answering. It will not ask again for a while."))
        later.clicked.connect(self.reject)
        row.addWidget(later)
        row.addStretch(1)
        save = QtWidgets.QPushButton(t("Save"))
        save.setObjectName("primary")
        save.setToolTip(t("Keep these answers. You can change them in Settings at any time."))
        save.clicked.connect(self.accept)
        row.addWidget(save)
        self._buttons = (later, save)
        layout.addLayout(row)

    def showEvent(self, event: QtGui.QShowEvent) -> None:        # noqa: N802 (Qt)
        """Two things that can only be done once the window is real.

        THE CAPTION. The style sheet reaches inside a dialog on its own — it is set on the
        main window and this is its child — but the title bar is drawn by Windows and is not
        Qt's to style. Without this the question arrives as the one white-capped,
        round-cornered window in a dark square program, which is how a dialog looks when it
        belongs to something else.

        THE BUTTON WIDTHS. One width for both, from whichever word is longer in the language
        the window happens to be in — the theme's own note on `#primary` says what the pair
        is meant to look like: the same size, told apart by colour. Measured HERE and not in
        `__init__` because a button's size hint changes when the sheet is applied to it: at
        build time the pair measured 80 and 60, after polishing 86 and 60, so equalising
        early set both to 80 and then let only one of them grow. A fixed number instead would
        be a number that is wrong in five of the six languages.
        """
        super().showEvent(event)
        theme.apply_titlebar(self)
        widest = max(button.sizeHint().width() for button in self._buttons)
        for button in self._buttons:
            button.setMinimumWidth(widest)

    def _cover_story(self, covered: bool) -> None:
        """Put the bars back up — except over a row the player has already ticked.

        The same rule the dialog opens with: your own answers are not spoilers to you. It
        matters on the way BACK, too — a ticked row that is covered is also locked, so
        blanket re-covering would leave an answer that could not be taken back until its
        bar was lifted a second time.
        """
        for key, spoiler in self.spoilers.items():
            spoiler.set_covered(covered and not self.boxes[key].isChecked())

    def answer(self) -> dict:
        return {key: box.isChecked() for key, box in self.boxes.items()}

    def grade_answer(self):
        """The chosen grade id, or None for "not sure" — which is not grade 1."""
        return self.grade.currentData()


class SeeingDialog(QtWidgets.QDialog):
    """A live picture of what the client can see, beside where it is looking.

    THIS IS FOR THE FAILURES THAT DO NOT LOOK LIKE FAILURES. A HUD template that is a
    photograph of a rock face is a perfectly good crop of a wall; it scores merely low,
    episodes quietly never close, and four chests are recorded as one. A band that capture
    does not grab reads nothing at all while sitting in exactly the right place. Neither is
    visible in a log, and both are obvious the moment the regions are drawn.

    Two views, because they answer different questions:

        where it looks   the regions outlined on the real frame
        what it gets     the strips, and black everywhere else — which is the picture that
                         shows a region MISSING rather than misplaced

    Deliberately not an overlay on top of the game. That would mean a window over a process
    protected by anti-cheat, and this needs nothing the game can see: the frames are the ones
    capture already takes.
    """

    def __init__(self, args, parent=None, t=None):
        super().__init__(parent)
        self.args = args
        self.t = t or Translator(None)
        self.setWindowTitle(self.t("What the client sees"))
        self.resize(760, 560)
        layout = QtWidgets.QVBoxLayout(self)

        self.view = QtWidgets.QLabel()
        self.view.setAlignment(QtCore.Qt.AlignCenter)
        self.view.setMinimumHeight(360)
        layout.addWidget(self.view, 1)

        self.note = QtWidgets.QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        row = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem(self.t("Where it looks"), "annotate")
        self.mode.addItem(self.t("What it gets"), "captured")
        self.mode.currentIndexChanged.connect(self._draw)
        row.addWidget(self.mode)
        row.addStretch(1)
        close = QtWidgets.QPushButton(self.t("Close"))
        close.setToolTip(self.t('Close this window. Nothing is lost.'))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

        self._frame = None
        self._profile = None
        # Slower than capture on purpose. This is for looking at, and a preview competing
        # with the capture loop for the same window would be a debugging aid that changes
        # what it is showing.
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._grab)
        self._timer.start(500)

    def showEvent(self, event: QtGui.QShowEvent) -> None:        # noqa: N802 (Qt)
        # Same reason as every other dialog here: the caption colour, the square corners and
        # the border are DWM attributes on a real window handle, not anything Qt draws.
        super().showEvent(event)
        theme.apply_titlebar(self)
        self._grab()

    def _grab(self) -> None:
        """One whole frame, not the strips — the point is to see what the strips LEAVE OUT."""
        from .__main__ import _live_size, _select_profile
        from .capture.source import open_source

        try:
            size = _live_size(self.args)
            frame = next(open_source(self.args.source, fps=1).frames()).image
        except Exception as exc:                       # noqa: BLE001
            self._frame = None
            self.note.setText(self.t("No game window yet: {why}", why=str(exc)))
            self.view.clear()
            return
        try:
            self._profile = _select_profile(self.args, size)
        except SystemExit as exc:
            # NOT `except Exception`. `_select_profile` says "no calibration for this size"
            # by raising SystemExit — it is written for the command line, where that IS the
            # message — and SystemExit is not an Exception, so it went straight through this
            # handler and out of a Qt slot. The window closed on a player who pressed a
            # button to be told something.
            self._frame = None
            self.note.setText(self.t(
                "No calibration for {size} yet. Calibrate at this size, then look again.",
                size=f"{size[0]}x{size[1]}" if size else "?"))
            self.view.clear()
            return
        self._frame = frame
        self._draw()

    def _draw(self) -> None:
        if self._frame is None or self._profile is None:
            return
        from .preview import annotate, as_capture_sees, named_regions

        shown = (annotate if self.mode.currentData() == "annotate" else as_capture_sees)(
            self._frame, self._profile)
        data = shown.convert("RGB").tobytes()
        image = QtGui.QImage(data, shown.size[0], shown.size[1],
                             shown.size[0] * 3, QtGui.QImage.Format_RGB888)
        self.view.setPixmap(QtGui.QPixmap.fromImage(image).scaled(
            self.view.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        named = ", ".join(name for name, _box in named_regions(self._profile))
        self.note.setText(self.t(
            "{size} — regions: {regions}. Everything outside them is black to the client.",
            size=f"{self._frame.size[0]}x{self._frame.size[1]}", regions=named))


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
        # WHICH STEP WE ARE ON, held outright rather than inferred from the two paths above.
        # Inferring it cannot express the third state: the walk shot was SKIPPED, which is an
        # offered choice and leaves `walk` empty exactly as never having taken it does. So
        # skipping advanced the wording to step 2 and nothing else — the next capture was
        # taken as the walk shot again, saved over walk.png, and the dialog looped there
        # forever with its button still reading Capture.
        self._walk_done = False
        self._working = False
        self.result: dict | None = None
        self._worker = None

        layout = QtWidgets.QVBoxLayout(self)
        self.step = QtWidgets.QLabel(self.t(
            "Step 1 of 2 — walk around a dungeon with the minimap visible, then press "
            "Capture.\nYou will have a few seconds to switch back — keep walking while it "
            "takes the shots."))
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
        self.skip.setToolTip(self.t('Carry on without this picture. The step it was for is left unfitted.'))
        self.skip.clicked.connect(self._skip)
        row.addWidget(self.skip)
        # THE SHOT FROM LAST TIME, when there is one. Calibration is re-run for reasons that
        # have nothing to do with the pictures — a fit refused, a size changed, a reader
        # improved — and re-taking them means going back into the game, standing in a
        # dungeon and opening another chest each time. The files are already here.
        self.load = QtWidgets.QPushButton(self.t("Use the saved shot"))
        self.load.setToolTip(self.t('Use the picture already on disk from an earlier calibration instead of taking a new one.'))
        self.load.clicked.connect(self._use_saved)
        row.addWidget(self.load)
        row.addStretch(1)
        self.action = QtWidgets.QPushButton(self.t("Capture"))
        self.action.setToolTip(self.t('Take the picture now. Set the game up first — this reads whatever is on screen.'))
        self.action.clicked.connect(self._capture)
        row.addWidget(self.action)
        layout.addLayout(row)

    def showEvent(self, event: QtGui.QShowEvent) -> None:        # noqa: N802 (Qt)
        super().showEvent(event)
        theme.apply_titlebar(self)

    # -- steps --------------------------------------------------------------------
    def _busy(self, working: bool) -> None:
        """Hold the dialog still while a thread is using it.

        The close button too: Qt will happily delete the widget a worker is about to signal,
        and the crash that follows arrives after the dialog is gone, where nothing connects
        it to the calibration the player was in the middle of.
        """
        for widget in (self.action, self.skip, self.load):
            widget.setEnabled(not working)
        # THE NAME BOX TOO, while the guess is running. It is filled in by the reader when it
        # finishes, and a player typing into it meanwhile is either about to have their
        # answer kept (and wonder why the box stopped accepting) or about to lose it. Locked
        # and then given the focus back, so the box is live exactly when it is theirs.
        self.name.setEnabled(not working)
        if not working and self.name.isVisible():
            self.name.setFocus()
        self._working = working

    def closeEvent(self, event) -> None:                # noqa: D102 (Qt override)
        if getattr(self, "_working", False):
            event.ignore()
            return
        super().closeEvent(event)

    def _shot_target(self) -> str:
        """Which shot the next Capture takes. Named so the step machine can be tested."""
        return str(Path(self.args.data) / ("walk.png" if not self._walk_done else "drop.png"))

    def _capture(self) -> None:
        from .__main__ import WALK_BURST

        path = Path(self._shot_target())
        self.action.setEnabled(False)
        self.skip.setEnabled(False)
        # The walking step is a burst; the drop message is one frame and must be, since it is
        # the line being read and the player is holding it on screen.
        self.load.setEnabled(False)
        self._worker = ShotWorker(self.args.delay, path, self,
                                  count=1 if self._walk_done else WALK_BURST)
        self._worker.tick.connect(
            lambda n: self.status.setText(self.t("switching back to the game… {n}", n=n)))
        self._worker.shot.connect(self._got_shot)
        self._worker.failed.connect(self._error)
        self._worker.start()

    def _shot_problem(self, path: str) -> str | None:
        """What is wrong with this screenshot, in the words the command line uses."""
        from PIL import Image

        from .__main__ import _drop_shot_problem, _walk_shot_problem

        check = _walk_shot_problem if not self._walk_done else _drop_shot_problem
        try:
            with Image.open(path) as shot:
                return check(shot.convert("L"))
        except Exception as exc:                       # noqa: BLE001
            return str(exc)

    def _use_saved(self) -> None:
        """Take this step's shot from disk instead of from the game.

        The same file the guided capture would have written, so everything downstream is
        unchanged — including the walking BURST, which the fit picks up beside walk.png when
        it is there.
        """
        path = Path(self._shot_target())
        if not path.exists():
            self.status.setText(self.t("No saved shot here yet — capture one."))
            return
        try:
            from PIL import Image

            with Image.open(path) as shot:
                size = f"{shot.size[0]}x{shot.size[1]}"
        except Exception:                              # noqa: BLE001
            size = "?"
        # SAID OUT LOUD, because a saved shot can be from another resolution and the fit it
        # produces belongs to THAT one, not to the window open now.
        self.status.setText(self.t("using the saved {name} ({size})",
                                   name=path.name, size=size))
        self._got_shot(str(path))

    def _skip(self) -> None:
        if not self._walk_done:
            # Skipping the walk shot means no HUD template, and without one episodes never
            # close on the HUD returning — they fall back to the idle timeout, which does
            # not bracket chests. Say so plainly instead of failing later.
            self.status.setText(self.t(
                "No HUD template will be made — chest bracketing will be poor."))
            self._walk_done = True
            self._advance_to_drop()
        else:
            self.reject()

    def _got_shot(self, path: str) -> None:
        self.action.setEnabled(True)
        self.skip.setEnabled(True)
        self.load.setEnabled(True)
        pix = QtGui.QPixmap(path)
        self.preview.setPixmap(pix.scaled(480, 160, QtCore.Qt.KeepAspectRatio,
                                          QtCore.Qt.SmoothTransformation))
        # LOOKED AT BEFORE IT IS USED, as the command line has always done: it refuses a
        # drop shot with no message on it and offers another go. Here it is a warning rather
        # than a refusal — the player can see the preview and decide — but saying nothing
        # turns "there was no message in that screenshot" into a fit that fails minutes
        # later with a number instead of a reason.
        problem = self._shot_problem(path)
        if problem:
            self.status.setText(f"[!] {problem}")
        if not self._walk_done:
            self.walk = Path(path)
            self._walk_done = True
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
            # LOCKED WHILE IT READS. The read takes tens of seconds, and everything it could
            # be interrupted by leaves the dialog in a state it cannot recover: skipping
            # starts a second shot while the first is still being read, closing tears the
            # window out from under a running thread, and fitting would run against a name
            # the box has not been filled in with yet.
            self._busy(True)
            self._reader = ReadWorker(self.args, self.drop, self)
            self._reader.vocabulary.connect(self._offer_names)
            self._reader.read.connect(self._proposed)
            self._reader.blank.connect(
                lambda: self.status.setText(self.t("Could not read it — please type it.")))
            self._reader.read.connect(lambda *_: self._busy(False))
            self._reader.blank.connect(lambda: self._busy(False))
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
            "Step 2 of 2 — open a chest and leave the 「…を手に入れた!!」 message on screen, then "
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
        # EVERYTHING THAT IS WRONG WITH IT, said here rather than discovered in a session.
        # A fit can pass its own check and still be built on a pair of shots that cannot
        # bracket a chest between them.
        trouble = list(result.get("findings") or [])
        if notes.get("name_ends_at") is None:
            trouble.append(self.t(
                "that message wrapped onto a second line, so the fit had less to go on. If "
                "readings look poor later, calibrate again on a chest whose whole sentence "
                "fits on one row."))
        if trouble:
            self.status.setText(self.status.text() + "\n"
                                + "\n".join(f"[!] {line}" for line in trouble))
        self.action.setText(self.t("Done"))
        self.action.setEnabled(True)
        self.action.clicked.disconnect()
        self.action.clicked.connect(self.accept)

    def _error(self, message: str) -> None:
        self.action.setEnabled(True)
        self.skip.setEnabled(True)
        self.status.setText(f"[!] {message}")


class UpdateWorker(QtCore.QThread):
    """Asks GitHub whether there is a newer client. On a thread, and silent about failure.

    Emits nothing at all unless there is something to say — no signal for "you are current",
    because there is no message for it either. Any failure is the same as being current from
    the window's point of view: this is the least important thing the program does, and it
    must never be the reason a player is looking at a spinner.
    """

    found = QtCore.Signal(str, str)

    def __init__(self, cfg: ClientConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self) -> None:                     # noqa: D102
        try:
            from .updates import check

            update = check(self.cfg)
        except Exception:                              # noqa: BLE001
            return
        if update is not None:
            self.found.emit(update.version, update.page)


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


def _frame_note(line: dict) -> str:
    """`[episode-211/f_00109.png]` after a reading, in a checkout only.

    Every recognised line already carries the frame it was read from — the CLI prints it and
    the record carries it as `source_frame`. The window did not show it, so the one question
    a recording exists to answer ("which frame was that, then?") meant opening the spool by
    hand and matching timestamps.

    Behind `in_checkout` rather than `in_development`, like the frame counter: the released
    exe carries the development marker on purpose, and a player has no use for a filename
    inside a folder they were never asked to look in.
    """
    src = line.get("source_frame")
    return f"  [{src}]" if src and in_checkout() else ""


def mmss(seconds) -> str:
    """MM:SS, everywhere a duration is shown.

    The window had two formats: the live counter read "5:07" while the ledger beside it read
    "36s" for the same kind of quantity. Two ways of writing one thing is a thing to work out
    rather than read, and the ledger's was the raw field off the record.

    Zero-padded minutes so the column does not jitter as it crosses ten, and hours are folded
    into the minutes rather than adding a third field — a dive that reaches 90:00 is better
    read as ninety minutes than as 1:30:00 in a row of MM:SS.
    """
    total = max(0, int(seconds or 0))
    return f"{total // 60:02d}:{total % 60:02d}"


class RoomyRows(QtWidgets.QStyledItemDelegate):
    """Gives a dropdown's rows the height they had before the style changed.

    Squaring the popups meant drawing the whole window with a plain style, and a plain style
    sizes list rows its own way: measured on the same dungeon picker, rows went from 43px to
    25px — square corners, and a list too tight to pick from comfortably.

    The style sheet cannot fix it. `::item { padding }` and `min-height` are both ignored
    here — measured across five combinations, every one of them still produced a 25px row —
    because the row height comes from the delegate's size hint, which is where this is.

    Derived from the FONT rather than fixed at 43, so it survives a player whose display
    scales differently or whose fonts are larger than this machine's.
    """

    # Chosen against the old build: 17px of font plus this is the 43px rows had before.
    PAD = 26
    # A rule and the air either side of it. Left at the style's own row height it was a 32px
    # hole between two dungeons, which reads as the list having lost an entry.
    SEPARATOR_HEIGHT = 11

    @staticmethod
    def _is_separator(index) -> bool:
        return index.data(QtCore.Qt.AccessibleDescriptionRole) == "separator"

    def sizeHint(self, option, index):                 # noqa: N802 (Qt)
        size = super().sizeHint(option, index)
        if self._is_separator(index):
            size.setHeight(self.SEPARATOR_HEIGHT)
            return size
        size.setHeight(option.fontMetrics.height() + self.PAD)
        return size

    def paint(self, painter, option, index) -> None:   # noqa: N802 (Qt)
        """Draw the rule, because the style sheet cannot.

        `QComboBox QAbstractItemView::separator` is honoured for a menu and ignored here —
        the popup is a list view, and its separators are items the delegate paints. Set in
        the sheet it produced a gap and no line, which is the same as no separator at all.
        """
        if not self._is_separator(index):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setPen(QtGui.QColor(theme.RULE))
        middle = option.rect.center().y()
        painter.drawLine(option.rect.left() + 10, middle, option.rect.right() - 10, middle)
        painter.restore()


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
        """Open the list — square, roomy, and below the control.

        Three things have to be true, and each is fixed somewhere different:

          * SQUARE. Windows decides a window's corners as it is shown, so the attribute is
            set BEFORE and after: a list that is rebuilt gets a new window, which makes
            every showing the first one, and `winId()` forces the handle to exist so there
            is something to set it on. What the STYLE paints inside is `theme.apply_style`.
          * BELOW, and with no container chrome around it. Also the style — see
            `theme.apply_style`, which answers one hint differently for exactly this.
          * ROOMY. The plain style sizes rows at 32px where this window's own reads at 43,
            so the height comes from a delegate. Attached here rather than at build time
            because asking for the view CONSTRUCTS the popup container, and doing that for
            every dropdown at startup builds lists nobody has asked to see.

        Nothing caps the height any more: with the popup a list rather than a menu, Qt
        honours `maxVisibleItems` again and arrives at ten rows on its own.
        """
        view = self.view()
        if not getattr(self, "_roomy", False):
            self._roomy = True
            view.setItemDelegate(RoomyRows(view))
        popup = view.window()
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


class AtlasWorker(QtCore.QThread):
    """Builds the glyph atlas off the GUI thread.

    Five seconds on a real machine — short enough to be tempting to do inline, long enough
    that the window would stop repainting while it happened, which reads as a hang on the
    one screen a new player is looking at.
    """

    done = QtCore.Signal(str)                  # "" on success, otherwise what went wrong

    def __init__(self, locale: str, parent=None):
        super().__init__(parent)
        self.locale = locale

    def run(self) -> None:                     # noqa: D102 (QThread entry point)
        try:
            import json

            from .atlas import build
            from .config import config_dir
            from . import gamefont
            from .gamefont import game_fonts

            vocab = find_data("vocab.{locale}.json", self.locale)
            if vocab is None:
                self.done.emit("no vocabulary")
                return
            fonts = game_fonts()
            if not fonts:
                self.done.emit("game not found")
                return
            words = json.loads(Path(vocab).read_text(encoding="utf-8"))
            build(fonts[0], words, config_dir(), self.locale, fallbacks=fonts[1:])
            # TWO atlases, because the game draws the two surfaces this client reads in two
            # different faces: the mining panel in BaseFont, the drop message band in
            # ScenarioFont. One atlas was built and used for both, so every chest line was
            # matched against the wrong typeface — see __main__._band_source.
            scenario = next((f for f in fonts if "ScenarioFont" in Path(f).name), None)
            if scenario is not None:
                build(scenario, words, config_dir(), self.locale,
                      fallbacks=[f for f in fonts if f != scenario],
                      stem=f"{self.locale}.scenario")
            # The faces have served their purpose the moment the atlases exist. Kept only
            # until here so a rebuild does not need the game running; see discard_cache.
            gamefont.discard_cache()
            self.done.emit("")
        except Exception as exc:               # noqa: BLE001 — reported, never a crash
            log.exception("wddrop: building the atlas failed")
            self.done.emit(str(exc))


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
        self._asked_about_updates = False
        self._update_page = None
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

        # SHORT, because of where it is shown. A taskbar button is a few characters wide and
        # alt-tab is not much better, so the full name — 「辟邪除妖 Variants Daphne 寶箱紀錄工具」
        # — arrives there as 「辟邪除…」, which identifies nothing. A short distinctive word
        # survives the truncation. The full name is in the ribbon, where there is room for it.
        # Belt and braces. `main()` sets it before anything exists, which is the right
        # place — but a window also gets built by the frozen self-check and by the tests,
        # and a dropdown that is square in one and round in the other is the kind of
        # difference that makes a screenshot in a bug report untrustworthy.
        theme.apply_style(QtWidgets.QApplication.instance())
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
        # The wordmark, BESIDE the navigation rather than stacked above it. There was none
        # here for a while, on the grounds that the title bar says what this is — but the
        # title bar said `wddrop 0.3.0`, which is the name of a folder, not of a thing a
        # player recognises. Now the title bar carries the real name for the taskbar, and
        # this carries it where the eye is; on one row, so it costs no height and cannot
        # read as the same sentence printed twice.
        wordmark = QtWidgets.QLabel(self.t(APP_NAME))
        wordmark.setObjectName("wordmark")
        top.addWidget(wordmark)
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
        # Hidden until there IS one. A permanent "no updates" line is noise that teaches the
        # player to stop reading the row the state line lives in.
        self.update_link = QtWidgets.QLabel("")
        self.update_link.setObjectName("meta")
        self.update_link.setOpenExternalLinks(True)
        self.update_link.setVisible(False)
        rl.addWidget(self.update_link)
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

        # THE TABLE AND ITS EMPTY STATE OCCUPY THE SAME SPACE, one shown at a time. An empty
        # table is four fifths of this window saying nothing — and saying it in the shape of
        # a ledger with ruled headings, which reads as "recording, and nothing found" rather
        # than as "not recording yet". The two states are not the same claim and should not
        # look alike.
        self.empty = QtWidgets.QLabel("")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(QtCore.Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.records = QtWidgets.QStackedWidget()
        self.records.addWidget(self.empty)
        self.records.addWidget(self.table)
        layout.addWidget(self.records, 1)

        foot = QtWidgets.QFrame()
        foot.setObjectName("footer")
        fl = QtWidgets.QHBoxLayout(foot)
        fl.setContentsMargins(20, 14, 20, 14)
        fl.setSpacing(10)
        self.start = QtWidgets.QPushButton(self.t("Start recording"))
        self.start.setToolTip(self.t('Begin reading the game window. Chests and veins are recorded as they happen.'))
        self.start.setObjectName("primary")
        self.start.clicked.connect(self._toggle)
        fl.addWidget(self.start)
        self.mark = QtWidgets.QPushButton(self.t("Mark next dive"))
        self.mark.setToolTip(self.t('Say that the next chest belongs to a new dive, when the client cannot see the change itself.'))
        self.mark.clicked.connect(self._mark_dive)
        self.mark.setEnabled(False)
        fl.addWidget(self.mark)
        fl.addStretch(1)
        self.spool_label = QtWidgets.QLabel("")
        self.spool_label.setObjectName("hint")
        fl.addWidget(self.spool_label)
        self.upload = QtWidgets.QPushButton(self.t("Upload"))
        self.upload.setToolTip(self.t('Send what is waiting to the study. Nothing leaves this computer until you press it.'))
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
        # The page's one loud thing. It was set at the same size as the caveats under it, so
        # the count a player came to read and the note about timezone boundaries carried
        # equal weight.
        self.stats_headline.setObjectName("headline")
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

        # THE OTHER SCALE, and it ranks differently. How MUCH of a thing came out and how
        # OFTEN it came out are two different questions, and for anything that drops in a
        # stack the totals answer neither honestly: one lucky vein of 300 ore outranks a
        # thing that turned up in every second chest, and the page would call the first the
        # bigger finding. Both numbers are already counted per row — `quantity` and
        # `openings` — so this chooses which one the column, the bars and the ranking use.
        #
        # A dropdown rather than a second column: the shares and the bars can only be OF one
        # of them, and two sets of bars in one table is a picture that compares nothing.
        self.stats_scale = Combo()
        self.stats_scale.setMinimumWidth(140)
        self._no_wheel(self.stats_scale)
        for label, value in ((self.t("By amount"), "quantity"),
                             (self.t("By times"), "openings")):
            self.stats_scale.addItem(label, value)
        self.stats_scale.currentIndexChanged.connect(self._refresh_stats_page)
        pick.addWidget(self.stats_scale)

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
        refresh.setToolTip(self.t('Re-read your own records and redraw this page.'))
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

    def _build_atlas(self) -> None:
        """Make the glyph atlas from the game's own font, once.

        Started from `_refresh_setup`, which runs whenever the page is shown — so the guard
        matters: without it, a build already running would be started again on every refresh.

        ONCE PER LOCALE PER SESSION, and that second guard is not belt-and-braces. A
        successful build calls `_refresh_setup` again, which asks the same question that
        started it; if the build could not produce everything asked for — the game shipping
        only one face, so no scenario atlas is written — the answer is still "missing" and it
        would rebuild forever, in a thread, silently. Trying once and living with what came
        out is the behaviour that terminates.
        """
        if getattr(self, "_atlas_worker", None) is not None:
            return
        tried = getattr(self, "_atlas_tried", None)
        if tried is None:
            tried = self._atlas_tried = set()
        if self.cfg.locale in tried:
            return
        tried.add(self.cfg.locale)
        self._say(self.t("Reading the game's own font, so text can be recognised. This "
                         "happens once."), "attention")
        self._atlas_worker = AtlasWorker(self.cfg.locale, self)
        self._atlas_worker.done.connect(self._atlas_built)
        self._atlas_worker.start()

    def _atlas_built(self, problem: str) -> None:
        self._atlas_worker = None
        if not problem:
            self._say(self.t("Ready."))
            self._refresh_setup()
            return
        if problem == "game not found":
            # Not an error to shout about: a player may be on a machine that never had the
            # game. Say what it needs and what to do, in one sentence.
            self._say(self.t("The game was not found on this computer, so its font could "
                             "not be read. Install it, or build the atlas yourself."),
                      "attention")
        else:
            self._say(self.t("The glyph atlas could not be built: {why}", why=problem),
                      "attention")

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

    @staticmethod
    def _tally(number: int, times: bool) -> str:
        """A cell in the number column, in whichever scale the page is showing.

        `×N` is the game's own way of writing an amount, and it is kept for amounts. A COUNT
        of openings is not an amount and must not borrow that mark — `×12` under a column
        headed "times" reads as twelve of something. The bare number does not, and the
        header is right above it saying which of the two it is; that also keeps the column
        free of a unit word that is a different length in each of the six languages.
        """
        return str(number) if times else f"×{number}"

    def _grouped(self, rows):
        """(heading, rows) in the order they are shown, or one unlabelled group.

        Money first because it is the smaller list and the one a player scans past, not
        because it is the bigger number — sorting the groups by quantity would put currency
        on top for exactly the reason it should not be mixed in.

        A single group loses its heading: a table of items under a heading that says "items"
        is a heading that tells nobody anything.
        """
        from .items import CURRENCY, ItemCategories

        categories = ItemCategories()
        buckets = {}
        for row in rows:
            buckets.setdefault(categories.of(row), []).append(row)
        if len(buckets) < 2:
            return [("", rows)] if rows else []
        order = [(CURRENCY, self.t("Currency")), ("item", self.t("Items"))]
        return [(label, buckets[key]) for key, label in order if buckets.get(key)]

    def _item_names(self):
        """What to call an item in the language the WINDOW is in.

        The game is in Japanese because this client asked it to be, so what was recorded is
        Japanese. Showing that to someone who set the interface to Chinese answers in a
        language they did not choose. Loaded once and kept: it is a few thousand strings and
        the page redraws on every refresh.
        """
        if getattr(self, "_names_table", None) is None:
            from .items import ItemNames

            locale = self.t.locale if hasattr(self.t, "locale") else (self.cfg.ui_locale or "")
            self._names_table = ItemNames.load(
                find_data(ItemNames.FILENAME, locale or self.cfg.locale))
        return self._names_table

    def _refresh_stats_page(self) -> None:
        from .stats import summarise

        chosen = self.stats_day.currentData()
        data = summarise(records_path(), spool_path(), day=chosen,
                         source=self.stats_source.currentData())
        self._fill_days(data, chosen)
        names = self._place_names()

        overall = data["overall"]
        # Only when it says something the headline does not. Looking at every day, this line
        # repeated the tally above it word for word — 「2 次開啟 · 2 條道具」 twice, once
        # under the other.
        # Only what the headline does not already say. Looking at every day the two lines
        # were the same tally printed twice, one under the other; what all-time adds THERE is
        # how many days it took, which the headline has no room for.
        showing_everything = self.stats_day.currentData() is None
        self.stats_overall.setText(
            self.t("{days} days recorded", days=len(data["days"])) if showing_everything
            else self.t("all time: {openings} openings · {lines} item lines · {days} days",
                        openings=overall["openings"], lines=overall["lines"],
                        days=len(data["days"])))

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

        # WHICH NUMBER THIS PAGE IS ABOUT — see the picker for why it is a choice. Every
        # total, share, bar and the ranking itself follow it, because a page that ranked by
        # one and drew bars from the other would be two answers in one picture.
        measure = self.stats_scale.currentData() or "quantity"
        times = measure == "openings"
        self.stats_table.horizontalHeaderItem(1).setText(
            self.t("times") if times else self.t("total"))

        # RE-SORTED HERE, not in stats.py. `summarise` ranks by amount because that is the
        # order everything else uses; the scale is a property of this view, and sorting a
        # copy of the list is cheaper than a summary that has to be asked twice.
        rows = data["by_item"]
        if times:
            rows = sorted(rows, key=lambda r: (-r["openings"], -r["quantity"], r["item"]))
        # GROUPED, because money is not a drop. ゴールド and Gil come out of chests in
        # amounts nothing else reaches, so a single ranked list is one currency row and then
        # everything a player actually wants to see, pushed under it. See items.CURRENCY_IDS
        # for why the two of them and not the coins.
        groups = self._grouped(rows)
        # CLEARED, not just resized. Shrinking a table leaves the cells that were there —
        # switching from every source to veins alone left the previous row's share and bar
        # sitting on the TOTAL line, which read as a total having a percentage of itself.
        self.stats_table.clearContents()
        # A TOTAL row at the end, as the game's own tally screen has: a column of shares is
        # not readable without the number they are shares OF.
        headings = len(groups) if len(groups) > 1 else 0
        self.stats_table.setRowCount(len(rows) + headings + (1 if rows else 0))
        index = 0
        for heading, group in groups:
            if headings:
                label = QtWidgets.QTableWidgetItem(heading)
                label.setForeground(QtGui.QColor(theme.VELLUM))
                subtotal = QtWidgets.QTableWidgetItem(
                    self._tally(sum(r[measure] for r in group), times))
                subtotal.setForeground(QtGui.QColor(theme.VELLUM))
                subtotal.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.stats_table.setItem(index, 0, label)
                self.stats_table.setItem(index, 1, subtotal)
                index += 1
            # SHARES WITHIN THE GROUP, and bars against the group's own top row. A share of
            # everything would say the same thing the heading already does, and a bar scaled
            # to a currency total leaves every item as a stripe too short to compare.
            within = sum(r[measure] for r in group) or 1
            tallest = max((r[measure] for r in group), default=0) or 1
            for row in group:
                shown = self._item_names().display(row)
                cells = (("   " if headings else "") + shown,
                         self._tally(row[measure], times),
                         f"{row[measure] / within * 100:.1f}%", "")
                for column, value in enumerate(cells):
                    cell = QtWidgets.QTableWidgetItem(value)
                    if column:
                        cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    if column == 3:
                        cell.setData(QtCore.Qt.UserRole, row[measure] / tallest)
                        # The OTHER number, where the column is not showing it. A row that is
                        # tall because of one lucky stack and a row that is tall because it
                        # turns up constantly look identical until both are in reach.
                        cell.setToolTip(
                            self.t("×{n} in total", n=row["quantity"]) if times
                            else self.t("{n} openings gave this", n=row["openings"]))
                    if column == 0 and shown != row["item"]:
                        # What was actually on screen, kept within reach: the localised name
                        # is a convenience, and the reading is the evidence.
                        cell.setToolTip(row["item"])
                    self.stats_table.setItem(index, column, cell)
                index += 1
        if rows:
            total = QtWidgets.QTableWidgetItem(
                self.t("total of {n} kinds", n=len(rows)))
            total.setForeground(QtGui.QColor(theme.VELLUM))
            summed = (sum(r["openings"] for r in rows) if times
                      else data["total_quantity"])
            amount = QtWidgets.QTableWidgetItem(self._tally(summed, times))
            if times:
                # SAID, because the number invites the wrong reading. This is the column
                # added up — the shares above are shares of it — but one opening yields
                # several kinds, so it counts that opening once per kind and lands well above
                # the number of openings in the headline. Two numbers that look like they
                # should agree and do not is worse than one number.
                amount.setToolTip(self.t(
                    "The column added up. One opening usually gives several kinds, so it "
                    "counts once under each of them — this is larger than the number of "
                    "openings above."))
            amount.setForeground(QtGui.QColor(theme.VELLUM))
            amount.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.stats_table.setItem(index, 0, total)
            self.stats_table.setItem(index, 1, amount)

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
            # A CONTROL is as wide as what it holds, not as wide as the window: stretched to
            # the full 780px, 「日本語」 sat alone in a box wide enough for a paragraph, and the
            # page read as a column of empty troughs.
            #
            # Only a control, though. Capping whatever a caller passes also caught the row
            # that shows where a player's data lives — a folder path and a button, squeezed
            # into 340px, so the path wrapped onto a second line. Which widgets want the
            # narrow treatment is a property of what they ARE, not of who added them, so it
            # is decided here rather than at each call.
            if isinstance(widget, (QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox,
                                   QtWidgets.QLineEdit)):
                widget.setMaximumWidth(SETTING_WIDTH)
            if isinstance(widget, QtWidgets.QPushButton):
                # A BUTTON IS AS WIDE AS ITS LABEL. Stretched across the row it reads as a
                # banner rather than something to press, and two buttons on the same page end
                # up different widths for no reason a player could name. Same rule as the
                # controls above, and applied here so the next button added does not have to
                # remember it — the first two did not, and only one of them worked around it.
                line = QtWidgets.QHBoxLayout()
                line.setContentsMargins(0, 0, 0, 0)
                line.addWidget(widget)
                line.addStretch(1)
                box.addLayout(line)
            else:
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


        self.data_label = QtWidgets.QLabel()
        self.data_label.setWordWrap(True)
        self.data_label.setObjectName("hint")
        # The button is DEVELOPMENT ONLY. The client ships the fits it has been tested at and
        # reads at those; a calibration made on a player's machine is a claim nobody has
        # checked against a recording, and the one made here was fitted against the wrong
        # typeface for three versions without anyone being able to tell. The label stays in
        # every build, because which sizes are ready is worth knowing; the offer to make
        # another one is for us. See config.in_development.
        self.cal_label = QtWidgets.QLabel()
        self.cal_label.setWordWrap(True)
        self.cal_button = None
        if in_development():
            cal = QtWidgets.QHBoxLayout()
            cal.addWidget(self.cal_label, 1)
            self.cal_button = QtWidgets.QPushButton(self.t("Calibrate…"))
            self.cal_button.setToolTip(self.t('Teach the client to read a window size it does not already know. Existing calibrations are kept.'))
            self.cal_button.clicked.connect(self._calibrate)
            cal.addWidget(self.cal_button)
            # Beside calibration because it is how you tell whether a calibration is any
            # good. The measured case: at 1920x1080 the region search preferred a rock face
            # to the minimap, and the stored HUD template was a photograph of a wall — a
            # thing no score reported and one glance would have.
            self.seeing_button = QtWidgets.QPushButton(self.t("See it…"))
            self.seeing_button.setToolTip(self.t('Draw what the client is looking at on top of the game, so you can check it is reading the right places.'))
            self.seeing_button.clicked.connect(self._see)
            cal.addWidget(self.seeing_button)
            holder = QtWidgets.QWidget()
            holder.setLayout(cal)
            form.addRow(self.t("Calibrate…").rstrip("…"), holder)
        else:
            form.addRow(self.t("Calibrate…").rstrip("…"), self.cal_label)
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
        open_folder.setToolTip(self.t('Open the folder holding everything this client has kept.'))
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
        # Restored, and saved on every change, like the rest of this page. Set BEFORE the
        # signals are connected so restoring a state is not itself a change to be written.
        self.record.setChecked(self.cfg.keep_frames)
        self.record_all.setChecked(self.cfg.keep_all_frames)
        self.record_all.setEnabled(self.cfg.keep_frames)
        self.record.toggled.connect(self.record_all.setEnabled)
        self.record.toggled.connect(self._record_changed)
        self.record_all.toggled.connect(self._record_changed)
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

        # Beside "keep the frames" because it is the same kind of thing and answers the same
        # question from the other side: the frames say what was on screen, the log says what
        # this client made of it. A miss needs both, and neither can be turned on afterwards.
        # Beside the log because both are about this program rather than about the game, and
        # this is the only place a player can stop the one request that goes anywhere other
        # than the study's own server.
        self.updates = QtWidgets.QCheckBox(self.t("Tell me when a new version is out"))
        self.updates.setChecked(self.cfg.check_updates)
        self.updates.toggled.connect(self._updates_changed)
        updates_box = QtWidgets.QVBoxLayout()
        updates_box.setSpacing(3)
        updates_box.addWidget(self.updates)
        note = QtWidgets.QLabel(self.t(
            "Asks GitHub once, when the window opens, whether a newer client exists. It "
            "sends nothing about you or your game."))
        note.setObjectName("hint")
        note.setWordWrap(True)
        updates_box.addWidget(note)
        holder = QtWidgets.QWidget()
        holder.setLayout(updates_box)
        form.addRow(self.t("New versions"), holder)

        self.trace = QtWidgets.QCheckBox(self.t("Write a detailed log"))
        self.trace.setChecked(self.cfg.trace)
        self.trace.toggled.connect(self._trace_changed)
        trace_box = QtWidgets.QVBoxLayout()
        trace_box.setSpacing(3)
        trace_box.addWidget(self.trace)
        self.trace_note = QtWidgets.QLabel()
        self.trace_note.setObjectName("hint")
        self.trace_note.setWordWrap(True)
        self.trace_note.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        trace_box.addWidget(self.trace_note)
        holder = QtWidgets.QWidget()
        holder.setLayout(trace_box)
        form.addRow(self.t("Detailed log"), holder)
        self._show_log_path()

        # THE STORY QUESTION, where a player goes looking for what this program knows about
        # them. The prompt is a one-off; this is the copy that is always reachable.
        self.progress_button = QtWidgets.QPushButton(self.t("My story progress…"))
        self.progress_button.setToolTip(self.t(
            "Say which story endings you have seen. It changes what drops, and nothing on "
            "screen shows it."))
        self.progress_button.clicked.connect(self._edit_progress)
        row(self.t("Story progress"), self.progress_button,
            self.t("Some dungeons get harder as the story goes on. Recording how far you "
                   "are is what lets your records be compared with other players' fairly."))

        # ITS OWN ROW, with its own title. It shared the answer's row, under the heading
        # "Story progress" — so a control that decides how often a POP-UP appears sat with
        # no name of its own beside the button that edits the answer, and read as a second
        # thing that changed the answer. Every other control on this page is one titled row.
        # Combo, not QComboBox: the plain one leaves its dropdown to the compositor, which
        # rounds it — the only soft corners in a window of hard rules.
        self.progress_interval = Combo()
        for label, days in ((self.t("Every 2 weeks"), 14), (self.t("Monthly"), 30),
                            (self.t("Never ask"), 0)):
            self.progress_interval.addItem(label, days)
        at = self.progress_interval.findData(int(self.cfg.progress_interval_days or 0))
        self.progress_interval.setCurrentIndex(max(0, at))
        self.progress_interval.currentIndexChanged.connect(self._progress_interval_changed)
        row(self.t("How often to ask about story progress"), self.progress_interval,
            self.t("How long to leave it before the question comes back on its own. It "
                   "stops asking once you have answered everything it knows about, and "
                   "you can always open it yourself above."))

        # WHO MADE THIS, at the foot of the page rather than beside the game's name in the
        # ribbon. It was in the header, where it sat next to a title nobody could mistake for
        # anything else and repeated itself on every screen; here it is stated once, where a
        # player looking for what this program is goes anyway.
        self.disclaimer_button = QtWidgets.QPushButton(self.t("Read the disclaimer"))
        self.disclaimer_button.setToolTip(self.t(
            "The terms you agreed to, in full. Reading it again changes nothing."))
        self.disclaimer_button.clicked.connect(self._show_disclaimer)
        row(self.t("Disclaimer"), self.disclaimer_button)

        about = QtWidgets.QLabel(self.t(
            "A fan-made tool. It is not made by, endorsed by, or connected to the makers of "
            "the game."))
        about.setObjectName("hint")
        about.setWordWrap(True)
        form.addRow(self.t("About"), about)

        self._no_wheel(self.send_mode, self.ui_locale, self.fps, self.progress_interval)

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
        export.setToolTip(self.t('Write your records to a CSV file you choose, to keep or to look at elsewhere.'))
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
    def _dress(self, dialog) -> None:
        """Give a dialog the window's own sheet and icon.

        A child dialog INHERITS the parent's style sheet, and that is enough right up until
        it is not: the sheet is set on the window rather than on the application, so anything
        that reaches the dialog by another route — a platform that treats a dialog as its own
        top-level, a window built with styling disabled — gets Qt's defaults instead, and the
        result is one plain grey window among dark ones. Copying it is a line, and it removes
        the question.
        """
        sheet = self.styleSheet()
        if sheet:
            dialog.setStyleSheet(sheet)
        icon = self.windowIcon()
        if not icon.isNull():
            dialog.setWindowIcon(icon)

    def _show_disclaimer(self) -> None:
        """The terms, readable again without re-asking anything.

        DELIBERATELY NOT the consent page. That page exists to take an answer, and sending a
        player back to it to re-read something would put their existing agreement back in
        play — including the sharing choice, which is answered on it. This only shows the
        text.
        """
        dialog = QtWidgets.QDialog(self)
        self._dress(dialog)
        dialog.setWindowTitle(self.t("Disclaimer"))
        dialog.resize(720, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        # NO MARGINS AROUND THE TEXT. They would inset the scrollbar as surely as padding
        # does; the buttons below get their own instead.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        view = QtWidgets.QTextBrowser()
        view.setMarkdown(self._disclaimer_text())
        space_out_markdown(view.document())
        flush_scrollbar(view, inset=22)
        view.setOpenExternalLinks(True)
        layout.addWidget(view, 1)
        close = QtWidgets.QPushButton(self.t("Close"))
        close.setToolTip(self.t("Close this window. Nothing is lost."))
        close.clicked.connect(dialog.accept)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(18, 12, 18, 14)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        # Shown before the frame is dressed: the attributes are set on a real window handle,
        # and `winId()` on a dialog that has never been shown is not one.
        dialog.show()
        theme.apply_titlebar(dialog)
        dialog.exec()

    @staticmethod
    def _disclaimer_text() -> str:
        from .consent import disclaimer_text

        return disclaimer_text()

    def _edit_progress(self) -> None:
        """The same dialog the prompt uses. One place to change the answer, so a player who
        finished a chapter last night does not have to wait to be asked."""
        from . import progress

        dialog = ProgressDialog(self.t, self.cfg, self)
        self._dress(dialog)
        if dialog.exec():
            progress.remember(self.cfg, dialog.answer(), grade=dialog.grade_answer())

    def _progress_interval_changed(self) -> None:
        self.cfg.progress_interval_days = int(self.progress_interval.currentData() or 0)
        self.cfg.save()

    def _build_guide(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        view = QtWidgets.QTextBrowser()
        view.setOpenExternalLinks(False)
        # Edge to edge, with the inset given back as a document margin — see
        # flush_scrollbar. The theme's scrollbar rules travel with it: a widget carrying its
        # own stylesheet stops inheriting the application's.
        flush_scrollbar(view)
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
        self._relaunch_in_the_new_language()

    def _relaunch_in_the_new_language(self) -> None:
        """Rebuild the window, because every string in it was translated on the way in.

        `self.t` is bound once at construction and each label was given its text then, so
        changing the language changed the SETTING and nothing a player could see — they had
        to restart to find out it had worked. Qt's usual answer is a retranslate pass, which
        means every widget growing a second place where its text lives; a window this size is
        cheaper to build again than to keep in step.

        NOT while recording. A capture owns a worker thread and a spool, and tearing its
        window down mid-session to change a language is not a trade worth offering. The
        setting is saved either way, so it applies the moment the session ends.
        """
        if self.worker is not None and self.worker.isRunning():
            self._say(self.t("The language changes when this recording stops."), "attention")
            return
        fresh = MainWindow(self.cfg, data=self.data)
        fresh.setGeometry(self.geometry())
        fresh.show()
        # Kept alive by the application, not by us: dropping the reference here would collect
        # the new window along with the old one.
        QtWidgets.QApplication.instance().setProperty("wddrop_window", fresh)
        fresh._show_page(3)                            # stay on Settings, where they are
        self.close()

    def _pickaxes_changed(self, value: int) -> None:
        self.cfg.pickaxes = int(value)
        self.cfg.save()
        # THE RUNNER IS TOLD TOO, when one is running. Restocking happens mid-dive — that is
        # what a town trip in the middle of a mining session IS — and the runner keeps its own
        # copy to count breaks down from, so correcting the box while recording would
        # otherwise be undone by the next break.
        runner = getattr(self.worker, "runner", None) if self.worker is not None else None
        if runner is not None and getattr(runner, "pickaxes_left", None) is not None:
            runner.pickaxes_left = int(value)
        self._refresh_pickaxes()

    def _record_changed(self, _checked: bool) -> None:
        self.cfg.keep_frames = self.record.isChecked()
        self.cfg.keep_all_frames = self.record_all.isChecked()
        self.cfg.save()

    def _updates_changed(self, checked: bool) -> None:
        """Off means the request is not made, not that its answer is hidden."""
        self.cfg.check_updates = bool(checked)
        self.cfg.save()

    def _trace_changed(self, checked: bool) -> None:
        """Takes effect NOW, not at the next launch.

        A player is asked to turn this on because something has just gone wrong, and they
        will try to reproduce it in the same sitting. A setting that needs a restart to
        begin recording detail would miss exactly that attempt.
        """
        from . import logs

        self.cfg.trace = checked
        self.cfg.save()
        logs.configure(trace=checked)
        self._show_log_path()

    def _show_log_path(self) -> None:
        from . import logs

        self.trace_note.setText(self.t(
            "Records what the client did while it read the screen, so a miss can be "
            "explained afterwards. Written to {path}. Nothing is uploaded.",
            path=str(logs.log_path())))

    def args_for(self, **extra) -> SimpleNamespace:
        """The same argument object the CLI builds, so both drive identical code."""
        locale = self.cfg.locale      # Japanese, fixed — see config.ClientConfig
        vocab = find_data("vocab.{locale}.json", locale)
        atlas = find_data("atlas.{locale}.json", locale)
        # The message band is drawn in a different face from the mining panel, so it reads
        # against a different atlas. Named outright rather than derived, because `fonts`
        # here is already an override (the locale's atlas beats the profile's) and one
        # override must not silently decide the other. See __main__._band_source.
        scenario = find_data("atlas.{locale}.scenario.json", locale)
        base = dict(
            data=str(self.data), locale=locale,
            vocab=str(vocab) if vocab else f"vocab.{locale}.json",
            fonts=str(atlas) if atlas else None,
            band_fonts=str(scenario) if scenario else None,
            # From the vocabulary, which carries it per locale ("開ける" in ja), not hardcoded
            # here — this said "打開" while asking the player to run the game in Japanese, so
            # it was searching every line for a string that could not appear. Harmless only
            # because the prompt renders in the action-button area rather than the message
            # band, so it is never seen there in ANY language (see episodes.py).
            open_prompt=None, record=None, record_mode="episodes", pickaxes=None,
            dungeon=None, floor=None, fps=self.fps.value(), delay=4.0, source="window",
        )
        base.update(extra)
        return SimpleNamespace(**base)

    def _refresh_setup(self) -> None:
        locale = self.cfg.locale
        found = {
            "vocabulary": find_data("vocab.{locale}.json", locale),
            "atlas": find_data("atlas.{locale}.json", locale),
            # In the WINDOW's language, not the game's. The other two are read against
            # pixels and must match the language the game is drawing; a dungeon list is
            # read by a person. Loading it with the rest put 「北穿の幽霊城」 in the picker
            # and in the tallies of a client whose interface is Chinese — the ids are the
            # same in every file, so only the words the player reads were wrong.
            "catalogue": (find_data("catalog.{locale}.json", self.t.locale)
                          or find_data("catalog.{locale}.json", locale)),
        }
        # The catalogue is OPTIONAL — the dungeon list is built in, and a file only overrides
        # it. The other two are not: without them nothing can be recognised at all.
        missing = [name for name, path in found.items()
                   if path is None and name != "catalogue"]
        # The atlas is not shipped: it is the game's own typeface, and the client builds it
        # here from the copy the player already has. Missing it is the ORDINARY state of a
        # fresh install, so it is built rather than reported.
        #
        # The SCENARIO atlas counts as missing too. A player upgrading from a build that made
        # only one atlas has a complete-looking install that is still reading every chest
        # line against the mining panel's typeface — the exact defect this replaced. Falling
        # back keeps them working, so this is a rebuild rather than a warning; but it has to
        # actually happen, and nothing else would ever trigger it.
        stale = found["atlas"] is not None and find_data(
            "atlas.{locale}.scenario.json", locale) is None
        if found["vocabulary"] is not None and (found["atlas"] is None or stale):
            self._build_atlas()
        # WHAT is loaded, not WHERE from. Three absolute paths in a monospace block was the
        # build machine's folder layout printed into a player's settings page — it answered
        # a question only a developer asks, and it answered it in the middle of the page. A
        # player's question is "does it have what it needs", and the folder button below is
        # already how they get to the files.
        # Counted over what the client NEEDS, which is two files. The catalogue is not one
        # of them any more — the dungeon list is built into the client, and a file only
        # overrides it — so counting it made a complete install report "2 of 3" and look
        # like something had gone missing.
        needed = [name for name in found if name != "catalogue"]
        self.data_label.setText(
            self.t("{n} of {total} data files loaded",
                   n=sum(1 for name in needed if found[name]), total=len(needed))
            + (f" — {self.t('missing')}: {', '.join(missing)}" if missing else ""))
        self._load_catalog(found["catalogue"])

        from .calibration import ProfileStore

        # The SHIPPED calibrations count. Without this the window asked a player to
        # calibrate a resolution the client already had a tested fit for, and refused to
        # start until they did — which is what a fresh install looked like: the fits were
        # inside the exe and only the command line ever consulted them.
        store = ProfileStore.load(self.data)
        shipped = ProfileStore.shipped(self.cfg.locale)
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

    def _scales(self, dungeon_id) -> bool | None:
        """Whether this dungeon changes with the player's story progress.

        None when the catalogue does not say — an older file, or a dungeon whose placements
        were never measured. See progress.should_ask: unknown means ask, because a question
        nobody needed costs one dismissal and a question never asked costs the covariate.
        """
        for entry in self._catalog or ():
            if entry.get("id") == dungeon_id:
                return entry.get("scales")
        return None

    def _maybe_ask_progress(self, dungeon_id) -> None:
        """Put the story question, if this is the moment for it.

        HERE rather than at first run: the first thing a player does is get the window
        working, and a questionnaire before their first recorded chest is where people give
        up. Picking a dungeon that scales is the first moment the answer means anything.
        """
        from . import progress

        if dungeon_id is None or not progress.should_ask(self.cfg, self._scales(dungeon_id)):
            return
        if getattr(self, "_progress_dialog", None) is not None:
            return                                  # already up; do not stack prompts
        # OPENED, NOT EXEC'D. This runs inside the picker's own signal handler, and `exec`
        # starts a nested event loop there — the window stops responding to everything else
        # until the dialog is answered, and anything driving the window without a person in
        # front of it waits forever. `open` shows it and returns; the answer arrives on
        # `finished`.
        dialog = ProgressDialog(self.t, self.cfg, self)
        self._dress(dialog)
        self._progress_dialog = dialog
        dialog.finished.connect(lambda code, d=dialog: self._progress_answered(code, d))
        dialog.open()

    def _progress_answered(self, code, dialog) -> None:
        from . import progress

        self._progress_dialog = None
        if code:
            progress.remember(self.cfg, dialog.answer(), grade=dialog.grade_answer())
        else:
            # A dismissal costs exactly what an answer costs. Otherwise it reappears next
            # session, and a prompt that reappears is one people learn to click away.
            progress.mark_asked(self.cfg)
        dialog.deleteLater()

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

        # In the WINDOW's language, like the file it stands in for. The table carries a name
        # per language for exactly this — a picker that lists 「北穿幽靈城」 to someone reading
        # a Japanese interface is asking them to recognise a place by a word they have not
        # seen in their game.
        self._catalog = built_in(self.t.locale)
        if path is not None:
            # Tolerated, not trusted. The built-in list is what makes the picker work at all,
            # and a catalogue that is missing, half-written or not JSON must not be able to
            # empty it — an empty picker blocks recording entirely.
            try:
                self._catalog = json.loads(path.read_text(encoding="utf-8")).get(
                    "dungeons", self._catalog)
            except (OSError, ValueError) as exc:
                log.warning("wddrop: ignoring the dungeon list at %s (%s)", path, exc)
        # A rule between each group of dungeons, and NO heading over them. The leading digit
        # of an id is the group — 7015 and 7001 are both 7 — and the game has no word for
        # that grouping, so a label would be one we invented. A rule says "these belong
        # together" without claiming to know what they are called.
        previous = None
        for d in self._catalog:
            group = int(d["id"]) // 1000
            if previous is not None and group != previous:
                self.dungeon.insertSeparator(self.dungeon.count())
            previous = group
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
        # The empty page tells the player what to do NEXT, and what that is has just
        # changed: picking a dungeon turns "choose one" into "press start".
        if self.table.rowCount() == 0:
            self._show_empty()
        if chosen != self.cfg.dungeon_id:
            self.cfg.dungeon_id = chosen
            self.cfg.save()
            self._maybe_ask_progress(chosen)
        self.floor.clear()
        self.floor.addItem(self.t("not sure"), None)
        # BY ID, not by position. The picker's rows and the catalogue stopped lining up the
        # moment separators went between the groups, and a positional lookup then reads the
        # floors of whichever dungeon happens to sit that many rows down.
        for entry in self._catalog:
            if entry["id"] == chosen:
                for f in entry.get("floors", []):
                    self.floor.addItem(f["name"], f["id"])
                break
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

    def _see(self) -> None:
        SeeingDialog(self.args_for(), self, t=self.t).exec()

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
        self._show_empty()
        self.chests = self.mined = 0
        self._started_at = datetime.now(timezone.utc)
        self._say(self.t("Preparing. This takes a few seconds."))
        self.start.setText(self.t("Stop recording"))
        self.start.setProperty("running", "true")
        self.start.style().unpolish(self.start)
        self.start.style().polish(self.start)
        self._set_setup_enabled(False)
        self.mark.setEnabled(True)
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
        """Lock the things that describe the RUN while it is running.

        The pickaxe count is not one of them. It is a fact about the player's bag rather than
        a setting for the session — they restock in town mid-dive, and they notice a wrong
        count precisely when they watch it tick down — so it stays editable throughout, and
        `_pickaxes_changed` carries the correction into the runner.
        """
        for widget in (self.dungeon, self.floor, self.fps, self.record,
                       self.record_all, self.upload,
                       self.ui_locale, self.share, self.send_mode, self.cal_button):
            if widget is not None:                     # the calibrate button is dev-only
                widget.setEnabled(enabled)
        if enabled:
            self._refresh_spool()

    def _elapsed(self) -> str:
        if self._started_at is None:
            return mmss(0)
        return mmss((datetime.now(timezone.utc) - self._started_at).total_seconds())

    def _show_empty(self) -> None:
        """What the page says before anything has been recorded.

        Three states, three different sentences, because they call for three different
        things from the player: choose a dungeon, press start, or go and open a chest. One
        blank ledger for all three would leave them guessing which.
        """
        if self.worker is not None and self.worker.isRunning():
            text = self.t("Recording. Open a chest or work a vein and it will appear here.")
        elif self.dungeon.currentData() is None:
            text = self.t("Choose the dungeon you are in, above.")
        else:
            text = self.t("Ready when you are — press {start}.",
                          start=self.t("Start recording"))
        self.empty.setText(text)
        self.records.setCurrentWidget(self.empty)

    def _add_row(self, at: str, kind: str, what: str, marker: bool = False) -> int:
        row = self.table.rowCount()
        self.records.setCurrentWidget(self.table)
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
        # Named in the language the WINDOW is in, not the one the GAME is in. The client asks
        # for a Japanese game, so every reading is Japanese — and this line is what a player
        # watches while they dive, so it was the one place the interface answered in a
        # language they had not chosen. The stats page already went through the table; this
        # printed `item_name` straight from the record.
        names = self._item_names()
        items = " · ".join(
            f"{names.display(c)} ×{c['quantity']}" + ("?" if c.get("qty_unknown") else "")
            + _frame_note(c)
            for c in event.get("contents", []))
        qc = event.get("qc") or {}
        unread = qc.get("panel_lines_unread")
        if unread:
            items += f"   [{unread} unread]"
        row = self._add_row(mmss(dive.get("elapsed_seconds", 0)),
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
        # THE FRAME COUNT IS OURS, NOT THE PLAYER'S. It answers "is the loop sampling",
        # which is a question about this program rather than about their dive, and it sits
        # in the one line the record page uses to say whether anything is working.
        #
        # Gated on the CHECKOUT, not on `in_development`: the released exe carries the
        # development marker on purpose, so that gate showed this to everyone who downloaded
        # it — reported, reasonably, as a debug number shipping to players.
        counted = (f"{stats.get('frames', 0)} frames   " if in_checkout() else "")
        self.counters.setText(
            f"{self._elapsed()}   {counted}"
            f"{self.chests} {self.t('chest')}   {self.mined} {self.t('vein')}")
        capped = stats.get("record_capped")
        if capped and not getattr(self, "_said_capped", False):
            self._said_capped = True
            self._say(self.t("Kept {n} frames — that is the limit, so no more pictures are "
                             "being saved. Drops are still being recorded.", n=f"{capped:,}"),
                      "attention")
        # NO "the minimap has not been seen" warning. It fired after 200 frames without the
        # HUD, which is an ordinary thing to spend: a player restocking in town, reading a
        # menu, or sitting in a cutscene sees nothing else for far longer than ten seconds.
        # And its advice was wrong — the calibration it told them to redo is one that shipped
        # with the client and was tested against real recordings. The runner still logs the
        # same observation for a bug report; it is not worth interrupting a dive for.

    def _finished(self, stats: dict) -> None:
        self.timer.stop()
        self.start.setText(self.t("Start recording"))
        self.start.setProperty("running", "false")
        self.start.style().unpolish(self.start)
        self.start.style().polish(self.start)
        self.start.setEnabled(True)
        self.mark.setEnabled(False)
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
            self._say_blocked(r["blocked"], r["remaining"]) if r.get("blocked") else (
                None if quiet else self._say(
                    self.t("Sent {sent}. {waiting} still waiting.",
                           sent=r["uploaded"], waiting=r["remaining"]))),
            self._refresh_spool(), self._upload_deferred(quiet)))
        self.uploader.failed.connect(
            lambda m: (self._say(self.t("Could not send: {why}. It stays on this computer "
                                        "and will be retried.", why=m), "attention"),
                       self._refresh_spool(),
                       setattr(self, "_upload_again", False)))
        self.uploader.start()

    def _say_blocked(self, blocked: dict, waiting: int) -> None:
        """The server will not take records from this build. Say so, and say what happens.

        SAID EVEN WHEN THE UPLOAD WAS QUIET. Every other outcome here can be left unsaid
        because it resolves itself — a failed send retries, a slow one finishes. This one
        never does: the waiting count stops falling and stays stopped until the player does
        something, and a number that does not move with no reason beside it was already
        reported once as broken detection.

        The two facts that matter are which version to get and that nothing was lost. A
        player told only "rejected" has every reason to assume the second one is false.
        """
        latest = blocked.get("latest_version") or blocked.get("min_version") or ""
        self._say(self.t(
            "This version can no longer send records — update to {version}. Nothing was "
            "lost: {waiting} record(s) are kept here and will send once you update.",
            version=latest, waiting=waiting), "attention")

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
        # ONCE A LAUNCH, AND ONLY AFTER THE WINDOW IS UP. A network call in __init__ delays
        # the one thing the player is waiting for, to answer a question that can wait.
        if not self._asked_about_updates:
            self._asked_about_updates = True
            self._update_worker = UpdateWorker(self.cfg, self)
            self._update_worker.found.connect(self._say_update)
            self._update_worker.start()

    def _say_update(self, version: str, page: str) -> None:
        """A newer client exists. Say so where the player already looks, and link it.

        NOT a modal. They opened this to record a dive, and a dialogue in front of that is a
        thing to dismiss before doing what they came for. The state line is where this
        window says everything else that is true right now.
        """
        self._update_page = page
        self._say(self.t("Version {version} is out — this is {running}.",
                         version=version, running=CLIENT_VERSION), "attention")
        self.update_link.setText(
            f'<a href="{page}">{self.t("Get it")}</a>')
        self.update_link.setVisible(True)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:      # noqa: N802 (Qt)
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop("app_closed")
            self.worker.wait(5000)
        # The atlas build too. A thread still running while the interpreter tears down dies
        # as "can't register atexit after shutdown" — a hard crash with no message, which is
        # what a player closing the window during the first-run build would have seen.
        atlas = getattr(self, "_atlas_worker", None)
        if atlas is not None and atlas.isRunning():
            atlas.wait(10000)
        event.accept()


# Where a player can read about the window-sizing tool, written by whoever wrote it. Linked
# rather than bundled and rather than described second-hand: it is someone else's work, it
# resizes a window and touches nothing in the game, and a player deciding whether to run a
# stranger's executable deserves the author's own page rather than our summary of it.
WINDOW_TOOL_URL = "https://forum.gamer.com.tw/C.php?bsn=70180&snA=3240"


# HOW FAR APART MARKDOWN SITS, once Qt has rendered it.
#
# `setMarkdown` builds the document directly rather than through the HTML parser, so a
# stylesheet does not reach it — `setDefaultStyleSheet` is for `setHtml` and silently does
# nothing here. What is left is the document itself, so the spacing is applied per block
# after the fact.
#
# It matters more than a nicety for this particular page: the disclaimer is the one thing a
# player is asked to READ before they agree to it, and Qt's own defaults pack the lines
# tightly enough that most people will scroll past instead. A wall of text that is legally
# sufficient and practically unread is not consent worth having.
MARKDOWN_LINE_HEIGHT = 145          # percent
MARKDOWN_PARAGRAPH_GAP = 10         # px below every paragraph
MARKDOWN_HEADING_GAP = 18           # px above a heading, so sections separate
# Qt renders headings at the document's font size unless told otherwise. Roughly the usual
# 1.5 / 1.3 / 1.15 scale, so a heading reads as one.
MARKDOWN_HEADING_SCALE = {1: 1.5, 2: 1.3, 3: 1.15}


def flush_scrollbar(view, inset: int = 24) -> None:
    """Put the scrollbar against the window's edge, and the TEXT where the padding was.

    A text view is normally inset by its own padding and by its page's margins, so its
    scrollbar floats in a channel with a strip of background either side of it — the only
    scrollbar in the window not touching an edge. Padding cannot fix that: the scrollbar is
    inside the widget, so anything that insets the text insets the bar with it.

    So the widget goes edge to edge, its padding drops to nothing, and the same distance is
    given back as the DOCUMENT's margin, which moves the text and leaves the bar alone.
    """
    view.setViewportMargins(0, 0, 0, 0)
    view.document().setDocumentMargin(inset)
    view.setStyleSheet("QTextBrowser { border: none; padding: 0; margin: 0; }"
                       + theme.scrollbar())


def space_out_markdown(document, base_point_size: float | None = None) -> None:
    """Give a Markdown document the breathing room a reader expects.

    Line height, a gap under each paragraph, a larger gap above each heading, and heading
    sizes that make sections findable. Applied to the built document because Markdown does
    not go through the stylesheet.
    """
    cursor = QtGui.QTextCursor(document)
    base = base_point_size or document.defaultFont().pointSizeF()
    block = document.begin()
    while block.isValid():
        cursor.setPosition(block.position())
        block_format = block.blockFormat()
        # `.value`, because PySide6 hands out a python enum here and the C++ signature wants
        # the int. Passing the enum itself raises, and it raises inside the disclaimer page —
        # i.e. before the window can be built at all.
        block_format.setLineHeight(MARKDOWN_LINE_HEIGHT,
                                   QtGui.QTextBlockFormat.ProportionalHeight.value)
        level = block_format.headingLevel()
        block_format.setTopMargin(MARKDOWN_HEADING_GAP if level else 0)
        block_format.setBottomMargin(MARKDOWN_PARAGRAPH_GAP)
        cursor.setBlockFormat(block_format)
        if level and base and base > 0:
            scale = MARKDOWN_HEADING_SCALE.get(level, 1.0)
            char_format = QtGui.QTextCharFormat()
            char_format.setFontPointSize(base * scale)
            char_format.setFontWeight(QtGui.QFont.DemiBold)
            cursor.select(QtGui.QTextCursor.BlockUnderCursor)
            cursor.mergeCharFormat(char_format)
        block = block.next()


def guide_html(t, data: Path) -> str:
    """The player-facing guide, in the window rather than in a markdown file nobody opens.

    EVERY SENTENCE GOES THROUGH `t`. Only the headings and two button names used to, so a
    window running in Chinese still explained itself in English — and this is the page that
    exists precisely for the player who does not already know what to do.

    The bold lead of each rule is a separate string from its explanation, so a translator is
    never asked to reproduce markup inside a sentence.
    """
    return f"""
    <div style="color:{theme.INK}; font-size:14px; line-height:1.7;">
      <h2 style="color:{theme.VELLUM};">{t('Set the game to Japanese')}</h2>
      <p>{t('In the game: Options → Language → 日本語. It costs nothing and can be changed '
            'back at any time.')}</p>
      <p>{t('Your records show item names in the language of this window, whatever the '
            'game is set to.')}</p>
      <h2 style="color:{theme.VELLUM};">{t('Turn these two on')}</h2>
      <p>{t('In the game, under Options:')}</p>
      <ul>
        <li><b>メッセージ早送り</b> — {t('message fast-forward')}</li>
        <li><b>テキスト一括表示</b> — {t('show the whole text at once')}</li>
      </ul>
      <p>{t('With these on, a drop line appears complete instead of being drawn one '
            'character at a time. That matters more than it sounds: this client reads '
            'whatever is on screen, and a half-drawn line is read as a different item '
            'rather than as a near miss.')}</p>
      <h2 style="color:{theme.VELLUM};">{t('Play at a size this client knows')}</h2>
      <p><b>1920 × 1080</b> {t('landscape')} · <b>1600 × 900</b> {t('landscape')} —
          {t('both are options in the game itself, under Screen size, and both are read '
             'without any setup here.')}</p>
      <p>{t('Full screen is fine at those sizes. The game keeps drawing at the size you '
            'chose and Windows stretches it to fill your screen; this client reads the '
            'picture at the size it was drawn.')}</p>
      <p>{t('Choose the size in the game before going full screen, though. Full screen '
            'enlarges whatever the game draws, and enlarging cannot put back detail that '
            'was never drawn.')}</p>
      <p><b>1280 × 720</b> {t('landscape')} — {t('not supported. At that size the game '
            'draws the names with too little detail to read reliably, and a line this '
            'client cannot read is left out rather than guessed at.')}</p>
      <p><b>704 × 1241</b> {t('portrait')} — {t('a tall window, if you prefer it. The game has no such '
            'option, so it needs {tool}: a small free utility by NowvaB that resizes the '
            'game window and nothing else. It is not ours and not bundled here.',
            tool=f'<a href="{WINDOW_TOOL_URL}" style="color:{theme.VELLUM};">WVDWS</a>')}</p>
      <h2 style="color:{theme.VELLUM};">{t('While you play')}</h2>
      <ol>
        <li><b>{t('Pick the right dungeon.')}</b>
        {t('It is the one thing this window cannot check for you, and every chest is filed '
           'under it.')}</li>
        <li><b>{t('Chests: let each line finish before advancing.')}</b>
        {t('A half-read line is recorded as a different item, not as a near miss.')}</li>
        <li><b>{t('Veins: wait for the ▼.')}</b>
        {t('It means the panel has finished and the swing has been recorded. Dismiss before '
           'it appears and that swing is lost.')}</li>
        <li><b>{t('Pickaxes are counted when one breaks.')}</b>
        {t('The client reads the break message itself, so the number beside the pickaxe '
           'follows what the game tells you. Set it when you restock.')}</li>
      </ol>
      <p>{t('You can use the computer while it records. The client reads the game window '
            'itself, not a picture of the screen, so a browser or a chat window in front of '
            'the game does not reach the recording. Minimising the game does: a window that '
            'is not being drawn has nothing to read.')}</p>
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
    """THE WINDOW'S OWN ENTRY POINT, and the one the exe runs.

    It used to call `logging.basicConfig`, which writes to a console — and a windowed build
    has none, so **the released exe never wrote a log line in its life**. The file logging
    lives in `__main__.main`, which the exe does not go through: `build_exe`'s entry script
    calls this function directly.

    The setting was half-wired for the same reason. `logs.configure` ran only when the trace
    checkbox was TOGGLED, so a player who had turned trace on in an earlier session opened
    the window with it ticked and got nothing — which is precisely how it was reported. The
    setting is applied here, at startup, where every other setting is.
    """
    from . import logs

    cfg = ClientConfig.load()
    logs.configure(trace=bool(getattr(cfg, "trace", False)))
    theme.install_message_filter()
    app = QtWidgets.QApplication(list(argv or []))
    theme.apply_style(app)
    theme.apply_font(app)
    theme.apply_icon(app)
    window = MainWindow(cfg)
    window.show()
    return app.exec()

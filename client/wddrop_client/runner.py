"""
The capture loop — ties frame source, HUD detector, episode machine and recogniser together.

Per frame:

    1. HUD present?  -> walking. Nothing to read; skip the expensive path entirely.
       Most of a session is walking, so this gate is what keeps the client cheap.
    2. HUD absent    -> an episode (battle / npc / chest / trap). Read the message band,
       recognise it against the vocabulary, and hand the text to the episode machine.
    3. HUD returns   -> episode closes; if it produced drop lines, spool a DropEvent.

The recogniser returns a NAME, not free text, so the episode machine is fed a reconstructed
line built from the locale template. That keeps one parser and one set of rules for both the
render-and-compare path and any future text path, instead of two divergent code paths.

Nothing is uploaded from here. Events are appended to the spool and the uploader drains it
later, so a dropped connection or a crash cannot cost the player their records.
"""
from __future__ import annotations

import json
import os
import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

from pathlib import Path

from .calibration import Profile

log = logging.getLogger("wddrop.runner")

# Stop the session if nothing has happened for this long — the player has almost certainly
# stopped playing without pressing Stop. Recorded as StopReason.IDLE_TIMEOUT, which matters
# because manual stops may be outcome-dependent and idle ones are not.
IDLE_TIMEOUT_SECONDS = 15 * 60

# Only near-misses worth a human's time are queued. Mid-typewriter frames produce garbage
# matches around 0.66-0.75 (a half-drawn line genuinely resembles nothing), and queueing
# those would bury the real questions. The case that matters — a true item beaten only by a
# one-character rival — scored 0.882.
QUEUE_MIN_SCORE = 0.80

# A panel line must look at least this good before its top two candidates are worth
# separating; below it the reading is bad for some other reason and a tie-break would only
# choose confidently between two wrong answers.
MINING_MIN_SCORE = 0.70
# ...and the discriminating columns must then separate them by this much. Measured on real
# panels: a genuine one-character rival (下級 vs 上級鐵礦石) separates by 0.18, and an
# already-unambiguous line by 0.44, against a whole-line margin of 0.02 for the first.
MINING_TIE_MARGIN = 0.10
# ...and the winner must actually LOOK like what is on screen over those columns. Measured:
#
#     下級/中級鐵礦石 vs 上級鐵礦石   winner 0.77-0.91, loser 0.37-0.59   correct every time
#     精煉石（攻擊力+4～6）           winner 0.2173,    loser 0.1096      WRONG, and accepted
#
# Nothing separates those two cases by margin — 0.36 and 0.11 are both over the gate. The
# absolute fit separates them by half. Below this the tie is simply not broken and the line
# is left unread, which is the honest answer: a wrong item name is a false measurement, an
# unread line is a gap that the review queue can still pick up.
MINING_TIE_MIN_SCORE = 0.60
# How good a panel must look at the band's own spacing before its spacing is worth fitting.
# Below the read gate on purpose — the whole point is that the right spacing is not in use
# yet — but above the junk, which sits at 0.30-0.54.
PANEL_SPACING_SWEEP_MIN = 0.55
# Below this a panel row is not a line of text at all -- it is a highlight or a marker that
# happened to light up inside the band -- so it is not counted as a line we failed to read.
PANEL_ROW_IS_TEXT = 0.40
# Veins exist in ONE dungeon: 北穿幽靈城 (7015). Everything about mining agrees -- the
# pickaxes are 北穿的十字鎬, and every recorded panel came from there.
#
# So the panel reader is switched off everywhere else, which is not an optimisation: a
# three-line index looking at every panel-shaped thing on screen produced a complete,
# plausible, entirely invented mining event in a chest-only session. Outside 7015 that whole
# class of mistake cannot happen at all.
#
# The cost is stated rather than hidden: mine in 7015 while the dungeon is set to something
# else and the swings are not recorded. The dungeon cross-check already warns when the label
# and the contents disagree, which is the same mistake seen from the other side.
MINING_DUNGEON_IDS = (7015,)
# The pickaxe index holds three candidates, so the ordinary gates are far too easy to clear
# by accident. A real break message reads at 0.9+; two stray rows cleared the default gates
# in a session containing no mining whatsoever.
PICKAXE_MIN_SCORE = 0.90
PICKAXE_MIN_MARGIN = 0.10

# Automatic break detection, OFF — and now measured on both halves of the question rather
# than argued. Two sessions, same index, same thresholds:
#
#   mining session, a pickaxe genuinely broke   0.8160  margin 0.2789   TRUE
#   chest-only session, no mining at all        0.8157  margin 0.2793   false
#   chest-only session, no mining at all        0.8107  margin 0.2683   false
#
# The true hit and the false one are 0.0003 apart, and the false one has the LARGER margin.
# No threshold separates them; the 0.90 gate below rejects all three, which is why it was
# set there and why the real break went unreported. Reading the message is not the problem —
# it is read correctly — telling it apart from whatever else lights up in that band is.
#
# A false break spends a pickaxe the player still has, and the swings-since-break count that
# the whole pickaxe-lifetime figure rests on would be wrong from that moment. So the button
# stays the signal until something other than score can separate these — most likely
# requiring the panel to be a MINING panel, which is knowable and is not currently checked.
#
# `WDDROP_PICKAXE_AUTODETECT=1` runs it anyway and logs every candidate with its score and
# margin, hit or miss. That is how the table above was produced, and how it should be
# reproduced before anyone turns this on.
# ON, now that it is measured rather than assumed. It was off because a real break read
# 0.4563 and two "false positives" scored like true hits — and both of those findings were
# artefacts: the index was built at the ITEM lines' size, and the "session with no mining"
# did contain mining. Read at the row's own size a break scores 0.906-0.911 with margin
# 0.31-0.41, while a chest-only session produces nothing above 0.55 at all.
# `WDDROP_PICKAXE_AUTODETECT=0` turns it off again.
PICKAXE_AUTODETECT = os.environ.get("WDDROP_PICKAXE_AUTODETECT", "1") != "0"
# These messages render at ~0.0 letter spacing, not at the panel's fitted value. Measured on
# real breaks: 1080 read 0.9060 at 0.0 against 0.8720 at the panel's -0.1, and 704x1241 read
# 0.9071 at 0.0 against 0.8635 at the panel's +1.1 — both of which fall under the 0.90 gate.
PICKAXE_SPACING = 0.0

# PNG compression for recorded frames. 1, not the default 6 and emphatically not
# `optimize=True`: the write happens on the capture thread, so every millisecond spent here
# is a millisecond not spent sampling the screen. See _write for the measurements.
RECORD_COMPRESS_LEVEL = 1
# Frames are encoded on a BACKGROUND thread, because encoding is not a background chore when
# it runs on the capture thread — it is time the screen is not being sampled. Measured per
# frame on a real recording, with everything else the loop does for comparison:
#
#     1920x1080   grey 0.7ms   HUD 0.1ms   panel rows 2.0ms   band ink 0.6ms   PNG 34.5ms
#      704x1241   grey 0.3ms   HUD 0.0ms   panel rows 0.3ms   band ink 0.2ms   PNG  9.6ms
#
# Recognition is nothing; the write is everything. A player asking for 20fps got 10.4 at 1080
# with the write in line, and a mining panel dismissed inside the gap is not in the recording
# for anything to find later.
#
# The queue is BOUNDED and drops rather than blocks. A slow disk must cost recorded frames,
# never sampled ones: a dropped frame is missing from the replay, a missed sample is missing
# from the DATA. Drops are counted and reported, never silent.
RECORD_QUEUE_MAX = 64

# A line recovered as it vanished was seen once and never observed to hold still, so the
# risk it carries is specifically that it was HALF-DRAWN. Score is the right guard for that:
# a partial line leaves the tail of the window empty and correlates poorly against any full
# candidate, whereas a complete line scores like any other (0.827 on a real replaced line).
# Margin measures something different -- how distinguishable the answer is -- so it keeps the
# ordinary threshold. Demanding double margin instead was measured to reject a perfectly good
# complete line, which is the failure this path exists to prevent.
# The risk a once-seen line carries is that it was HALF-DRAWN, and that is tested DIRECTLY
# by comparing how wide the line actually was against how wide the matched name renders: a
# partial line is visibly shorter than its own name.
#
# Score was tried as the guard and is a bad proxy -- it varies with CONTENT, not
# completeness. A legitimate complete line whose name is ASCII digits scored 0.644 where CJK
# names score 0.83+, so any threshold admitting the digits would also admit genuinely partial
# CJK lines.
VANISH_MAX_WIDTH_SHORTFALL_PX = 12

# Fraction of the previous line's ink that must disappear for it to count as REPLACED rather
# than still being drawn. Measured on real frames: a line re-drawn identically removes 0.0%,
# while genuine replacements removed 56% and 95%.
REPLACED_INK_FRACTION = 0.25

# A message band with less ink than this holds no text worth recognising. Cheap arithmetic,
# and it skips the expensive path on most frames.
MIN_BAND_INK_PIXELS = 40
# Warn if the HUD never fires. Without it episodes only close on the idle fallback, and the
# frame gate that keeps the client cheap is doing nothing — the first live Steam run hit
# exactly this and pinned a core.
HUD_SILENT_WARN_FRAMES = 200

# Rendered candidate indexes, shared across every session in this process.
#
# One index is 3,244 candidates x 5,720 pixels of float32 — 74 MB, four seconds to build —
# so a player doing several dives in a sitting used to pay that again for each. Keyed by the
# DATA VERSION as well as the geometry: the index is a rendering of a particular vocabulary
# through a particular atlas, and reusing one across a data update would read plausible wrong
# names rather than fail.
#
# Only the geometry a session LOCKS ON reaches this. The probes it discards on the way there
# do not, or the cache would be the memory leak it exists to prevent.
_SHARED_INDEXES: dict[tuple, object] = {}


class CaptureRunner:
    def __init__(
        self,
        profile: Profile,
        recognizer,
        hud_detector,
        tracker,
        *,
        message_format,
        on_event=None,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        renderer=None,
        prefix: str = "",
        review_queue=None,
        record_dir=None,
        record_mode: str = "episodes",
        record_limit: int = 4000,
        record_context: int = 8,
        dungeon_hints=None,
        pickaxe_watch=None,
        pickaxe_recognizer=None,
        mining_names=None,
        mining_render_source=None,
        mining_format=None,
        mining_prefix: str = "",
        pickaxes: int | None = None,
        data_version: str | None = None,
        profile_path=None,
    ):
        # Mining reports through a different UI entirely — a centred dialogue panel saying
        # 「得到了…。」 rather than the bottom band's 「獲得了…！！」 — so it needs its own
        # reader. See capture/panel.py.
        self._mining_names = list(mining_names) if mining_names else None
        self._render_source = mining_render_source
        self._mining_indexes: dict[int, object] = {}
        self._pickaxe_indexes: dict[int, object] = {}
        self._mining_renderers: dict[int, object] = {}
        self._mining_renderer = None
        # (size, spacing) of the panel, LOCKED once a confident read happens. See _fit_panel.
        self._panel_fit: tuple[int, float] | None = None
        self._data_version = data_version
        self._profile_path = Path(profile_path) if profile_path else None
        # A fit carried over from a previous session, if it was made against this same data.
        stored = (getattr(profile, "panel_font_size", None),
                  getattr(profile, "panel_letter_spacing", None))
        if all(v is not None for v in stored):
            if getattr(profile, "panel_data_version", None) == data_version:
                self._panel_fit = (int(stored[0]), float(stored[1]))
                log.info("wddrop: panel geometry from the profile: %dpx spacing %+.1f",
                         *self._panel_fit)
            else:
                log.info("wddrop: panel geometry ignored — it was fitted against other "
                         "game data")
        # Sized for the longest candidate at a plausible panel size; the panel is centred and
        # its lines are short, so this is comfortably generous.
        self._panel_window = (520, 44)
        self.mining_format = mining_format
        self.mining_prefix = mining_prefix
        self.mining_min_score = MINING_MIN_SCORE
        self._panel_key: bytes | None = None
        # Whether the panel currently on screen has already been read. One vein is MANY
        # swings, and HOW many is dynamic -- one measured vein gave five yields for a single
        # pickaxe, but nothing here may assume that number. Each appearance is one swing.
        self._panel_emitted = False
        # A plain client-side count the player sets before diving. Not data — the server is
        # told nothing about it — just the number they came in with, less what they have
        # spent, so they know when to go back to town.
        self.pickaxes_left = pickaxes
        self.on_mining = None
        # Mining's cost, which the player has to be told about: a pickaxe that breaks is
        # gone, and running out is what quietly ends a mining run.
        self.pickaxes = pickaxe_watch
        self._pickaxe_recognizer = pickaxe_recognizer
        self.on_pickaxe = None
        # Cross-checks the player's declared dungeon against what the chest contained. The
        # label is user_declared and is the analysis stratum, so a wrong one moves
        # observations into another dungeon's distribution rather than merely adding noise.
        self.dungeon_hints = dungeon_hints
        # Frame recording, so a live session can be replayed offline and the recogniser
        # re-tested against exactly what it saw. PNG rather than video on purpose: mp4
        # compression alters pixels, and the whole recogniser works on pixels — a lossy
        # recording would not reproduce the run it is meant to explain.
        self.record_dir = Path(record_dir) if record_dir else None
        self.record_mode = record_mode
        self.record_limit = record_limit
        # HUD-present frames kept either side of an episode. Saving ONLY HUD-absent frames
        # produced recordings that could not be replayed: episodes close when the HUD
        # RETURNS, so a recording containing no HUD frames never closes one. Context frames
        # restore the transitions that make a replay mean the same as the live run.
        self.record_context = record_context
        self._recorded = 0
        self._pre_context: list = []
        self._post_context = 0
        # Frames are filed per EPISODE inside the session directory, so the frames that
        # produced a given chest sit together and can be inspected on their own. Reviewing a
        # single suspect drop otherwise means hunting through a few thousand PNGs.
        self._episode_index = 0
        self._in_episode = False
        self.fps: float | None = None
        self._frame_src: str | None = None
        # name -> the frame it was read from, so provenance survives to the emitted event.
        # Both this and _quantities are PER CHEST and cleared on emit: the same item can drop
        # from several chests, and a global cache made the second occurrence inherit the
        # first one's frame and, worse, its QUANTITY -- silently copying a number from an
        # unrelated chest.
        self._sources: dict[str, str] = {}
        self.renderer = renderer
        self.prefix = prefix
        # Near-misses go here rather than being dropped. A refusal is the recogniser working
        # correctly (it declines to guess), but silently discarding it would lose real drops:
        # on a real frame the true item scored 0.882 and was refused only because its
        # one-character rival 中/重 sat 0.023 behind.
        self.review_queue = review_queue
        self.profile = profile
        self.recognizer = recognizer
        self.hud = hud_detector
        self.tracker = tracker
        self.fmt = message_format
        self.on_event = on_event or self._spool
        self.idle_timeout = idle_timeout
        self.stats = {"frames": 0, "hud_present": 0, "skipped_blank": 0,
                      "skipped_animating": 0, "skipped_same": 0,
                      "recognised": 0, "recognised_on_vanish": 0,
                      "queued": 0, "chests": 0, "recorded": 0}
        self._quantities: dict[str, int | None] = {}
        # Recognition is by far the most expensive step (~230ms over 3,381 candidates), and a
        # message line persists for MANY frames. Caching on the band's content collapses that
        # to once per distinct line instead of once per frame.
        self._last_band_key: bytes | None = None
        self._last_text: str = ""
        self._recognised_key: bytes | None = None
        # The most recent non-blank band that has NOT been recognised yet. A player who
        # advances the dialogue quickly can show a line for less than one sample interval,
        # so waiting for it to be stable across two frames would drop it entirely. When the
        # band goes blank, whatever was last on it gets one recognition attempt.
        self._pending: tuple[bytes, object] | None = None
        self._last_mask = None
        self._warned_no_hud = False
        # Cooperative cancellation. The loop is a generator pull over a frame source, so the
        # only ways out used to be "the source ended" and Ctrl-C -- neither of which a Stop
        # button in a window can produce. An Event is set from any thread and read once per
        # frame, so stopping is as prompt as one sample interval and lands BETWEEN frames,
        # where the tracker is consistent: killing the thread instead would abandon a chest
        # mid-episode, which is the one thing stop_session exists to prevent.
        self._stop = threading.Event()
        self._stop_reason: str | None = None
        self.stop_reason: str | None = None

    # -- stopping ----------------------------------------------------------------
    def stop(self, reason: str = "user_stop") -> None:
        """Ask the loop to finish, from any thread.

        WHY THE REASON IS RECORDED: manual start/stop puts the session boundary under the
        player's control, and stopping may be OUTCOME-dependent — quitting right after a
        good drop, or after a bad streak, makes session end correlate with drop quality and
        can manufacture exactly the "quality falls with farming time" pattern the study
        exists to test. Without the reason that bias is invisible; with it, user-stopped
        sessions can be compared against idle ones. See StopReason in the schema.
        """
        self._stop_reason = reason
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- main loop ---------------------------------------------------------------
    def run(self, source, dungeon_id: int, floor_id: int | None = None) -> dict:
        start = datetime.now(timezone.utc)
        self.fps = getattr(source, "fps", None)
        self._write_manifest(start, dungeon_id, floor_id)
        self.tracker.start_session(start)
        self.tracker.on_chest = lambda obs: self._emit(obs, dungeon_id, floor_id)
        self._dungeon_id, self._floor_id = dungeon_id, floor_id
        # Kept so the caller can attribute the stop reason to this dive's events once the
        # session ends -- the reason is only known then, and the events are already spooled.
        self.dive_id = str(self.tracker.dive_id) if self.tracker.dive_id else None
        try:
            self._consume(source, start)
        except KeyboardInterrupt:
            # Ctrl-C is a deliberate stop by the player, and is the same event as a Stop
            # button. Recording it as "the source ended" would put every ctrl-c session in
            # the wrong bucket for the bias check above.
            self.stop_reason = "user_stop"
            raise
        finally:
            # Ctrl-C is the normal way to end a session, and it used to propagate straight
            # out of here -- so stop_session never ran and a chest still in progress was
            # abandoned without a trace.
            self.tracker.stop_session(self._clock(start, 0))
            # Before the counts are read: frames still queued have not been written yet, and
            # a session that reports 4,000 recorded frames must have 4,000 on disk.
            self._stop_writer()
            self.stats["recorded"] = self._recorded
            self.stats["stop_reason"] = self.stop_reason
        return dict(self.stats)

    def _consume(self, source, start) -> None:
        checked_size = False
        for frame in source.frames():
            if self._stop.is_set():
                self.stop_reason = self._stop_reason
                break
            self.stats["frames"] += 1
            if not checked_size:
                checked_size = True
                self._check_frame_size(frame.image.size)
            now = self._clock(start, frame.t)
            gray = frame.image.convert("L")
            self._frame_src = frame.source

            present = self.hud.present(gray) if self.hud else False
            self._record(frame.image, present, now)
            # Before the HUD gate: the panel opens and closes independently of it, and the
            # frames where it is GONE are what tell us the next one is a new swing.
            self._read_panel(gray, now)
            if present:
                self.stats["hud_present"] += 1
                # Read any line still pending BEFORE the episode closes, and attribute it to
                # that episode. Carrying it forward credited a drop from one chest to the
                # NEXT one, several seconds later -- the elapsed time and the chest index
                # were both wrong, which is worse than losing it.
                pending_text = self._flush_pending(now)
                if pending_text:
                    self.tracker.tick(now, False, pending_text)
                self.tracker.tick(now, True, "")
                self._last_band_key = self._recognised_key = None
                self._last_mask = None
                continue
            if (
                not self._warned_no_hud
                and self.hud is not None
                and self.stats["frames"] >= HUD_SILENT_WARN_FRAMES
                and self.stats["hud_present"] == 0
            ):
                self._warned_no_hud = True
                log.warning(
                    "wddrop: HUD never detected in %d frames. Episodes will only close on the "
                    "idle fallback. Run `wddrop probe` while walking in a dungeon to see what "
                    "the detector is looking at.", self.stats["frames"],
                )

            text = self._read_band(gray, now)
            self.tracker.tick(now, False, text)

            if self.tracker.idle_seconds(now) > self.idle_timeout:
                log.info("wddrop: idle timeout, stopping session")
                self.stop_reason = "idle_timeout"
                break
        # Falling out of the loop means the SOURCE ended -- the recording ran out, or the
        # game window went away. stop_reason stays None here rather than being guessed at;
        # the caller knows which of those it was and says so.

    def _read_panel(self, gray, now) -> None:
        """Read the mining result panel, if one is showing.

        ONE VEIN IS MANY SWINGS, and how many is DYNAMIC -- one measured vein gave five
        yields with the grade varying between them (下級 then 中級), but that count is an
        observation, not a rule, so nothing here counts swings or stops after any number of
        them. The panel does not blank in between — the text is REPLACED in place as the player advances, exactly like the
        message band — so "a new swing" means the text CHANGED, not that the panel reappeared.

        Settling is by SIMILARITY, not equality. The panel fades in and its ▼ marker blinks,
        so the mask is never bit-identical twice running: one panel gave 10 distinct masks
        over 10 frames at every ink threshold tried. Demanding equality meant it could never
        settle, and on the vein measured only one swing in five was read.
        """
        if self._mining_names is None or self._dungeon_id not in MINING_DUNGEON_IDS:
            return
        from .capture.glyph import anchor_window
        from .capture.panel import (advance_marker, panel_rows, panel_signature,
                                    same_text, size_from_rows)

        rows = panel_rows(gray)
        size = size_from_rows(rows)
        band = getattr(self.profile, "font_size", 26)
        # "No panel" must mean no PLAUSIBLE panel: a battle button or a chest highlight lights
        # rows of its own, and treating those as a panel resets the comparison against noise.
        if not rows or size is None or abs(size - band) > 4:
            # The panel is gone. The next one is a new swing, whatever it says.
            self._panel_key = None
            self._panel_emitted = False
            return
        sig = panel_signature(gray, rows)
        # THE ▼ IS THE GAME SAYING THE PANEL IS FINISHED, so it beats inferring completeness
        # from pixels settling: it is there on the FIRST frame the panel is done, where the
        # settle test needs a second frame to compare against. That is precisely the gap a
        # player who clicks quickly falls into -- measured, three swings of one vein were
        # dismissed inside two frames and lost, and the marker was present in all of them.
        #
        # Similarity stays as the fallback for anything the marker constants do not fit
        # (another resolution, another locale's panel), so nothing regresses to worse than
        # before if the arrow is not found.
        settled = advance_marker(gray, rows) or same_text(sig, self._panel_key)
        self._panel_key = sig
        # ONE read per appearance, decided by presence rather than by content. Two successive
        # swings can differ only in a digit — measured 0.960 mask overlap between 「×6」 and
        # 「×3」 — so no pixel comparison can separate them, and deduplicating on content
        # would delete a real yield. The panel does leave the screen between swings, and that
        # gap is the honest boundary.
        if not settled or self._panel_emitted:
            return
        # THE ROW BOUNDARY JITTERS BY A PIXEL as the panel fades, which flips the derived
        # size by one -- and one pixel of size is the difference between 0.86 and 0.51, so two
        # swings read as nonsense (靈樹皮帽 for 中級鐵礦石). The derived size is a
        # starting point; its neighbours are tried and the SCORE decides, which is the same
        # thing calibration does. Indexes are cached per size, so this costs at most three
        # builds for the life of the session.
        # Probe with the row whose height is TYPICAL of the panel, not the first one: a
        # stray element above the panel (a highlight, a marker) lands in `rows` too, and
        # sizing off it picked a size that read the real lines as nonsense.
        heights = sorted((b - a, i) for i, (a, b) in enumerate(rows))
        probe = rows[heights[len(heights) // 2][1]]
        best = self._fit_panel(gray, probe, size)
        if best is None:
            return
        _score, size, spacing, window, recognizer = best
        # The tie-breaker renders its own candidates, so it must use the renderer for the
        # size that WON -- the sweep above leaves the last one tried selected otherwise, and
        # the shapes then disagree.
        self._mining_renderer = self._mining_renderers[(size, round(spacing, 2),
                                                        tuple(window))]
        # Cut the pixels AFTER the size is settled but BEFORE anything slow: building an index
        # for a size not seen before takes seconds over ~3,400 candidates, and the panel can
        # be dismissed while it runs.
        # Paired with their rows: the pickaxe pass reports by row index, and a row that
        # cannot be cut must not shift the rest along.
        cut = [(row, anchor_window(gray, row, window)) for row in rows]
        crops = [(row, w) for row, w in cut if w is not None]
        # AUTOMATIC BREAK DETECTION IS OFF, and this is why. 「北穿的黃金十字鎬壞掉了」 does
        # appear in the panel and is read correctly -- but a three-candidate index has almost
        # nothing for a wrong answer to lose to, and the same index reported the gold pickaxe
        # breaking TWICE in a session containing no mining at all. The true hit and the false
        # ones could not be separated by score: at a bar high enough to reject the false pair
        # (0.90) the real break was rejected too.
        #
        # A false break spends a pickaxe the player still has, which is worse than not
        # counting breaks, so the counter falls back to one per mine (see _emit_mining) until
        # there is enough data to separate them.
        # The PANEL's index, not `self._pickaxe_recognizer`. That one is built for the
        # message band, and feeding it a panel crop is not a near miss — the windows are
        # different shapes and the correlation raises "size 3120 is different from 9776".
        # So the off switch was hiding a wiring bug as well as a policy: `_pickaxe_index`
        # was written for exactly this and never called.
        # The pickaxe messages first, and per ROW rather than over the panel's crops: they
        # are sized differently from the item lines, so they need their own anchoring. A row
        # that IS one of them is not an item line, so it is taken out of the item pass.
        lines, unread, tie_broken = [], 0, 0
        for row, win in crops:
            match = recognizer.recognize(win)
            name = match.name
            if name is None and self._pickaxe_row(gray, row, size):
                # A pickaxe message, not an item line. Tried only where the item index
                # REFUSED: a row that reads as an item is not one of these, and sweeping
                # every row of every frame cost tens of thousands of comparisons for the
                # handful of frames that are actually a message.
                continue
            if (not match.accepted and match.score >= self.mining_min_score
                    and match.best and match.runner_up):
                # Graded families (下級/中級/上級/特級鐵礦石) differ by ONE character, so the
                # identical rest of the line drowns the difference and both are refused.
                # Re-score over just the columns where the two candidates differ.
                from .capture import glyph as _g

                winner, margin, fit = _g.break_tie(
                    win, self._mining_renderer, self.mining_prefix,
                    match.best, match.runner_up)
                # BOTH gates, for the same reason the whole-line recogniser needs both: the
                # margin says which of the two fits better, not that either fits. A family of
                # 111 精煉石（...）entries differing only in the digits inside the brackets
                # produced winner 0.2173 against loser 0.1096 — a 0.108 margin, over the
                # gate, between two readings that were both noise — and overturned a
                # whole-line pass that had the right answer.
                if margin >= MINING_TIE_MARGIN and fit >= MINING_TIE_MIN_SCORE:
                    name, tie_broken = winner, tie_broken + 1
            if not name:
                # Only rows that LOOK like text count as unread. A highlight or a marker
                # inside the panel band is not a line the recogniser failed on, and counting
                # it would make a complete panel look partial.
                if match.score >= PANEL_ROW_IS_TEXT:
                    unread += 1
                if match.score >= QUEUE_MIN_SCORE and self.review_queue is not None:
                    self._queue(match, now)
                continue
            quantity = self._panel_quantity(win, name)
            lines.append((name, quantity))
        self._panel_emitted = True
        if lines:
            self._emit_mining(lines, now, unread, tie_broken)

    # Spacings to try, as offsets from the calibrated band's. The panel is not drawn with
    # the band's spacing, and until this existed it inherited it — which worked on one client
    # by coincidence and failed completely on another. Measured on a real 1920x1080 panel,
    # band +0.5:
    #
    #     spacing +0.5 (inherited)  0.603   nothing read, under the 0.70 gate
    #     spacing +0.2              0.770   中級鐵礦石
    #     spacing +0.8              0.782   中級鐵礦石  (at 21px)
    #
    # +-0.6 in steps of 0.3 covers both of those and the +0.2 that the 704x1241 client
    # wants. Zero is first so the common case — the band's spacing being right — costs
    # nothing extra.
    PANEL_SPACING_OFFSETS = (0.0, -0.3, 0.3, -0.6, 0.6)

    def _fit_panel(self, gray, probe, size: int):
        """Find the (size, spacing) that actually reads this panel, and then stop looking.

        Size first at the band's spacing, exactly as before; then spacing at that size. Both
        are decided by the RECOGNITION SCORE against the full index, not by a cheaper proxy
        — fitting spacing from the prefix alone was tried and picked +0.5 where +0.2 was
        right, turning three correct readings into one wrong one. The expensive measure is
        the one that works.

        LOCKED once something reads above the gate. The lock is what stops the drift that
        had this building nine indexes in a session that should need three: junk frames pass
        the size gate with whatever height they happen to have, and each new size costs a
        build over ~3,400 candidates.
        """
        from .capture.glyph import anchor_window

        band_spacing = getattr(self.profile, "letter_spacing", 0.0)

        def attempt(candidate_size: int, spacing: float):
            window = self._panel_window_for(candidate_size)
            crop = anchor_window(gray, probe, window)
            index = self._mining_index(candidate_size, window, spacing)
            if crop is None or index is None:
                return None
            return (index.recognize(crop).score, candidate_size, spacing, window, index)

        if self._panel_fit is not None:
            return attempt(*self._panel_fit)

        best = None
        for candidate in (size, size - 1, size + 1):
            got = attempt(candidate, band_spacing)
            if got and (best is None or got[0] > best[0]):
                best = got
        if best is None:
            return None
        # Only pay for the spacing sweep when the frame looks like a panel at all. Every
        # size costs an index build over ~3,400 candidates, and junk frames — a battle
        # flash, a highlight — pass the size gate constantly with whatever height they
        # happen to have. Measured probe scores at the band's spacing: junk 0.30, 0.49,
        # 0.50, 0.54; the real 1920x1080 panel 0.637, the real 704x1241 one higher still.
        # Sweeping everything took a session from 9 index builds to 19.
        # ...and only when it is not ALREADY good enough. A client whose band spacing suits
        # the panel — which was every client before 1920x1080 turned up — reads above the
        # gate on the first try, and searching for a better spacing then costs four index
        # builds to confirm what already worked. Measured over 26 archived sessions:
        # sweeping regardless took 133 index builds to 155.
        if PANEL_SPACING_SWEEP_MIN <= best[0] < self.mining_min_score:
            for offset in self.PANEL_SPACING_OFFSETS[1:]:
                got = attempt(best[1], band_spacing + offset)
                if got and got[0] > best[0]:
                    best = got
        if best[0] >= self.mining_min_score:
            self._panel_fit = (best[1], best[2])
            log.info("wddrop: panel reads at %dpx spacing %+.1f (band is %+.1f), score %.3f",
                     best[1], best[2], band_spacing, best[0])
            self._keep_only_the_fit()
            self._remember_panel_fit()
        return best

    def _keep_only_the_fit(self) -> None:
        """Drop the geometries the search tried on the way to the right one.

        Each is 74 MB of rendered templates and none is consulted again — a session that
        probed eleven of them was holding most of a gigabyte for the rest of the run. The
        winner is promoted to the process-wide cache, so the next dive in this sitting
        builds nothing at all.
        """
        keep = (self._panel_fit[0], round(self._panel_fit[1], 2))
        for key in [k for k in self._mining_indexes if k != keep]:
            self._mining_indexes.pop(key, None)
            self._mining_renderers.pop(key, None)
        if keep in self._mining_indexes:
            _SHARED_INDEXES[(self._data_version, str(self._render_source)) + keep] = (
                self._mining_indexes[keep], self._mining_renderers[keep])

    def _remember_panel_fit(self) -> None:
        """Write the fit into the profile, so the next session searches for nothing.

        Best effort, and deliberately so: this runs inside a capture loop, and a profile
        that cannot be written is a slower next session, not a lost one. It must never take
        the session down with it.
        """
        if self._profile_path is None or self._panel_fit is None:
            return
        size, spacing = self._panel_fit
        if (getattr(self.profile, "panel_font_size", None) == size
                and getattr(self.profile, "panel_letter_spacing", None) == round(spacing, 2)
                and getattr(self.profile, "panel_data_version", None) == self._data_version):
            return
        try:
            from .calibration import ProfileStore

            self.profile.panel_font_size = int(size)
            self.profile.panel_letter_spacing = round(float(spacing), 2)
            self.profile.panel_data_version = self._data_version
            root = self._profile_path.parent
            store = ProfileStore.load(root)
            store.put(self.profile)
            store.save(root)
            self.profile.save(self._profile_path)
            log.info("wddrop: panel geometry saved to the profile")
        except Exception:                                  # noqa: BLE001
            log.debug("wddrop: could not save the panel geometry", exc_info=True)

    def warm_mining_index(self, dungeon_id: int | None) -> bool:
        """Build the panel's index BEFORE capture starts, not on the first panel.

        Lazy was wrong in a way that cost data. The build takes ~2.9s over 2,655 candidates
        and a mining panel is on screen for one to two seconds, so the FIRST swing of every
        fresh process was read while the index did not exist yet — the panel was gone by the
        time it did. Reported as "the first mine after starting is always missing", and
        invisible on a recording, where the loop is synchronous and simply waits.

        Only in a dungeon that HAS veins, so a chest-only session still pays nothing. Only
        when the profile already knows the panel's geometry; otherwise the size is genuinely
        unknown until a panel is seen, and the band's size is the sweep's own starting point
        — warming that removes one build from the critical path rather than all of them.
        """
        if dungeon_id not in MINING_DUNGEON_IDS or not self._mining_names:
            return False
        if self._panel_fit:
            size, spacing = self._panel_fit
        else:
            size, spacing = (getattr(self.profile, "font_size", 26),
                             getattr(self.profile, "letter_spacing", 0.0))
        # The same window the fit will ask for, height and all. Warming a different shape
        # builds an index nothing can use and leaves the real one still to be built.
        return self._mining_index(size, self._panel_window_for(size), spacing) is not None

    def _panel_window_for(self, size: int) -> tuple[int, int]:
        """The comparison window for a panel line at this size — one definition, so the warm
        build and the fit cannot disagree about its shape."""
        return (self._panel_window[0], size + 2)

    def _mining_index(self, size: int | None, window=None, spacing=None):
        """The panel's candidate index, built at the size the panel is actually drawn at.

        Cached across sessions in one process, and rebuilt only if the size changes, which it
        does not within one resolution. It is built up front for a mining dungeon — see
        warm_mining_index for what building it on demand cost.
        """
        band = getattr(self.profile, "font_size", 26)
        # The panel is drawn near, but not at, the message band's size (25px against 26px on
        # the measured client). Anything far outside that is not a panel at all -- it is a
        # button or a highlight that happened to look like a row of text -- and building an
        # index for it costs seconds each time.
        if size is None or abs(size - band) > 4:
            return None
        if spacing is None:
            spacing = getattr(self.profile, "letter_spacing", 0.0)
        window = tuple(window or self._panel_window)
        # The WINDOW is part of the key, because it is part of the templates: they are
        # rendered at exactly that shape and correlated against a crop of the same shape.
        # Leaving it out let an index built for one window be handed a crop cut for another,
        # which fails as "size 3380 is different from 5720" — deep inside the matmul, in a
        # place that says nothing about which two shapes disagreed or why.
        key = (size, round(spacing, 2), window)
        if key in self._mining_indexes:
            self._mining_renderer = self._mining_renderers[key]
            return self._mining_indexes[key]
        shared = _SHARED_INDEXES.get((self._data_version, str(self._render_source)) + key)
        if shared is not None:
            index, renderer = shared
            self._mining_indexes[key] = index
            self._mining_renderers[key] = renderer
            self._mining_renderer = renderer
            return index
        from .capture.glyph import RenderRecognizer, centred_shifts, make_renderer

        log.info("wddrop: building the mining index at %dpx (the panel is not the same size "
                 "as the message band)", size)
        renderer = make_renderer(self._render_source, size, window, spacing)
        self._mining_renderer = renderer
        index = RenderRecognizer(
            renderer, self.mining_prefix, self._mining_names,
            shifts=centred_shifts(tuple(self.profile.offset), 1),
        )
        self._mining_indexes[key] = index
        self._mining_renderers[key] = renderer
        return index

    def _pickaxe_index(self, size: int, spacing: float):
        """The pickaxe messages, rendered at the PANEL's size AND ITS SPACING.

        They share the panel with the yields -- 「北穿的黃金十字鎬壞掉了」 appears there, not
        in the message band -- which is why a whole-screen search at the band's 26px found
        nothing: one pixel of size is the difference between 0.9 and 0.46.

        Spacing is exactly the same trap and was missed when the panel got its own fitted
        value: this rendered at the panel's size but the BAND's spacing, and a real break
        message in a real recording read 0.4535 instead of ~0.9. That number is what the
        "cannot be told apart from noise" verdict was based on, so it was a measurement of
        this bug rather than of the problem.
        """
        if self.pickaxes is None or not len(self.pickaxes):
            return None
        key = (size, round(spacing, 2))
        if key in self._pickaxe_indexes:
            return self._pickaxe_indexes[key]
        from .capture.glyph import (RenderRecognizer, centred_shifts, make_renderer,
                                    required_window)

        # Its OWN window, not the panel's: these lines are a different length from an item
        # line, and at a different size, so the panel's window is the wrong shape for them.
        probe = make_renderer(self._render_source, size, (1600, 80), spacing)
        window = required_window(probe, "", self.pickaxes.candidates)
        index = RenderRecognizer(
            make_renderer(self._render_source, size, window, spacing),
            "", self.pickaxes.candidates,
            shifts=centred_shifts(tuple(self.profile.offset), 1),
        )
        self._pickaxe_indexes[key] = (index, window)
        return self._pickaxe_indexes[key]

    def _pickaxe_hit(self, gray, row, panel_size: int):
        """Is this panel row one of the pickaxe messages?

        Sized RELATIVE TO the panel fit, because the game draws these SMALLER than the item
        lines it shows them beside — but by a pixel or two, not arbitrarily. Measured on real
        breaks, against the panel's own fitted values:

            1920x1080   item lines 22px   break line 20px @ 0.0   0.9060  margin 0.3329
             704x1241   item lines 24px   break line 23px @ 0.0   0.9071  margin 0.3115
            ...read at the panel's fit instead                    0.4563  -- refused

        That 0.45 is what "cannot be told apart from noise" was based on, so the verdict was
        about this bug rather than about the problem. Read at its own size the message is
        unambiguous, and the two hits once filed as false positives in a "session with no
        mining" turned out to be real breaks in a session that did have some.
        """
        from .capture.glyph import anchor_window

        best = None
        for size in (panel_size - 2, panel_size - 1, panel_size):
            built = self._pickaxe_index(size, PICKAXE_SPACING)
            if built is None:
                return None
            index, window = built
            win = anchor_window(gray, row, window)
            if win is None:
                continue
            hit = index.recognize(win)
            if best is None or hit.score > best.score:
                best = hit
        return best

    def _pickaxe_row(self, gray, row, panel_size: int) -> bool:
        """Read one refused row as a pickaxe message; report a BREAK if that is what it is.

        Returns whether the row was one of these messages at all, so the caller can stop
        treating it as an item line it failed to read.
        """
        from .capture.pickaxe import BROKE

        if not (PICKAXE_AUTODETECT and self.pickaxes is not None and len(self.pickaxes)):
            return False
        hit = self._pickaxe_hit(gray, row, panel_size)
        if hit is None:
            return False
        log.info("wddrop: pickaxe candidate %r score=%.4f margin=%.4f",
                 hit.best, hit.score, hit.margin)
        # Both gates, kept high. A false break spends a pickaxe the player still has, and the
        # swings-since-break figure would be wrong from then on.
        if not (hit.name and hit.score >= PICKAXE_MIN_SCORE
                and hit.margin >= PICKAXE_MIN_MARGIN):
            return False
        event = self.pickaxes.feed(hit.name)
        # BREAKS only are reported. 「如果有十字鎬的話應該能採掘。」 stays in the index — it is a
        # third thing for a wrong answer to lose to, and dropping it would leave the margin
        # between the two break lines as the only evidence there is — but it is not acted on:
        # a player who has run out of pickaxes already knows, and the only count it could
        # move is the count of pickaxes BROKEN.
        if event is not None and event[0] == BROKE:
            self._on_pickaxe_event(event)
        return True

    def _on_pickaxe_event(self, event) -> None:
        from .capture.pickaxe import BROKE

        self.stats["pickaxe_messages"] = self.stats.get("pickaxe_messages", 0) + 1
        if event[0] == BROKE and self.pickaxes_left is not None:
            # Decremented on the BREAK, not on every swing. Measured: one vein took five
            # swings and cost ONE pickaxe, so counting per swing would have reported five
            # gone.
            self.pickaxes_left = max(0, self.pickaxes_left - 1)
        if self.on_pickaxe:
            self.on_pickaxe(event[0], event[1], self.pickaxes)

    def _panel_quantity(self, window, name: str) -> int | None:
        """The 「× N」 on a panel line. Unknown stays None — never a fabricated 1."""
        if self._mining_renderer is None or self.mining_format is None:
            return None
        from .capture.glyph import recognize_quantity

        after = (self.mining_format.raw.get("drop_item") or "").split("{0}")[-1]
        sep = self._separator()
        qty, _margin = recognize_quantity(
            window, self._mining_renderer, self.mining_prefix, name, after, separator=sep,
        )
        return qty

    def _emit_mining(self, lines, now, unread: int = 0, tie_broken: int = 0) -> None:
        """One panel = one observation.

        Emitted directly rather than through the episode machine: an episode exists to group
        a chest's lines, and the panel has already done that grouping itself.
        """
        self.stats["mining"] = self.stats.get("mining", 0) + 1
        # NOT decremented here any more. This counted SWINGS as a placeholder while break
        # detection could not be trusted, and it over-counted by an unknown factor: one vein
        # measured gave five yields for a single pickaxe. Breaks are now read directly, so
        # the count falls when a pickaxe actually breaks -- which is also what the player
        # sees, and what they told us was wrong about the counter.
        contents = []
        for name, quantity in lines:
            entry = {"item_name": name, "raw_text": self._as_mining_line(name)}
            if quantity is None:
                entry["quantity"], entry["qty_unknown"] = 1, True
            else:
                entry["quantity"] = quantity
            contents.append(entry)
        started = self.tracker.started_at
        event = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "occurred_at": now.isoformat(),
            "provenance": "mining",
            "contents": contents,
            # An unread line is a missing item, and a panel with one is not a complete
            # observation -- saying so is what lets the analysis exclude it on evidence.
            "qc": ({"fps": self.fps} if self.fps else {})
            | ({"panel_lines_unread": unread} if unread else {})
            # Recorded so a reading that needed the tie-breaker is identifiable. It resolves
            # graded families, and a grade is exactly the kind of thing worth auditing.
            | ({"panel_lines_tie_broken": tie_broken} if tie_broken else {}),
            "dive": {
                "dive_id": str(self.tracker.dive_id) if self.tracker.dive_id else str(uuid4()),
                "started_at": (started or now).isoformat(),
                "elapsed_seconds": int((now - started).total_seconds()) if started else 0,
                "dungeon_id": self._dungeon_id,
                "floor_id": self._floor_id,
                "label_source": "user_declared",
            },
        }
        self.on_event(event)
        if self.on_mining:
            self.on_mining(event, self.pickaxes_left)

    def _as_mining_line(self, name: str) -> str:
        template = (self.mining_format.raw.get("drop_item") if self.mining_format else None)
        return template.replace("{0}", name) if template else name

    def _record(self, image, hud_present: bool, now) -> None:
        """Save frames for offline replay.

        `episodes` (default) keeps only HUD-absent frames — chests, battles and dialogue —
        which is where everything interesting happens and is a small fraction of a session.
        `all` keeps everything and gets large fast, so both are capped.
        """
        if self.record_dir is None or self._recorded >= self.record_limit:
            return

        if not hud_present and not self._in_episode:
            # Episode opening: start a new folder and flush the frames that led up to it.
            self._in_episode = True
            self._episode_index += 1
            self._episode_frame = 0
            for buffered in self._pre_context:
                self._write(buffered)
            self._pre_context.clear()
            self._post_context = self.record_context
        elif hud_present and self._in_episode:
            # Episode closing: keep a short trailing run so the close is captured too.
            self._in_episode = False
            self._post_context = self.record_context

        if hud_present:
            if self._post_context > 0:
                self._post_context -= 1
            elif self.record_mode == "all":
                self._write(image)
                return
            else:
                self._pre_context.append(image)
                del self._pre_context[: -self.record_context]
                return
        self._write(image)

    def _write_manifest(self, start, dungeon_id, floor_id) -> None:
        """Describe the recording next to its frames.

        Without this a recording does not say what rate it was captured at, and replaying it
        at a different one silently rescales every timestamp -- `elapsed_seconds` is frame
        index / fps, and elapsed time is the variable the whole study turns on. The profile
        details are stored too, so a recording read back months later can be checked against
        the calibration that produced it.
        """
        if self.record_dir is None:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "started_at": start.isoformat(),
            "fps": self.fps,
            "dungeon_id": dungeon_id,
            "floor_id": floor_id,
            "record_mode": self.record_mode,
            "frame_size": list(self.profile.frame_size),
            "profile": {
                "message_band": list(self.profile.message_band),
                "font_size": self.profile.font_size,
                "letter_spacing": getattr(self.profile, "letter_spacing", 0.0),
                "offset": list(self.profile.offset),
                "calibration_score": self.profile.calibration_score,
            },
        }
        (self.record_dir / "session.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def _episode_dir(self):
        return self.record_dir / f"episode-{max(1, self._episode_index):03d}"

    def _drain_writes(self) -> None:
        """The writer thread: encode and save whatever the capture loop hands over."""
        while True:
            item = self._write_queue.get()
            try:
                if item is None:
                    return
                image, target = item
                try:
                    image.save(target, compress_level=RECORD_COMPRESS_LEVEL)
                except Exception as exc:                       # noqa: BLE001
                    log.warning("wddrop: could not write %s: %s", target.name, exc)
            finally:
                self._write_queue.task_done()

    def _stop_writer(self) -> None:
        """Let the queue finish before the session is declared over."""
        if getattr(self, "_writer", None) is None:
            return
        self._write_queue.put(None)
        self._writer.join(timeout=30)
        self._writer = None
        if self.stats.get("record_dropped"):
            log.warning("wddrop: %d recorded frame(s) dropped — the disk could not keep up. "
                        "Capture was not affected; the recording has gaps.",
                        self.stats["record_dropped"])

    def _write(self, image) -> None:
        if self._recorded >= self.record_limit:
            return
        target = self._episode_dir()
        target.mkdir(parents=True, exist_ok=True)
        self._recorded += 1
        self._episode_frame = getattr(self, "_episode_frame", 0) + 1
        # Updated here, not at the end of run(): Ctrl-C is the NORMAL way to stop a session,
        # and it skipped the end-of-run assignment, so a run that recorded 703 frames
        # reported 0.
        self.stats["recorded"] = self._recorded
        # Greyscale is exactly what the pipeline consumes (everything converts to "L"), so
        # this is lossless for replay while being ~3x smaller than RGB. 1.2 GB for a short
        # session is otherwise easy to hit.
        #
        # `optimize=True` COST A MINING RESULT. It re-encodes with several filter strategies
        # to shave the last few percent, and it runs on the capture thread, so every frame
        # written delayed the next sample. Measured on the frames of a real session:
        #
        #     1920x1080   optimize=True 624ms / 177KB    compress_level=1  21ms / 285KB
        #      704x1241   optimize=True 348ms / 321KB    compress_level=1  17ms / 385KB
        #
        # A player asking for 20fps got 2.5 at 1080 and 5.3 at 704x1241, with a median 448ms
        # between frames — and a mining panel dismissed inside that window was never sampled
        # at all. Nothing downstream can recover a frame that was not taken. Trading ~60% more
        # bytes for 30x the sampling rate is not close.
        if getattr(self, "_writer", None) is None:
            import queue

            self._write_queue = queue.Queue(maxsize=RECORD_QUEUE_MAX)
            self._writer = threading.Thread(target=self._drain_writes, name="wddrop-record",
                                            daemon=True)
            self._writer.start()
        try:
            self._write_queue.put_nowait(
                (image.convert("L"), target / f"f_{self._episode_frame:05d}.png"))
        except Exception:                                  # queue.Full — never block capture
            self._recorded -= 1
            self._episode_frame -= 1
            self.stats["record_dropped"] = self.stats.get("record_dropped", 0) + 1
            return
        if self._recorded == self.record_limit:
            log.warning("wddrop: recording cap of %d frames reached; stopping recording",
                        self.record_limit)

    def _check_frame_size(self, size) -> None:
        """Refuse to run on a resolution the profile was not fitted to.

        Every region in the profile is absolute pixels, so a different resolution does not
        degrade gracefully — it reads the wrong part of the screen and silently records
        nothing, or worse, nonsense. Resizing the game window after calibrating is an easy
        mistake to make (the reference screenshots for this build arrived at two different
        resolutions), so it is caught rather than assumed away.
        """
        expected = tuple(self.profile.frame_size)
        if tuple(size) != expected:
            raise SystemExit(
                f"[!] frame is {size[0]}x{size[1]} but the profile was calibrated at "
                f"{expected[0]}x{expected[1]}.\n"
                f"    If the game runs WINDOWED, capture the window rather than the whole\n"
                f"    monitor — that is usually this exact mismatch:\n"
                f"        --source window\n"
                f"        wddrop windows            (to find the title, then --source window:<title>)\n"
                f"    Otherwise re-run `calibrate` at the resolution you actually play at."
            )

    def _read_band(self, gray, now) -> str:
        """Recognise the message band, skipping work wherever possible.

        Two facts from real captures drive this, and the obvious implementation gets both
        wrong:

        1. HASH THE INK, NOT THE PIXELS. The message renders over a live 3D scene, so the
           raw band differs every frame even when the text is identical — a raw hash reports
           "still animating" forever and nothing is ever recognised. Measured on a real
           chest: consecutive frames of the same line had identical INK MASKS but different
           raw pixels, and the session recorded zero drops because of it.

        2. A LINE IS USUALLY REPLACED, NOT BLANKED. In a multi-item chest each message
           overwrites the last, so waiting for the band to go blank misses every line but
           the final one.

           Replacement is detected by asking how much of the PREVIOUS ink survived, not by
           whether the ink total went down. Ink volume is not a valid signal: a replacing
           line is often LONGER than the one it replaced (measured: 528 -> 719 px of ink),
           so a drop test never fires and the line is lost. Survival separates the two
           cleanly — on real frames, the same line re-drawn removes 0.0% of its ink, while a
           replacement removes 56% and 95%.
        """
        import numpy as np

        from .capture.glyph import INK_LEVEL, anchor_window

        top, bottom = tuple(self.profile.message_band)
        band = np.asarray(gray, dtype=np.uint8)[top:bottom, :]
        mask = band > INK_LEVEL
        ink = int(mask.sum())

        if ink < MIN_BAND_INK_PIXELS:
            self.stats["skipped_blank"] += 1
            text = self._flush_pending(now)
            self._last_band_key = self._recognised_key = None
            self._last_mask = None
            return text

        key = mask[::2, ::2].tobytes()

        if key == self._recognised_key:
            self.stats["skipped_same"] += 1
            return self._last_text

        if key == self._last_band_key:
            # Held still and not yet read: this is the settled line.
            window = anchor_window(gray, (top, bottom), tuple(self.profile.window),
                                   x0_fixed=getattr(self.profile, "text_x0", None))
            # Report the FIRST frame the line appeared on, not the second. Recognition fires
            # once the band has held still, i.e. on the second frame -- but the frame worth
            # opening is the one where the line first showed, which is what a person
            # scrubbing the recording will point at.
            first_src = self._pending[3] if (self._pending and len(self._pending) > 3) else None
            self._pending = None
            self._last_mask = mask
            if window is None:
                return ""
            return self._recognise(window, now, key=key, frame_src=first_src)

        # Content changed. If the previous line was REPLACED rather than extended, read it
        # now or lose it; if its ink survived, the typewriter is simply still going.
        text = ""
        if self._pending is not None and self._last_mask is not None:
            prev = self._last_mask
            prev_ink = int(prev.sum())
            removed = int((prev & ~mask).sum()) / prev_ink if prev_ink else 1.0
            if removed >= REPLACED_INK_FRACTION:
                text = self._flush_pending(now)

        window = anchor_window(gray, (top, bottom), tuple(self.profile.window))
        from .capture.glyph import ink_bbox
        box = ink_bbox(band)
        obs_width = (box[2] - box[0]) if box else 0
        # The pending frame's OWN source is carried with it. A line recovered by the flush
        # path is recognised from a window captured earlier, so stamping the current frame
        # pointed at the blank frame that triggered the flush rather than the one showing
        # the text -- off by one, in the direction that makes the evidence useless.
        self._pending = (key, window, obs_width, self._frame_src) if window is not None else None
        self._last_band_key = key
        self._last_mask = mask
        self.stats["skipped_animating"] += 1
        return text

    @staticmethod
    def _short_source(path: str) -> str:
        """episode-006/f_00069.png -- enough to find the frame, short enough to read."""
        parts = Path(path).parts
        return "/".join(parts[-2:]) if len(parts) >= 2 else Path(path).name

    def _flush_pending(self, now) -> str:
        """Read a line that vanished or was replaced before it ever held still."""
        pending, self._pending = self._pending, None
        if pending is None or pending[1] is None:
            return ""
        text = self._recognise(
            pending[1], now, key=None, strict=True,
            observed_width=pending[2] if len(pending) > 2 else None,
            frame_src=pending[3] if len(pending) > 3 else None,
        )
        if text:
            self.stats["recognised_on_vanish"] += 1
        return text

    def _recognise(self, window, now, key: bytes | None, strict: bool = False,
                   observed_width: int | None = None, frame_src: str | None = None) -> str:
        # NOTE: the pickaxe messages are NOT read here. They appear in the mining PANEL --
        # 「北穿的黃金十字鎬壞掉了」 was found there, at the panel's size -- and a three-candidate
        # index run against the message band produced two false hits in a session containing
        # no mining at all. See _read_panel.
        match = self.recognizer.recognize(window)
        accepted = match.accepted and match.name
        if accepted and strict and observed_width:
            shortfall = match.template_width - observed_width
            accepted = shortfall <= VANISH_MAX_WIDTH_SHORTFALL_PX
            if not accepted:
                log.debug("wddrop: vanished line %r refused: name renders %dpx but only "
                          "%dpx was on screen", match.name, match.template_width, observed_width)
        text = ""
        if accepted:
            self.stats["recognised"] += 1
            self._read_quantity(window, match.name)
            src = frame_src or self._frame_src
            if src:
                self._sources[match.name] = self._short_source(src)
            text = self._as_line(match.name)
        elif match.score >= QUEUE_MIN_SCORE and self.review_queue is not None:
            self._queue(match, now)
        if key is not None:
            self._recognised_key, self._last_text = key, text
        return text

    def _read_quantity(self, window, name: str) -> None:
        """Read the quantity once the name is fixed; cache it for the emit step.

        Quantity is deliberately a SECOND stage: identifying the name from `prefix + name` is
        quantity-independent, and only once the name is known is it possible to say where the
        digits start. A quantity that cannot be read stays None rather than defaulting to 1 —
        equipment lines legitimately carry no quantity, and so do boosted item lines.
        """
        # Retry until a read SUCCEEDS rather than caching the first attempt. The name
        # finishes drawing before its 「× N！！」 tail does, so the earliest frames where the
        # name is recognisable still have no quantity on screen — caching that failure would
        # permanently record a quantity-less line as 1.
        if self.renderer is None or self._quantities.get(name) is not None:
            return
        from .capture.glyph import recognize_quantity

        after = self._template_after()
        sep = self._separator()
        try:
            qty, _margin = recognize_quantity(
                window, self.renderer, self.prefix, name, after,
                separator=sep, offset=tuple(self.profile.offset),
            )
        except Exception as exc:
            log.debug("wddrop: quantity read failed for %r: %s", name, exc)
            qty = None
        if qty is not None:
            self._quantities[name] = qty

    def _queue(self, match, now) -> None:
        from .review import Candidate

        self.stats["queued"] += 1
        cands = [Candidate(name=match.name or match.runner_up or "", score=round(match.score, 4))]
        if match.runner_up:
            cands.append(Candidate(name=match.runner_up, score=round(match.score - match.margin, 4)))
        self.review_queue.add(
            match.runner_up or "?", "", cands, occurred_at=now,
        )

    def _template_after(self) -> str:
        import re

        tpl = self.fmt.raw.get("drop_item") or "{0}"
        clean = re.sub(r"<[^>]+>", "", re.sub(r"^Msg@", "", tpl))
        return clean.split("{0}")[1] if "{0}" in clean else ""

    def _separator(self) -> str:
        import re

        tpl = self.fmt.raw.get("name_and_quantity") or "{0}\u00d7{1}"
        parts = re.split(r"\{\d\}", tpl)
        return parts[1] if len(parts) > 2 else "\u00d7"

    # -- helpers -----------------------------------------------------------------
    @staticmethod
    def _clock(start: datetime, offset: float) -> datetime:
        from datetime import timedelta

        return start + timedelta(seconds=offset)

    def _as_line(self, name: str) -> str:
        """Rebuild the full message line from the recognised name.

        The recogniser identifies the NAME; the episode machine and its guards (termination,
        stability, dedup) are written against complete lines. Reconstructing through the same
        locale template keeps a single set of rules rather than a parallel one.
        """
        tpl = self.fmt.raw.get("drop_item") or "{0}"
        import re
        clean = re.sub(r"<[^>]+>", "", re.sub(r"^Msg@", "", tpl))
        return clean.replace("{0}", name)

    def _emit(self, obs, dungeon_id: int, floor_id: int | None) -> None:
        self.stats["chests"] += 1
        contents = []
        for line in obs.lines:
            qty = self._quantities.get(line.name)
            entry = {"item_name": line.name, "raw_text": line.raw}
            src = self._sources.get(line.name)
            if src:
                entry["source_frame"] = src
            if qty is None:
                # Unknown, not assumed. Recording a fabricated 1 where the true value was 3
                # would corrupt the data far more quietly than an explicit gap.
                entry["quantity"] = 1
                entry["qty_unknown"] = True
            else:
                entry["quantity"] = qty
            contents.append(entry)
        # Per-chest caches: clear them so the next chest reads its own values.
        self._quantities.clear()
        self._sources.clear()

        qc = {"fps": self.fps} if self.fps else {}
        if self.dungeon_hints is not None:
            check = self.dungeon_hints.check(dungeon_id, [c["item_name"] for c in contents])
            qc.update(check)
            if check.get("label_conflict"):
                # Logged as a warning, not swallowed: the player can still fix the label
                # while the session is running, and afterwards nobody can.
                log.warning("wddrop: %s", self.dungeon_hints.describe_conflict(dungeon_id, check))
        event = {
            "schema_version": 1,
            **({"truncated": True} if getattr(obs, "truncated", False) else {}),
            "event_id": str(uuid4()),
            "occurred_at": obs.occurred_at.isoformat(),
            "provenance": "chest_direct",
            "contents": contents,
            # Sampling rate travels with every event: a line dismissed faster than one
            # sample interval is never seen, so the rate bounds what the record can contain
            # and belongs in QC rather than in someone's memory of how it was run. The
            # dungeon the CONTENTS point at rides along for the same reason — evidence about
            # a label is only useful if it outlives the session that produced it.
            "qc": qc,
            "dive": {
                "dive_id": str(self.tracker.dive_id) if self.tracker.dive_id else str(uuid4()),
                "started_at": self.tracker.started_at.isoformat() if self.tracker.started_at else obs.occurred_at.isoformat(),
                "elapsed_seconds": obs.elapsed_seconds,
                "dungeon_id": dungeon_id,
                "floor_id": floor_id,
                "chest_index_in_dive": obs.chest_index,
                "label_source": "user_declared",
            },
        }
        self.on_event(event)

    @staticmethod
    def _spool(event: dict) -> None:
        """Write the event twice, to two files with two different jobs.

        The spool is an OUTBOX and the uploader empties it; the records file is the
        player's own copy and nothing removes from it. One file cannot be both — draining
        the outbox would take the player's data with it, which is what it did.
        """
        from .config import records_path, spool_path

        line = json.dumps(event, ensure_ascii=False) + "\n"
        for path in (spool_path(), records_path()):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)


def record_stop_reason(dive_id: str | None, reason: str | None, path=None) -> int:
    """Stamp how the session ended onto the events it already produced.

    The reason is only known once the session ends, but its events were spooled as they
    happened — spool-first is what makes a crash survivable, so buffering them until the
    end to fill one field in would trade a real guarantee for a small convenience. They are
    therefore stamped afterwards, and only ever the ones carrying this dive_id.

    Rewritten through a temporary file and an atomic replace: the spool is the player's only
    copy of unsent data, so a crash halfway through this must leave the original intact.
    Returns how many events were stamped.
    """
    from .config import spool_path

    if not dive_id or not reason:
        return 0
    path = Path(path) if path else spool_path()
    if not path.exists():
        return 0
    out, stamped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            # A line we cannot parse is kept untouched rather than dropped; the uploader
            # already treats malformed lines as recoverable-after-a-fix.
            out.append(line)
            continue
        dive = event.get("dive") or {}
        if dive.get("dive_id") == dive_id and not dive.get("stop_reason"):
            dive["stop_reason"] = reason
            event["dive"] = dive
            stamped += 1
            line = json.dumps(event, ensure_ascii=False)
        out.append(line)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    tmp.replace(path)
    return stamped

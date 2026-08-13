"""
Session and episode state machine for OCR mode.

This is the client's control flow, derived from what the recordings actually show rather
than from assumptions about the UI:

    walking (minimap HUD present)
        │  HUD disappears
        ▼
    EPISODE  — battle / npc / chest / trap. The HUD is gone for ALL of these.
        │      watch the calibrated text region
        │      「打開」 seen  -> this episode involves a chest
        │      獲得了… lines -> collect them
        │  HUD returns
        ▼
    walking again  -> emit a ChestObservation if the chest actually paid out

Two orderings that were verified on frame samples, and that the design depends on:

* The HUD is ALREADY GONE before 「打開」 appears (t=9s: chest interaction on screen,
  minimap absent; t=28.5s walking, minimap present). So "HUD disappeared" cannot end a
  chest episode — it happens before the chest starts. 「打開」 opens the episode's chest
  phase; the HUD RETURNING closes it.
* During walking there is nothing to read, which is most of a session. Gating the expensive
  OCR path on `hud_present == False` is what keeps the client cheap.

THE CANCELLED-CHEST TRAP
------------------------
Seeing 「打開」 is NOT evidence a chest was opened: the same prompt offers 「什麼都不做」
(do nothing). Emitting an observation just because the prompt appeared would fabricate a
zero-drop chest every time a player walks up to one and declines — biasing measured drop
rates DOWNWARD. So an observation is emitted only on positive evidence of an outcome:

    at least one 獲得了… line   -> chest paid out
    the DropEmpty message       -> chest opened and was genuinely empty

An empty chest IS recorded (it is a real observation and the worst outcome); a declined
chest is not recorded at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from .ocr import MessageFormat, MessageLineReader, ParsedLine

log = logging.getLogger("wddrop.episodes")

# How long the HUD may stay absent before we assume the player left the dungeon rather than
# being in a long battle. Boss fights are long, so this is deliberately generous; the
# session-level idle timeout is the real backstop.
MAX_EPISODE_SECONDS = 20 * 60

# Fallback: close an episode this long after its last drop line, even if the HUD never comes
# back. The HUD signal is the primary bracket, but making it the ONLY one means a detector
# that silently never fires produces zero chests forever — which is exactly what happened on
# the first live Steam run (hud_present=0, recognised=10, chests=0). A second, independent
# closing condition turns that from "no data, no explanation" into "data, slightly coarser".
EPISODE_IDLE_CLOSE_SECONDS = 8.0


@dataclass
class ChestObservation:
    """One chest that actually produced an outcome."""

    chest_index: int
    elapsed_seconds: int
    occurred_at: datetime
    lines: list[ParsedLine] = field(default_factory=list)
    is_empty: bool = False
    # The session ended before this chest's dialogue closed, so its item list may be short.
    truncated: bool = False
    # NOTE: `saw_open_prompt` is deliberately NOT carried here. It is tracked on the episode
    # but is FALSE for every chest measured, because 「打開」 renders in the action-button
    # area rather than in the calibrated message band — the same place the mining panel and
    # the pickaxe messages turned out to live. Emitting a field that is always false would
    # look like evidence that no chest was ever opened.


@dataclass
class _Episode:
    started_at: datetime
    saw_open_prompt: bool = False
    lines: list[ParsedLine] = field(default_factory=list)
    saw_empty_message: bool = False


class EpisodeTracker:
    """Feed it ticks; it calls `on_chest` for each chest that paid out (or was empty).

    The tracker is deliberately ignorant of *which kind* of chest it saw. Both
    randomly-placed map chests and enemy-dropped chests are collection targets, so there is
    nothing to distinguish and no classification to get wrong.
    """

    def __init__(
        self,
        fmt: MessageFormat,
        open_prompt: str,
        on_chest,
        *,
        stable_frames: int = 2,
        idle_close_seconds: float = EPISODE_IDLE_CLOSE_SECONDS,
    ):
        self.idle_close_seconds = idle_close_seconds
        self._last_line_at: datetime | None = None
        self.fmt = fmt
        # The localised 「打開」 string (Common@Open) for the player's locale.
        self.open_prompt = (open_prompt or "").strip()
        self.on_chest = on_chest
        self.reader = MessageLineReader(fmt, stable_frames=stable_frames)

        self.dive_id: UUID | None = None
        self.started_at: datetime | None = None
        self.chest_index = 0
        self._episode: _Episode | None = None
        self._last_activity: datetime | None = None

    # -- session -----------------------------------------------------------------
    def start_session(self, now: datetime) -> UUID:
        self.dive_id = uuid4()
        self.started_at = now
        self.chest_index = 0
        self._episode = None
        self._last_activity = now
        # A new session starts from nothing: line state left over from the previous one
        # would suppress this session's first line if it happened to be identical.
        self.reader.reset()
        log.info("wddrop: session %s started", self.dive_id)
        return self.dive_id

    def stop_session(self, now: datetime) -> None:
        # An episode still open at stop time is emitted, FLAGGED as possibly truncated,
        # rather than discarded.
        #
        # Discarding was the earlier behaviour and it loses a whole chest -- which is what
        # happens every time a player stops with Ctrl-C just after opening one, and the
        # reason a session reported 7 chests when 8 were opened. Neither option is clean:
        # the content list may genuinely be incomplete. But a chest recorded with a
        # "truncated" marker can be excluded at analysis time on evidence, whereas a chest
        # that was never recorded is indistinguishable from one that never happened.
        if self._episode is not None:
            log.info("wddrop: session stopped mid-episode; emitting it as truncated")
            self._close_episode(now, truncated=True)
        self._episode = None
        self.dive_id = None
        self.started_at = None

    @property
    def active(self) -> bool:
        return self.dive_id is not None

    def idle_seconds(self, now: datetime) -> float:
        if self._last_activity is None:
            return 0.0
        return (now - self._last_activity).total_seconds()

    # -- per-frame ---------------------------------------------------------------
    def tick(self, now: datetime, hud_present: bool, region_text: str = "") -> None:
        """One sampled frame.

        `region_text` is the OCR of the calibrated text region. It is only consulted while
        the HUD is absent, so callers may skip OCR entirely when `hud_present` is True.
        """
        if not self.active:
            return

        if hud_present:
            self._close_episode(now)
            self._last_activity = now
            return

        if self._episode is None:
            self._episode = _Episode(started_at=now)
            self._last_line_at = None
            # Deliberately NOT resetting the reader here. Whoever ended the previous episode
            # already reset it, and only that code knows whether the message left the screen
            # — resetting again here threw that judgement away and re-created the duplicate
            # it was meant to prevent.

        # Close on idle if the episode already produced something and nothing has arrived
        # since. Without this the chest is only emitted when the HUD returns.
        #
        # The message may still be ON SCREEN when this fires — that is the normal case, in
        # fact: a player who walks away leaves it up indefinitely, and the idle timer starts
        # from the last line, not from the player dismissing it. So the reader keeps its
        # memory of that line (dialogue_ended=False); otherwise the next frame re-reads the
        # same text as new, the new episode closes 8s later, and the same chest is recorded
        # over and over for as long as the screen is left alone.
        if (
            self._last_line_at is not None
            and (self._episode.lines or self._episode.saw_empty_message)
            and (now - self._last_line_at).total_seconds() >= self.idle_close_seconds
        ):
            self._close_episode(now, dialogue_ended=False)
            return

        # A pathologically long episode means we probably missed the HUD returning.
        # Abandon it rather than let it absorb a later chest's lines.
        if (now - self._episode.started_at).total_seconds() > MAX_EPISODE_SECONDS:
            log.warning("wddrop: episode exceeded %ds, abandoning", MAX_EPISODE_SECONDS)
            self._episode = None
            # A full reset here: 20 minutes on, whatever was on screen is long gone, and
            # suppressing the next real line as a "duplicate" of it would lose a chest.
            self.reader.reset()
            return

        text = (region_text or "").strip()
        if not text:
            return

        if self.open_prompt and self.open_prompt in text:
            self._episode.saw_open_prompt = True
            self._last_activity = now

        parsed = self.reader.feed(text)
        if parsed is None:
            return
        self._last_activity = now
        self._last_line_at = now
        if parsed.is_empty:
            self._episode.saw_empty_message = True
        else:
            self._episode.lines.append(parsed)

    # -- internals ---------------------------------------------------------------
    def _close_episode(self, now: datetime, truncated: bool = False,
                       dialogue_ended: bool = True) -> None:
        ep, self._episode = self._episode, None
        self._last_line_at = None
        if ep is None:
            return
        # `dialogue_ended` says whether the message is actually GONE from the screen. When
        # the HUD comes back it is; when the idle fallback fires it may well not be, and
        # forgetting the last line there makes the still-displayed text read as new.
        self.reader.reset(keep_last_line=not dialogue_ended)

        # Positive evidence only — see the cancelled-chest trap in the module docstring.
        paid_out = bool(ep.lines)
        empty = ep.saw_empty_message and not ep.lines
        if not paid_out and not empty:
            return

        self.chest_index += 1
        self.on_chest(
            ChestObservation(
                chest_index=self.chest_index,
                elapsed_seconds=int((now - self.started_at).total_seconds()),
                occurred_at=now,
                lines=list(ep.lines),
                is_empty=empty,
                truncated=truncated,
            )
        )

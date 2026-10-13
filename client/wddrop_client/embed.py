"""Reading drops from inside another program.

The client normally owns the whole loop: it finds the game, decides when a dive began, reads
the lines and records them. A bot already knows most of that better than the client can guess
it — it decided to enter the dungeon, it knows which floor it walked to, and it knows its
route finished rather than the player losing interest. So this module splits the work where
the knowledge actually is:

    the caller knows WHEN and WHERE          -- it opens and closes a dive, naming the floor
    this client knows WHAT WAS FOUND         -- it reads the line and owns the record

Reading is PURE. `read_drop_line` takes pixels and returns what it saw or why it would not
say; it writes nothing. That is what makes a read rate measurable offline over a recording,
which is the only honest way to find out whether a change helped.

Recording is NOT pure and is deliberately not the caller's to do. A record carries the
install_id that is the player's only erasure handle, the client and schema versions the
server checks, and it has to reach two files with two different jobs — an outbox that gets
drained and an archive that never does. One implementation of that, here, where the rule is
already written down.

THE DIVE CONTEXT TRAVELS ON EVERY RECORD, not in a file of its own. The server has no dives
table: `dive_id`, `dungeon_id` and `floor_id` are columns on the drop event itself, so a
separate dive log — however sensibly the caller keeps one — cannot reach the study. What the
caller knows has to be handed in at `open_dive`, and it is stamped onto each drop from there.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:                                   # pragma: no cover
    from .calibration import Profile

__all__ = ["Geometry", "Refusal", "DropLine", "EmptyChest", "Refused", "LineReader",
           "read_drop_line", "Recorder", "DiveHandle", "DEFAULT_FACE"]

#: The face this screen is drawn in. See `Geometry.font_path` for what the other one costs.
DEFAULT_FACE = "atlas.ja.scenario.json"


@dataclass(frozen=True)
class Geometry:
    """Where to look, and how big the text is.

    `size` is a SEED, not a measurement. It is computed from the window size and matches every
    size that has been measured by hand — but a size one pixel out does not misread, it reads
    NOTHING: on twelve real lines the right size accepted all twelve at 0.87 and one pixel
    under accepted none at 0.37. A caller that sees `Refusal.BELOW_GATE` on every line should
    try `size` a pixel either way before concluding the game is not showing drops.
    """

    band: tuple[int, int]
    text_x0: int
    size: int
    columns: tuple[int, int]
    window: tuple[int, int]
    letter_spacing: float = 0.0
    #: THE TYPEFACE, which is part of the geometry and not a detail left to the caller.
    #: The game ships a scenario twin of its UI font and this screen is drawn in the scenario
    #: one. Reading it in the other costs about 0.1 of score and most of the margin — measured
    #: over 208 real frames, the right face read 0.8724 against 0.7603, with mean margin
    #: 0.3149 against 0.2009 — and a whole class of long names fell under the margin gate
    #: because of it. It was a free parameter on both sides, which is exactly how it got
    #: picked wrong and stayed wrong without ever looking broken.
    font_path: str = DEFAULT_FACE

    @classmethod
    def for_window(cls, frame_size, *, locale: str = "ja", font_path: str = DEFAULT_FACE,
                   windows: bool = True, safe_area=None) -> "Geometry":
        """Computed from the window size alone — no calibration step for the caller.

        `windows` is NOT cosmetic: the Windows client draws this panel at 0.898 of the size
        every other platform does, so leaving it true on a phone shifts the pen and shrinks
        the text together. It defaulted true with no way to say otherwise, which is wrong on
        any device — pass `windows=False` off Windows.
        """
        from .calibration import Profile

        p = Profile.derived(frame_size, font_path=font_path or DEFAULT_FACE, locale=locale,
                            windows=windows, safe_area=safe_area)
        return cls.from_profile(p)

    @classmethod
    def from_profile(cls, profile: "Profile") -> "Geometry":
        """From a calibration that already exists, which beats a computed one where there is
        one: the three measured window sizes are measurements and this is not."""
        from .calibration import read_columns

        cols = read_columns(profile) or (0, int(profile.frame_size[0]))
        return cls(band=tuple(profile.message_band), text_x0=int(profile.text_x0 or 0),
                   size=int(profile.font_size), columns=cols,
                   window=tuple(profile.window), letter_spacing=profile.letter_spacing,
                   font_path=profile.font_path)


class Refusal(str, enum.Enum):
    """Why no name was returned. THE CALLER MUST TELL THESE APART.

    `UNTERMINATED` and `BELOW_GATE` look identical from outside and mean opposite things. A
    line still being typed out will finish on a later frame, so waiting is exactly right. A
    line that scores under the gate will score under it on every frame of the same geometry,
    so waiting is a loop that never ends — the size is wrong and something has to change.
    """

    NO_INK = "no_ink"                   # nothing is written in the band; not an error
    UNTERMINATED = "unterminated"       # still being typed out — READ THE NEXT FRAME
    BELOW_GATE = "below_gate"           # scored too low — THE GEOMETRY IS WRONG, waiting will not help
    AMBIGUOUS = "ambiguous"             # two candidates too close to choose between


@dataclass(frozen=True)
class DropLine:
    """One line that was read, and how well."""

    name: str
    quantity: int | None
    score: float
    margin: float
    truncated: bool = False


@dataclass(frozen=True)
class EmptyChest:
    """A chest that said it held nothing, READ rather than inferred.

    「しかし、何も入っていなかった……」 carries no 「を手に入れた!!」, so the rule that proves a
    drop line finished cannot prove this one — without recognising it outright, an empty chest
    looks exactly like a line still being typed and would be waited on forever.

    It matters that this is positive evidence. An empty chest is a real outcome and the rate
    of them is part of what the study measures, so recording one on "we saw nothing" would be
    counting a failure to read as a result.
    """

    score: float


@dataclass(frozen=True)
class Refused:
    """No name, and why. `reason` decides whether another frame can help — see `Refusal`."""

    reason: Refusal
    score: float = 0.0
    best: str | None = None     # what it nearly said, for a log; NEVER record this

    @property
    def wait_for_another_frame(self) -> bool:
        return self.reason is Refusal.UNTERMINATED


class LineReader:
    """Holds the rendered vocabulary, which is expensive to build and cheap to reuse.

    Building the index means rendering every name in the vocabulary at one size — thousands of
    them — so it is built once per (size, spacing) and kept. A caller sweeping the seed size
    makes one of these per size and keeps whichever reads.
    """

    #: What the game writes when a chest held nothing.
    EMPTY_TEXT = "しかし、何も入っていなかった……"
    #: How well the empty line must match before it is believed. Deliberately high: calling a
    #: chest empty is recording an outcome, and the cost of a false one is a fabricated zero.
    EMPTY_MIN_SCORE = 0.70

    def __init__(self, geometry: Geometry, names: list[str], atlas_path=None, *,
                 after: str = "を手に入れた!!", quantity_mark: str = "×",
                 empty_text: str | None = None):
        from .capture import glyph as g

        self._g = g
        self.geometry = geometry
        # THE GEOMETRY'S FACE unless the caller insists. Passing one here that disagrees with
        # the geometry is how the wrong typeface was used for a whole run without anything
        # looking broken — the names still read, a tenth lower, and the long ones stopped.
        atlas_path = atlas_path or geometry.font_path or DEFAULT_FACE
        self.after = after
        self.quantity_mark = quantity_mark
        window = g.required_window(
            g.make_renderer(atlas_path, geometry.size, (2400, 90), geometry.letter_spacing),
            "", names)
        self._window = window
        self._renderer = g.make_renderer(atlas_path, geometry.size, window,
                                         geometry.letter_spacing)
        self._index = g.RenderRecognizer(self._renderer, "", names)
        self.empty_text = self.EMPTY_TEXT if empty_text is None else empty_text
        self._empty = (g.RenderRecognizer(self._renderer, "", [self.empty_text])
                       if self.empty_text else None)

    def read(self, frame_gray) -> "DropLine | EmptyChest | Refused":
        """One frame in, one answer out. Writes nothing, keeps nothing."""
        import numpy as np

        g = self._g
        gray = np.asarray(frame_gray, dtype=float)
        top, bottom = self.geometry.band
        if gray.shape[0] < bottom:
            return Refused(Refusal.NO_INK)

        window = g.anchor_window(gray, (top, bottom), self._window,
                                 x0_fixed=self.geometry.text_x0 or None,
                                 columns=self.geometry.columns)
        if window is None:
            return Refused(Refusal.NO_INK)

        named, cut = g.mask_after_name(window, self._renderer, self.after,
                                       self.quantity_mark)
        if not cut:
            # Before calling it unfinished: an empty chest never shows the tail at all, so it
            # would be waited on forever by a rule that only knows about drops.
            if self._empty is not None:
                m = self._empty.recognize(window)
                if float(getattr(m, "score", 0.0)) >= self.EMPTY_MIN_SCORE:
                    return EmptyChest(score=float(m.score))
            # The template's trailing wording is the ONLY single-frame proof the line finished
            # being typed. Without it the text on screen is a prefix of what it will say, and a
            # prefix of one name can be a different, complete name — so this is a refusal and
            # not a low score.
            return Refused(Refusal.UNTERMINATED)

        match = self._index.recognize(named)
        score = float(getattr(match, "score", 0.0))
        margin = float(getattr(match, "margin", 0.0))
        if not getattr(match, "accepted", False):
            # Which gate it failed matters to the caller: a near-tie is a vocabulary problem
            # and can differ on the next frame, while a low score at a computed size is the
            # geometry and will not.
            reason = (Refusal.AMBIGUOUS
                      if score >= getattr(self._index, "min_score", 0.0)
                      else Refusal.BELOW_GATE)
            return Refused(reason, score=score, best=getattr(match, "best", None))

        # The quantity is a SECOND read, against the untouched window rather than the one the
        # name was masked out of: where the digits start is only knowable once the name is,
        # so it cannot come out of the same pass. A None here stays None — "I could not read
        # how many" is not a quantity of one, and turning it into one would put a number in
        # the study that nobody measured.
        quantity, _qmargin = g.recognize_quantity(
            window, self._renderer, "", match.best, self.after, self.quantity_mark)
        return DropLine(name=match.best, quantity=quantity, score=score, margin=margin,
                        truncated=bool(getattr(match, "truncated", False)))


def read_drop_line(frame_gray, reader: LineReader) -> "DropLine | EmptyChest | Refused":
    """The pure read, for callers that would rather not hold the reader themselves."""
    return reader.read(frame_gray)


class DiveHandle:
    """One dive, opened by the caller because only the caller knows a dive began.

    The context handed in here is stamped onto every drop recorded through it, because the
    server has no dives table to join a separate log against — see the module docstring.
    """

    def __init__(self, recorder: "Recorder", *, dungeon_id: int | None,
                 floor_id: int | None, label_source: str, started_at: datetime):
        self._recorder = recorder
        self.dive_id = str(uuid4())
        self.dungeon_id = dungeon_id
        self.floor_id = floor_id
        self.label_source = label_source
        self.started_at = started_at
        self._chests = 0
        self._closed = False

    def chest(self, lines: "list[DropLine]", *, floor_id: int | None = None,
              unread: int = 0, occurred_at: datetime | None = None,
              qc: dict | None = None) -> str:
        """Write ONE chest, with everything it showed. Returns its event_id.

        TAKES A LIST BECAUSE A CHEST IS THE UNIT, NOT A LINE. One chest can print several
        messages in a row — a real sighting read 「骸の歪な霊珊を手に入れた!!」 and then
        「落石の巻物を手に入れた!!」 from the same open — and the wire format says so in its
        first lines: the unit of analysis is one chest open with its FULL content list.

        This used to take a single line, which meant a caller doing the obvious thing — call
        it per message — turned one chest into several chests holding one item each. That is
        not a lost record, it is a WRONG one: it would inflate the chest count, deflate the
        items-per-chest, and do it silently, in the two quantities the study exists to measure.

        An empty list is valid and means a chest that yielded nothing. That is a real outcome
        and dropping it would bias the very rate being measured.

        `floor_id` IS PER CHEST, not per dive. A dive crosses floors — down to the farm floor,
        then past others on the way out — and drop tables are keyed per floor, so a dive-level
        floor would label every chest found on the way out with the floor it was collected on.
        Omitted, it falls back to the dive's, which is right only when the dive stayed put.

        `unread` is how many lines were seen being typed but never read. It goes to `qc`, and
        it is not bookkeeping: A CHEST WITH unread > 0 HAS AN INCOMPLETE CONTENT LIST. Counting
        it as a complete observation would understate what chests hold, so analysis has to be
        able to exclude it — which it cannot do if the number is never written down.
        """
        if self._closed:
            raise RuntimeError("this dive is closed; open another before recording into it")
        now = occurred_at or datetime.now(timezone.utc)
        self._chests += 1
        marks = dict(qc or {})
        if unread:
            marks["unread_lines"] = int(unread)
        event = self._recorder.build_event(
            list(lines), dive_id=self.dive_id, started_at=self.started_at, occurred_at=now,
            dungeon_id=self.dungeon_id,
            floor_id=self.floor_id if floor_id is None else floor_id,
            label_source=self.label_source, chest_index=self._chests, qc=marks)
        self._recorder.write(event)
        return event["event_id"]

    def close(self, stop_reason: str = "route_end") -> int:
        """Say how the dive ended, which is only knowable now.

        Stamped onto the events already written rather than held back until the end: records
        are written as they happen so a crash cannot take the dive with it, which means the
        reason has to be backfilled. Returns how many events it reached.
        """
        from .runner import record_stop_reason

        self._closed = True
        return record_stop_reason(self.dive_id, stop_reason)


class Recorder:
    """Builds and writes records. The caller supplies the dive; this supplies everything else.

    Everything else is not small: the install_id that is the player's only handle for erasure,
    the client and schema versions the server validates against, and the two files a record
    has to reach — an outbox the uploader drains and an archive nothing removes from.
    """

    def __init__(self, config=None, *, capture_mode: str = "autopilot", locale: str = "ja",
                 data_dir=None, spool=None, records=None):
        """`data_dir` KEEPS THE BOT'S RECORDS SEPARATE FROM A PLAYER'S, and should be used.

        Left at the default, a bot and a standalone client on one machine share a spool, an
        archive and an install_id. The install_id is the handle a player gives to have their
        data erased, so sharing it means one erasure request covers rows the player never
        knew were theirs — and whichever process drains the spool drains the other's records
        too. Separate directories keep the two accounts, and the two erasure handles, apart.
        """
        from .config import ClientConfig

        # FROM data_dir WHEN ONE IS GIVEN. Loading the machine's own config here would have
        # handed the bot the PLAYER's install_id while writing its records elsewhere — which
        # is the half of the separation that matters, and the half this got wrong until
        # wd-autopilot noticed the player's config being read.
        self.config = config or ClientConfig.load(
            directory=Path(data_dir) if data_dir is not None else None)
        self.capture_mode = capture_mode
        self.locale = locale
        base = Path(data_dir) if data_dir is not None else None
        self._spool = Path(spool) if spool else (base / "spool.jsonl" if base else None)
        self._records = Path(records) if records else (base / "records.jsonl" if base else None)
        if base is not None:
            base.mkdir(parents=True, exist_ok=True)

    def paths(self) -> tuple[Path, Path]:
        """(outbox, archive) — the client's own unless this was given somewhere else."""
        from .config import records_path, spool_path

        return (self._spool or Path(spool_path()), self._records or Path(records_path()))

    def drain(self, mode=None) -> dict:
        """Upload what is spooled. Never raises; returns counts, including on failure.

        An unattended caller cannot act on an exception from inside a farming loop, and a
        crash there would lose the loop rather than the upload — the records are already on
        disk and the next drain takes them. The mode comes off each record, so draining does
        not relabel rows written by anything else.
        """
        from .uploader import upload_spool

        try:
            from wddrop_schema.models import CaptureMode

            fallback = mode or CaptureMode(self.capture_mode)
        except Exception:                                 # pragma: no cover - import guard
            fallback = mode
        try:
            return upload_spool(self.config, fallback, spool=self.paths()[0])
        except Exception as exc:                          # noqa: BLE001 - see docstring
            return {"uploaded": 0, "remaining": None, "rejected": 0, "held": 0,
                    "error": f"{type(exc).__name__}: {exc}"}

    def open_dive(self, *, dungeon_id: int | None = None, floor_id: int | None = None,
                  label_source: str = "screen_read",
                  started_at: datetime | None = None) -> DiveHandle:
        """`label_source` says where the floor came from, and it is not decoration.

        A floor the caller READ off the screen carries recognition error; one a human picked
        from a dropdown carries attention error and may be a week stale. The study can measure
        either rate only while they stay apart, so the default here is `screen_read` and a
        caller that is really passing a human's claim should say so.
        """
        return DiveHandle(self, dungeon_id=dungeon_id, floor_id=floor_id,
                          label_source=label_source,
                          started_at=started_at or datetime.now(timezone.utc))

    def build_event(self, lines: "list[DropLine]", *, dive_id: str, started_at: datetime,
                    occurred_at: datetime, dungeon_id, floor_id, label_source: str,
                    chest_index: int, qc: dict | None = None) -> dict:
        from .config import CLIENT_VERSION

        contents = []
        for line in lines:
            contents.append(self._item(line))
        return self._event(contents, dive_id=dive_id, started_at=started_at,
                           occurred_at=occurred_at, dungeon_id=dungeon_id, floor_id=floor_id,
                           label_source=label_source, chest_index=chest_index, qc=qc,
                           truncated=any(l.truncated for l in lines))

    @staticmethod
    def _item(line: DropLine) -> dict:
        item = {"item_name": line.name, "quantity": line.quantity or 1,
                "match_confidence": round(line.score, 4)}
        if line.quantity is None:
            # 1 AND FLAGGED, which is what the player client does — and matching it is the
            # point. The wire format requires a quantity of at least 1, so something has to
            # be written; the flag is what keeps "recorded 1 because I read a 1" apart from
            # "recorded 1 because no number was on screen". If this path flagged differently
            # from the other, `qty_unknown` would mean one thing for a player's rows and
            # another for a bot's, and the study pools them.
            item["qty_unknown"] = True
        return item

    def _event(self, contents: list, *, dive_id, started_at, occurred_at, dungeon_id,
               floor_id, label_source, chest_index, qc, truncated: bool) -> dict:
        """THE SPOOL'S SHAPE, which is not the wire's.

        A spooled record is pre-hydration: the uploader attaches identity and context when it
        drains, so `client_version` and `qc` sit at the TOP level and there is no `capture`
        block. Writing the wire shape here instead looks right and loses silently — `hydrate`
        reads `qc` from the top level, so a nested one arrives empty, and it rebuilds
        `capture` wholesale, so a mode written inside would be replaced by the uploader's.
        That is this project's oldest failure wearing a new hat: a field the client sets that
        nothing downstream reads.
        """
        from .config import CLIENT_VERSION

        return {
            "schema_version": 3,
            "client_version": CLIENT_VERSION,
            "event_id": str(uuid4()),
            "occurred_at": occurred_at.isoformat(),
            "provenance": "chest_direct",
            "truncated": truncated,
            # Carried on the RECORD, so a drain cannot relabel it. See `uploader.hydrate`:
            # without this the mode comes from whoever happens to run the uploader, and a
            # player draining their own spool would turn these rows into theirs.
            "capture_mode": self.capture_mode,
            "qc": dict(qc or {}),
            "contents": contents,
            "dive": {
                "dive_id": dive_id,
                "started_at": started_at.isoformat(),
                "elapsed_seconds": max(0, int((occurred_at - started_at).total_seconds())),
                "dungeon_id": dungeon_id,
                "floor_id": floor_id,
                "chest_index_in_dive": chest_index,
                "label_source": label_source,
            },
        }

    def write(self, event: dict) -> None:
        """Both files, one line each — see `runner.CaptureSession._spool` for the rule."""
        line = json.dumps(event, ensure_ascii=False) + "\n"
        for path in self.paths():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)


_ZERO = __import__("datetime").timedelta(0)

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

__all__ = ["Geometry", "Refusal", "DropLine", "Refused", "LineReader",
           "read_drop_line", "Recorder", "DiveHandle"]


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
    font_path: str = ""

    @classmethod
    def for_window(cls, frame_size, *, locale: str = "ja", font_path: str | None = None,
                   safe_area=None) -> "Geometry":
        """Computed from the window size alone — no calibration step for the caller."""
        from .calibration import Profile, read_columns

        p = Profile.derived(frame_size, font_path=font_path or "", locale=locale,
                            safe_area=safe_area)
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

    def __init__(self, geometry: Geometry, names: list[str], atlas_path, *,
                 after: str = "を手に入れた!!", quantity_mark: str = "×"):
        from .capture import glyph as g

        self._g = g
        self.geometry = geometry
        self.after = after
        self.quantity_mark = quantity_mark
        window = g.required_window(
            g.make_renderer(atlas_path, geometry.size, (2400, 90), geometry.letter_spacing),
            "", names)
        self._window = window
        self._renderer = g.make_renderer(atlas_path, geometry.size, window,
                                         geometry.letter_spacing)
        self._index = g.RenderRecognizer(self._renderer, "", names)

    def read(self, frame_gray) -> DropLine | Refused:
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


def read_drop_line(frame_gray, reader: LineReader) -> DropLine | Refused:
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

    def record(self, line: DropLine, *, occurred_at: datetime | None = None,
               qc: dict | None = None) -> str:
        """Write one drop. Returns its event_id, which is also the erasure handle for it."""
        if self._closed:
            raise RuntimeError("this dive is closed; open another before recording into it")
        now = occurred_at or datetime.now(timezone.utc)
        self._chests += 1
        event = self._recorder.build_event(
            line, dive_id=self.dive_id, started_at=self.started_at, occurred_at=now,
            dungeon_id=self.dungeon_id, floor_id=self.floor_id,
            label_source=self.label_source, chest_index=self._chests, qc=qc)
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

    def __init__(self, config=None, *, capture_mode: str = "autopilot", locale: str = "ja"):
        from .config import ClientConfig

        self.config = config or ClientConfig.load()
        self.capture_mode = capture_mode
        self.locale = locale

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

    def build_event(self, line: DropLine, *, dive_id: str, started_at: datetime,
                    occurred_at: datetime, dungeon_id, floor_id, label_source: str,
                    chest_index: int, qc: dict | None = None) -> dict:
        from .config import CLIENT_VERSION

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
        return {
            "schema_version": 3,
            "event_id": str(uuid4()),
            "install_id": str(self.config.install_id),
            "occurred_at": occurred_at.isoformat(),
            "tz_offset_minutes": int(
                (occurred_at.astimezone().utcoffset() or _ZERO).total_seconds() // 60),
            "provenance": "chest_direct",
            "truncated": bool(line.truncated),
            "capture": {"mode": self.capture_mode, "client_version": CLIENT_VERSION,
                        "locale": self.locale, "qc": dict(qc or {})},
            "contents": [item],
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

    @staticmethod
    def write(event: dict) -> None:
        """Both files, one line each — see `runner.CaptureSession._spool` for the rule."""
        from .config import records_path, spool_path

        line = json.dumps(event, ensure_ascii=False) + "\n"
        for path in (spool_path(), records_path()):
            with Path(path).open("a", encoding="utf-8") as fh:
                fh.write(line)


_ZERO = __import__("datetime").timedelta(0)

"""
Client configuration and local state.

Lives next to the exe on Windows (%LOCALAPPDATA%\\wddrop) so an antivirus-friendly single
file can still keep durable state. Holds:

  * install_id  — a random UUID4, the ONLY identifier ever transmitted. Not derived from
    hardware, account, or anything else, so it cannot be correlated back to a person.
  * consent record
  * the spool file of events not yet uploaded
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from .consent import ConsentState

log = logging.getLogger("wddrop.config")

APP_NAME = "wddrop"
# Stamped onto every event as the build that READ it, and asked about by the ingest server as
# the build that is SENDING. The release tag must say the same thing — CI refuses a tag that
# disagrees, because a client that under-reports itself would be refused after the fix that
# made it acceptable, and one that over-reports would be admitted before it.
CLIENT_VERSION = "0.7.1"


def config_dir() -> Path:
    """Everything this client keeps, in ONE folder, away from the program.

    `%LOCALAPPDATA%\\wddrop` on Windows. Three reasons it is there rather than beside the
    exe or in Documents:

      * beside the program, unpacking a new version over the old one puts a player's
        install_id, their calibration and their unsent records in the blast radius of a
        drag-and-drop — and the install_id is the only handle they have for erasure;
      * Documents is synced by OneDrive on most machines, and this folder grows kept frames
        by the gigabyte. Nobody asked for their capture recordings to be uploaded anywhere;
      * Local rather than Roaming for the same reason — captures must not follow a domain
        profile around.

    One folder also makes the promise simple to keep: deleting it deletes everything held
    on this computer, and there is nothing anywhere else to find.
    """
    base = os.environ.get("WDDROP_HOME")
    if base:
        root = Path(base)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def hide(path: str | Path) -> bool:
    """Mark a file hidden, the way the platform actually does it.

    NOT by renaming it. A leading dot hides nothing on Windows — that is a Unix convention,
    and Explorer shows `.atlas.ja.json` exactly as it shows any other name. Windows has a
    file ATTRIBUTE for this, so that is what gets set.

    Hidden, not protected: every reader still opens these normally, and a player who has
    turned on "show hidden files" still sees them. The point is only that the rendered
    typeface is not the first thing in the folder someone opens to find their records.

    Returns whether the mark was applied, and never raises: failing to hide a file is not a
    reason to fail the thing that produced it.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        ok = ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
        return bool(ok)
    except Exception:                                  # noqa: BLE001
        log.debug("wddrop: could not hide %s", path, exc_info=True)
        return False


# Marks a build that still carries the parts that are not finished. Bundled by default and
# left out by `build_exe.py --production`, so what a player receives is decided at BUILD time
# rather than by anything they can set.
DEV_MARKER = "DEVELOPMENT"


def in_development() -> bool:
    """Whether the unfinished parts of the window should be shown.

    True in a checkout, because that is what a checkout is for. In a build it is true only
    when the marker was bundled — a production exe carries no way to turn it back on, which
    is the point: calibration is offered here for us to test with, and a fit made on a
    player's machine is a claim nobody has checked against a recording.
    """
    if not getattr(sys, "frozen", False):
        return True
    for root in (bundled_dir(), program_dir()):
        if root and (root / DEV_MARKER).exists():
            return True
    return False


def in_checkout() -> bool:
    """Whether this is the source tree rather than something a player was given.

    NOT the same question as `in_development`, and the difference is the whole point. The
    released exe carries the DEVELOPMENT marker deliberately — only one resolution ships a
    fit, so taking calibration away from players would refuse every other screen size — and
    that made every "dev only" thing visible in the build people download. A frame counter on
    the record page is one of those: it is an instrument reading, not a feature, and a player
    reported it as such.

    So: `in_development` gates what a PLAYER may need on their own machine (calibration).
    This gates what only we ever read.
    """
    return not getattr(sys, "frozen", False)


def unhide(path: str | Path) -> None:
    """Clear the hidden mark, so the file can be written over.

    Windows refuses to open an existing HIDDEN file for writing through a normal create call
    — it returns "permission denied", not a hint about attributes. So a file this program
    hides is a file this program can no longer rebuild, which is how marking the atlas turned
    every rebuild after the first into `[Errno 13] ... atlas.ja.png` on the one screen a new
    player is looking at.

    Never raises: a file that is not there, or not markable, is not a reason to fail the
    write that is about to happen anyway.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_NORMAL = 0x80
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_NORMAL)
    except Exception:                                  # noqa: BLE001
        log.debug("wddrop: could not clear attributes on %s", path, exc_info=True)


def program_dir() -> Path:
    """The folder the client was unpacked into — client/, packages/, and the data files.

    Frozen into a one-file exe there is no such folder: the modules live in a temporary
    directory that is deleted on exit. The EXE's own folder is the right answer there, because
    that is where a player can put a file — an atlas built for a locale that did not ship, a
    newer vocabulary — and expect it to be found.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_dir() -> Path | None:
    """Where a one-file build unpacked the data it carries, if this is one.

    Searched AFTER the exe's own folder, so a file a player drops beside the exe wins over
    the copy inside it. That is the only way to fix a stale vocabulary without a new build.
    """
    root = getattr(sys, "_MEIPASS", None)
    return Path(root) if root else None


def data_dir() -> Path:
    """Where vocabularies, atlases and catalogues live: with the PROGRAM, not the player.

    They are rebuilt from the game's own files by `tools/`. None of them is the player's, so
    none of them belongs in a folder whose whole promise is "delete this and it is all gone".
    """
    here = program_dir()
    return here / "data" if (here / "data").is_dir() else here


# What belongs to the PLAYER rather than to the program. Vocabularies, atlases and
# catalogues are not here: they ship with the client and are rebuilt, not kept.
STATE_FILES = (
    "config.json",        # the install_id above all — losing it loses the erasure handle
    "profile.json", "profiles.json",
    "spool.jsonl", "records.jsonl", "closes.jsonl",
    "review.json", "verified.json",
    "hud_template.png", "walk.png", "drop.png",
)


def migrate_state(old: Path) -> list[str]:
    """Move state left behind by a version that kept it beside the program.

    Only ever into an empty seat: a file that already exists in the new folder is the newer
    truth and is left alone. Copy-then-unlink rather than replace, because the two can be on
    different drives.

    The install_id is the reason this exists at all. Without it a returning player becomes a
    new player — their history is stranded under a pseudonym nobody can reach, and the id
    they were told to quote for erasure no longer refers to anything.
    """
    old = Path(old)
    root = config_dir()
    if old.resolve() == root.resolve():
        return []
    moved = []
    for name in STATE_FILES:
        source, target = old / name, root / name
        if not source.is_file() or target.exists():
            continue
        try:
            target.write_bytes(source.read_bytes())
            source.unlink()
            moved.append(name)
        except OSError:
            # A folder we cannot write to is not a reason to refuse to start.
            continue
    repair_profile_paths()
    return moved


# Paths a profile records that point at a file which may have moved out from under it.
_PROFILE_PATH_KEYS = ("hud_template_path",)


def repair_profile_paths() -> list[str]:
    """Re-point, or drop, absolute paths inside a profile that no longer lead anywhere.

    A profile stores where its HUD template was when it was fitted. Moving state out of the
    program folder moved the template and left the path behind pointing at nothing — a file
    that describes itself wrongly, which is a trap for whoever reads it next even when
    nothing breaks today. (Nothing does: the template is also embedded in the profile, and
    the embedded copy is preferred precisely because "a path can be moved, cleaned up or
    lost while the profile still names it".)

    Idempotent, and safe to call on every launch: a path that resolves is left alone.
    """
    root = config_dir()
    touched = []
    for name in ("profile.json", "profiles.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        entries = [data] if "frame_size" in data else [
            v for v in (data.get("profiles") or data).values() if isinstance(v, dict)]
        changed = False
        for entry in entries:
            for key in _PROFILE_PATH_KEYS:
                stored = entry.get(key)
                if not stored or Path(stored).exists():
                    continue
                moved_here = root / PurePosixPath(stored.replace("\\", "/")).name
                entry[key] = str(moved_here) if moved_here.exists() else None
                changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            touched.append(name)
    return touched


def spool_path() -> Path:
    """Append-only JSONL of events awaiting upload — the OUTBOX, drained and emptied.

    Events are spooled to disk before any network attempt so a dropped connection, a
    crash, or a closed laptop never costs the player their records — the uploader drains
    this file on the next run.

    It is not the player's copy of what they recorded; see `records_path`.
    """
    return config_dir() / "spool.jsonl"


def records_path() -> Path:
    """Append-only JSONL of everything recorded, ever — the PLAYER'S OWN COPY.

    Separate from the spool because the spool is an outbox: the uploader empties it as it
    sends. With per-record sending on (the default) it is empty within a second of every
    chest, so exporting from it handed the player a CSV with a header and no rows — their
    data, gone from their own machine, in the mode most of them will never change.

    Appended to at record time, so it costs one write and cannot be affected by anything the
    network does. Nothing deletes from it; erasure (DISCLAIMER §6) is about the server's copy.
    """
    return config_dir() / "records.jsonl"


def closes_path() -> Path:
    """Append-only JSONL of dive endings still to be sent. See `uploader.record_close`."""
    return config_dir() / "closes.jsonl"


# How a recorded event reaches the server, once sharing is on.
SEND_EACH = "each"        # as it happens — the spool is drained after every record
SEND_BATCH = "batch"      # once a handful have accumulated
SEND_MANUAL = "manual"    # when the player presses Upload

# How many finds a batch waits for. Ten, from the measured cadence: a median 20s between
# records puts a batch at roughly three to five minutes, so a session's data still arrives
# while it is being played, at a tenth of the requests. One recorded session was 27 events —
# 27 requests as-it-happens, 3 like this.
#
# Waiting costs nothing that matters. The spool is written before any network attempt either
# way, so a crash loses no more in this mode than in the other; only the SEND is deferred.
# It also gives `record_stop_reason` something to stamp at session end, which per-record
# sending leaves it nothing of.
SEND_BATCH_SIZE = 10

AUTOMATIC_MODES = (SEND_EACH, SEND_BATCH)


@dataclass
class ClientConfig:
    install_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # The study's ingest host. Not editable in the window on purpose — where a player's
    # records go is not a preference, and "change your server address to…" is exactly the
    # instruction someone else would give. An existing config.json keeps whatever it has,
    # so a dev machine pointed at localhost stays pointed there.
    server_url: str = "https://wizardry-daphne-api.kuaz.dev"
    # The language the GAME is in. FIXED at Japanese, and no longer a choice: the face the
    # recogniser needs is readable in the files the game already installed, but only for this
    # language. In every other one, that face is unreachable and the substitute cannot draw
    # 28 of the characters Chinese item names use — 375 of 3,478 names, which do not fail
    # loudly. They become gaps, and a gap is indistinguishable from "nothing dropped".
    #
    # Kept as a field rather than a constant because it names which vocabulary and atlas to
    # load, and every one of those files is per-language. It is NOT the language of the
    # window: see `ui_locale`, which is still the player's to set.
    locale: str = "ja"
    # The language of the WINDOW. None means "follow the operating system", which is the
    # default; a real value means the player chose one and it stops following.
    ui_locale: str | None = None
    # SHARING IS SEPARATE FROM RECORDING, and off until the player says otherwise.
    # Everything is recorded and kept locally either way; this only decides whether it is
    # also sent. Asked once at first run so it is an answer rather than an unnoticed default.
    share_uploads: bool = False
    asked_sharing: bool = False
    # Batched by default. Per-record is still there for anyone who wants to watch records
    # land, but ten to one on requests is the better shape for both ends and costs nothing
    # a player would notice.
    send_mode: str = SEND_BATCH
    send_batch_size: int = SEND_BATCH_SIZE
    # How many pickaxes the player is carrying. Never uploaded — it is theirs, not data.
    pickaxes: int = 0
    # The dungeon chosen last time. Restored on open because a player runs the same one for
    # a session at a time, and re-picking it every launch is a step they will eventually
    # forget — and a dive filed under the wrong dungeon is worse than one not recorded.
    # Still deliberately NOT defaulted to anything: "unset" and "I chose the first entry"
    # must stay distinguishable, so this is None until a real choice is made.
    dungeon_id: int | None = None
    # Whether to keep the captured frames, and whether to keep the walking ones too. Settings,
    # like everything else on that page — they were the only two controls there that lived
    # only in the widget, so every launch silently reset them to off and a player who had
    # turned recording on lost the frames for the very session they turned it on to explain.
    # The failure is invisible at the moment it happens: nothing looks different until the
    # frames are wanted and are not there.
    keep_frames: bool = False
    keep_all_frames: bool = False
    # Ask GitHub, once a launch, whether there is a newer client. ON by default and stated
    # in the disclaimer: a player running a build known to mis-read the screen is the one
    # person who cannot find that out on their own, and the server's floor only reaches
    # them if they upload — which is off until they say otherwise. See updates.py for what
    # the request carries, which is nothing of theirs.
    check_updates: bool = True
    # Detailed logging, off by default. A setting rather than a rebuild because the report
    # that needs it — "it did not record my chest" — arrives from someone who cannot
    # reproduce it on demand, and asking them to install a debug build is asking them to
    # stop helping. See logs.py for what each level holds.
    trace: bool = False
    # HOW FAR THROUGH THE STORY THIS PLAYER IS, as they reported it.
    #
    # Some dungeons scale with a value the game keeps on its own side and never shows anyone:
    # it decides how strong the enemies are, which groups appear at all, and what some quests
    # pay out. Two players standing on the same floor can be in measurably different games —
    # in one dungeon the same quest pays 2,500 or 8,500 gold depending on it. Whether it also
    # changes what is IN a chest is exactly the open question this study can answer, and only
    # if the covariate is recorded.
    #
    # It cannot be read: no screen shows it, no file the client can reach holds it, and the
    # amount each story ending adds is decided on the game's side. What CAN be asked is what
    # the player has seen, so this is a bitfield of those endings — self-reported, never a
    # level, and stored as `1`/`0` characters oldest-first.
    #
    # APPEND ONLY. New endings get new bits on the end; an existing bit never changes meaning
    # or position, because a row written last month cannot be re-asked. `progress_width` is
    # how many bits existed when the answer was given, so a bit that did not exist yet reads
    # as unknown rather than as "no".
    progress_bits: str = ""
    progress_width: int = 0
    # When the question was last PUT — answered or dismissed alike. Dismissing has to cost
    # the same as answering, or a prompt that reappears next session teaches people to click
    # it away without reading, and a reflexive answer is worse than none.
    progress_asked_at: str | None = None
    # How long to leave it before asking again, in days. Zero means never ask; the question
    # stays available in Settings either way. Progress moves slowly — reaching an ending is
    # not something that happens between two sessions — so this is a ceiling on nagging
    # rather than a schedule.
    progress_interval_days: int = 14
    # THE MAIN CHARACTER'S GRADE, a separate axis from the story above and stored as the
    # game's own grade id. It caps the party's level — 40 at bronze, 70 at copper — so two
    # players at the same story point but different grades are farming with different
    # parties. None means nobody has said; it is NOT grade 1, which is a real rung.
    character_grade: int | None = None
    consent: ConsentState = field(default_factory=ConsentState)

    @classmethod
    def load(cls) -> "ClientConfig":
        path = config_dir() / "config.json"
        if not path.exists():
            cfg = cls()
            cfg.save()
            return cfg
        raw = json.loads(path.read_text(encoding="utf-8"))
        # The GAME language is no longer stored: it is Japanese. A value left by an older
        # build would otherwise keep loading a vocabulary this client has no face to draw —
        # silently, since a name that cannot be rendered simply never matches. `ui_locale`,
        # the window's own language, is untouched.
        raw.pop("locale", None)
        raw["consent"] = ConsentState(**{
            k: v for k, v in raw.get("consent", {}).items()
            if k in ConsentState.__dataclass_fields__
        })
        # Keys this build does not know are DROPPED, not fatal. Raising here loses every
        # setting at once — the window will not open, and the only obvious way out is
        # deleting the file that also holds the install_id, which is the one thing that
        # cannot be recovered. A config written by a newer build, or by one that has since
        # renamed a field, must still open.
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            log.warning("wddrop: ignoring unknown config setting(s): %s", ", ".join(sorted(unknown)))
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self) -> None:
        path = config_dir() / "config.json"
        data = asdict(self)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

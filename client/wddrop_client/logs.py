"""Where the client writes down what it did.

WHY A FILE AT ALL
-----------------
Everything interesting this client does happens while the player is looking at the GAME, not
at the window — and the one report that matters ("it did not record my chest") arrives hours
later, from someone who cannot reproduce it on demand. Console output does not survive that:
the window has no console, and the exe is built without one.

The frames already recorded are the other half of the evidence, and they are the expensive
half. A log costs nothing and says WHY a frame was passed over, which is the question a
recording alone cannot answer — a frame that was skipped and a frame that was never sampled
look identical on disk.

TRACE IS OFF BY DEFAULT, AND IS A SETTING RATHER THAN A REBUILD
---------------------------------------------------------------
At INFO the file holds what a player would recognise: sessions starting and stopping, panels
fitted, chests recorded, uploads. At DEBUG it holds the recogniser's own reasoning — refused
readings, quantity attempts, why a line was not read — which is what a diagnosis needs and
is far too much to write all the time.

So it is a switch the player can turn on when asked to, without a new build and without the
command line: `wddrop --trace`, or the checkbox in Settings.

WHAT IT MUST NEVER CONTAIN
--------------------------
The install_id. It is the erasure handle and the one identifier the service promises never
to store, and a log is exactly the kind of file that gets pasted into a chat window when
someone asks for help. `tests/test_logging.py` holds that to it.

TWO THINGS THAT LOOK LIKE DETAIL AND ARE NOT
--------------------------------------------
* **utf-8, explicitly.** Every item name in this study is CJK, and a log file opened at the
  Windows default (cp932 on a Japanese machine, cp950 on a Chinese one) raises inside the
  handler on the first name it cannot encode. Logging swallows that and drops the record, so
  the lines that vanish are precisely the ones naming what was recognised.
* **Rotating, and capped.** Trace on a 20fps capture loop writes steadily, and a player who
  turns it on and forgets is not doing anything wrong. Three files of 2MB is enough to hold
  a long session and small enough to attach to a message.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import config_dir

# Kept beside the rest of the player's own files, so "delete that folder and it is all gone"
# stays true of the logs as well.
LOG_DIR_NAME = "logs"
LOG_NAME = "wddrop.log"
MAX_BYTES = 2_000_000
BACKUPS = 3

# The console keeps the shape it has always had — a level and a message, nothing else. The
# FILE carries time and logger name, because a file is read long after the fact and by
# someone who was not there.
CONSOLE_FORMAT = "%(levelname)s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"

_configured = False


def log_dir() -> Path:
    return config_dir() / LOG_DIR_NAME


def log_path() -> Path:
    return log_dir() / LOG_NAME


def configure(trace: bool = False, console: bool = True) -> Path | None:
    """Set up console and file logging. Returns the log file, or None if it cannot be written.

    Safe to call again — the window calls it when the setting changes — and the second call
    replaces the handlers rather than adding a second copy of each.

    A log file that cannot be opened is NOT an error worth stopping for: a read-only folder,
    a full disk or an antivirus holding the file are all reasons to carry on capturing
    without one. Capture is the thing that cannot be redone.
    """
    global _configured

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_wddrop", False) or not _configured:
            root.removeHandler(handler)
            if getattr(handler, "_wddrop", False):
                handler.close()
    root.setLevel(logging.DEBUG if trace else logging.INFO)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(logging.Formatter(CONSOLE_FORMAT))
        stream._wddrop = True
        root.addHandler(stream)

    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError:
        _configured = True
        logging.getLogger("wddrop.logs").warning(
            "wddrop: no log file could be opened at %s; carrying on without one", path)
        return None
    handler.setLevel(logging.DEBUG if trace else logging.INFO)
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    handler._wddrop = True
    root.addHandler(handler)
    _configured = True

    # Third-party libraries are left at their own level even in trace mode. urllib3 and PIL
    # at DEBUG bury the client's own lines in traffic and image internals, and the question
    # being asked is always about this code.
    for noisy in ("httpx", "httpcore", "urllib3", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.INFO)

    logging.getLogger("wddrop.logs").info(
        "wddrop: logging to %s (%s)", path, "trace" if trace else "normal")
    return path


def tail(lines: int = 200) -> str:
    """The last few lines, for showing someone what just happened without opening a file."""
    path = log_path()
    if not path.exists():
        return ""
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-lines:])
    except OSError:
        return ""

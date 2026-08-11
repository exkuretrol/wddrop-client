"""Where this machine keeps the things the tests need.

ONE PLACE, AND NOT IN THE REPOSITORY. Absolute paths in a test name a particular computer,
and some of these name where the project keeps the game's own extracted data — which is
exactly the kind of thing that should not travel with a client anyone can download. Keeping
them here means the client can be published without a sweep for stray paths every time, and
means a machine that has none of it skips those tests instead of failing them.

Set them in the environment, or in `tests/local.env` (untracked), one per line:

    WDDROP_TEST_FONTS=/somewhere/extracted
    WDDROP_TEST_ITEMS=/somewhere/item.json
    WDDROP_TEST_CAPTURES=/somewhere/capture
    WDDROP_TEST_PROFILES=/somewhere/profiles.json
"""
from __future__ import annotations

import os
from pathlib import Path

LOCAL = Path(__file__).resolve().parent / "local.env"


def _load_local() -> None:
    """`local.env` fills in anything the environment did not.

    The environment wins, so CI can set one value without the file having to agree.
    """
    if not LOCAL.exists():
        return
    for line in LOCAL.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and not key.startswith("#"):
            os.environ.setdefault(key, value)


_load_local()


def _path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    found = Path(value)
    return found if found.exists() else None


# The game's own fonts, extracted. Tests that render with them skip without.
FONTS = _path("WDDROP_TEST_FONTS")
# The game's item table, for the vocabulary-scale tests.
ITEMS = _path("WDDROP_TEST_ITEMS")
# A folder of recorded sessions, and one session in particular where a test names one.
CAPTURES = _path("WDDROP_TEST_CAPTURES")
# The player's calibration store.
PROFILES = _path("WDDROP_TEST_PROFILES")


def capture(name: str) -> Path | None:
    """One recorded session by name, if the captures folder is configured and has it."""
    if CAPTURES is None:
        return None
    found = CAPTURES / name
    return found if found.exists() else None

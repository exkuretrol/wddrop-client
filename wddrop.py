#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pillow>=10",
#     "numpy>=1.26",
#     "mss>=9.0",
#     "pydantic>=2.7",
#     "httpx>=0.27",
#     # The wire format, shared with the server and pinned to a tag so both sides
#     # speak a version somebody chose. It used to sit in packages/schema beside this
#     # file; it is its own repository now.
#     "wddrop-schema @ https://github.com/exkuretrol/wddrop-schema/archive/refs/tags/v0.1.0.tar.gz",
#     # Reading the game's own font out of the installation. Since 0.5.0 the atlas is not
#     # shipped and is built on the player's machine instead, so without this the client
#     # starts, says "UnityPy is not installed", and never gets an atlas — which reads as
#     # recognition being broken rather than as a missing dependency.
#     "UnityPy",
#     # Reading the game WINDOW rather than the screen it is drawn on. Optional in that
#     # capture falls back to `mss`, but the fallback records whatever is in FRONT of the
#     # game, which is not the same recording.
#     "windows-capture>=2.0; sys_platform == 'win32'",
# ]
# ///
#
# THIS LIST IS ONE OF THREE, and `tests/test_launcher.py` holds them together: the exe's is
# `build_exe.RUNTIME_IMPORTS`, the project's is `client/pyproject.toml`, and this one is for
# running the script straight out of a folder. PySide6 belongs to none of them — an 80MB GUI
# toolkit must never become a requirement of reading the screen, so the window is asked for
# explicitly with `--with PySide6-Essentials`.
"""
Launcher — run this instead of `python -m wddrop_client`.

    uv run wddrop.py calibrate --drop-shot drop.png --name "初始的雜物" ...
    uv run wddrop.py replay frames\\ --dungeon 2000
    uv run wddrop.py run --dungeon 2000

For the window instead of the command line, add the GUI toolkit for that run only:

    uv run --with PySide6-Essentials wddrop.py ui

PySide6 is deliberately NOT in the dependency list below: it is ~80 MB, and the capture
path must not become unusable on a machine where a GUI toolkit will not install.

Why this file exists: the client is a package sitting beside this file (`client/`) rather
than something installed, so `python -m wddrop_client` only works if PYTHONPATH is set
first — and forgetting that produces a bare "No module named wddrop_client" that says
nothing about the cause. This puts it on sys.path itself, so there is no environment to
get wrong. `wddrop_schema` is NOT on that list: it is a pinned dependency above, because
the server needs the same one and a folder cannot be shared between two repositories.

The PEP 723 header above means `uv run wddrop.py` also installs the dependencies on first
run. With plain `python wddrop.py` you install them yourself:

    py -m pip install pillow numpy mss pydantic httpx
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

for pkg in (HERE / "client",):
    if not pkg.is_dir():
        sys.exit(
            f"[!] missing {pkg}\n"
            f"    Run this from the wddrop folder, next to client/."
        )
    sys.path.insert(0, str(pkg))

# State used to land beside this script. It now lives in one per-user folder — see
# config.config_dir for why — so anything left over is moved across once, before anything
# reads it. WDDROP_HOME still wins, which is what the tests use.
try:
    from wddrop_client.config import migrate_state

    _moved = migrate_state(HERE)
    if _moved:
        print(f"[=] moved {len(_moved)} file(s) to your data folder: {', '.join(_moved)}")
except Exception:                        # a failed move must never stop the client opening
    pass

if __name__ == "__main__":
    try:
        from wddrop_client.__main__ import main
    except ImportError as exc:  # a dependency, not a path problem
        sys.exit(
            f"[!] {exc}\n"
            f"    Install the dependencies:  py -m pip install pillow numpy mss pydantic httpx\n"
            f"    or run through uv, which does it for you:  uv run wddrop.py --help"
        )
    raise SystemExit(main())

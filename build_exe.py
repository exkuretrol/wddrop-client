"""Build the one-file Windows client.

    uv run --with pyinstaller --with PySide6-Essentials build_exe.py

WHY A SCRIPT AND NOT A .spec
----------------------------
The data files are the whole problem. The client is useless without its vocabulary and
atlas, they live beside the launcher rather than inside the package, and PyInstaller silently
produces a working-looking exe when they are missing — it fails later, on the player's
machine, as "no calibration" or "nothing recognised". So the list is checked here, before the
build, and a missing file stops it.

WHAT ENDS UP INSIDE
-------------------
    wddrop_client, wddrop_schema     the two packages, which are path imports rather than
                                     installed distributions
    vocab.ja.json                    the item names the reader matches against
    catalog.ja.json                  the dungeon and floor names the picker shows
    atlas.ja.json + .png             the rendered glyphs — SEE THE LICENCE NOTE
    profiles.shipped.json            the calibrations that were verified against recordings

NOT boosts.json. The client has never read it — it is the server's, and it was bundled here
by mistake, which is the kind of thing an allow-list of files quietly preserves forever.

THE ATLAS IS A RASTERISATION OF THE GAME'S OWN FONT
---------------------------------------------------
`AR SYSongTextH32B5Pro EB`, © Arphic Technology, fsType 0x0004. Putting it inside an exe and
handing that to other people redistributes a derivative of a commercial typeface. Building it
for yourself is the same as the files already on your disk; PUBLISHING it is the step that
raises the question. `--no-atlas` leaves it out — the exe then needs an atlas beside it, and
cannot read anything without one. See docs/QUANTITY-RECOGNITION.md for why a free font is not
a drop-in replacement yet.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# What client/pyproject.toml declares. Kept in step by test_build.py rather than parsed here,
# so this script has no dependency on a toml reader.
PYTHON_FLOOR = (3, 11)
# Checked differently from the rest: the client reaches this one through a shim (see
# capture/wgc.py) that satisfies the OpenCV import the package makes at module scope and this
# build excludes. A plain `import` of it inside the exe fails where the client succeeds.
WINDOW_CAPTURE = "windows_capture.windows_capture"

# Everything the client looks for beside itself at runtime, and what it costs to omit each.
DATA = [
    ("DISCLAIMER.md", "the terms the player agrees to — the FIRST thing the window shows", True),
    ("profiles.shipped.json", "the verified calibrations — without it every player calibrates", True),
    ("vocab.ja.json", "the item names — without it nothing can be recognised at all", True),
    # OPTIONAL, and listed so the build says so: the dungeon list is built into the client
    # (wddrop_client/dungeons.py) and a file only overrides it.
    ("catalog.ja.json", "a fuller dungeon list than the one built in", False),
    # Optional: without it the page shows the names as they were read, which is never
    # wrong, only in the language the game is in.
    ("names.ja.json", "item names in the window's language", False),
    ("names.zh_tw.json", "item names in the window's language", False),
    ("names.zh_cn.json", "item names in the window's language", False),
    ("names.en.json", "item names in the window's language", False),
    ("names.ko.json", "item names in the window's language", False),
    ("names.de.json", "item names in the window's language", False),
    # NOT shipped. The atlas is the game's own typeface rendered, and the client builds it on
    # the player's machine from the copy they already have — which is the whole reason no
    # typeface is distributed. `--with-atlas` bundles one anyway for a build that must work
    # without the game installed.
    ("atlas.ja.json", "a prebuilt glyph atlas", False),
    ("atlas.ja.png", "a prebuilt glyph atlas", False),
]
ATLAS = {"atlas.ja.json", "atlas.ja.png"}

# Everything the client imports at run time. PyInstaller can only bundle what is installed
# in the environment the BUILD runs in, and a missing one is not a build error — the exe is
# produced, and fails on the player's machine at the moment that import is reached, which is
# never at startup. Measured: `--with requests` instead of `--with httpx` produced an exe
# that opened, recorded, and then could not upload; `mss` is worse, since without it there
# is nothing to read the screen with at all.
#
# Named as (import name, install name, what is lost). The install names build the command
# this prints, so a person who hits this can copy the line rather than work it out.
RUNTIME_IMPORTS = [
    ("PySide6", "PySide6-Essentials", "the window itself"),
    ("PIL", "pillow>=10", "every part of recognition — nothing can be read"),
    ("numpy", "numpy", "every part of recognition — nothing can be read"),
    ("mss", "mss>=9.0", "screen capture: there is no way to see the game"),
    ("httpx", "httpx>=0.27", "uploading: records are kept but never sent"),
    ("pydantic", "pydantic>=2.7", "the record format — nothing can be spooled"),
    ("UnityPy", "UnityPy", "reading the game's own font: no atlas can be built"),
    (WINDOW_CAPTURE, "windows-capture",
     "reading the game WINDOW: capture falls back to the screen, and anything in front of "
     "the game is read instead of it"),
]


def entry_script(target: Path) -> Path:
    """A launcher with no sys.path games in it.

    wddrop.py puts client/ on the path at run time, which is right for a checkout and wrong
    for a bundle: PyInstaller has to see the imports statically or it packs nothing.
    wddrop_schema needs no --paths — it is installed in the build environment.
    """
    body = [
        '"""Entry point for the bundled client — see build_exe.py."""',
        "import multiprocessing",
        "import os",
        "import sys",
        "",
        "import wddrop_client.ui as ui",
        "",
        "",
        "def selftest() -> int:",
        '    """Does the FROZEN app find its own data?',
        "",
        "    The failure this build is most likely to have: it looks fine on the machine that",
        "    made it and arrives on a player's as 'nothing recognised'.",
        '    """',
        "    from wddrop_client.calibration import ProfileStore",
        "    from wddrop_client.config import ClientConfig",
        "    from wddrop_client.config import bundled_dir, program_dir",
        "",
        "    import functools",
        "",
        "    print = functools.partial(__builtins__['print'] if isinstance(__builtins__, dict)",
        "                              else __builtins__.print, flush=True)",
        "    print('frozen     :', getattr(sys, 'frozen', False))",
        "    print('exe folder :', program_dir())",
        "    print('bundled at :', bundled_dir())",
        "    ok = True",
        "    found = {}",
        "    for pattern, needed in (('vocab.{locale}.json', True),",
        "                            # Built on the player's machine from the game's own",
        "                            # font — absent here is the normal state.",
        "                            ('atlas.{locale}.json', False),",
        "                            # Optional: the dungeon list is built into the client",
        "                            # and a file only overrides it.",
        "                            ('catalog.{locale}.json', False)):",
        "        hit = ui.find_data(pattern, ClientConfig.load().locale)",
        "        found[pattern] = hit",
        "        ok = ok and (hit is not None or not needed)",
        "        print('  %-24s %s' % (pattern, hit or ('built on first run'",
        "                                                if not needed else None)))",
        "    shipped = ProfileStore.shipped()",
        "    print('  %-24s %s' % ('shipped profiles', shipped.keys()))",
        "    ok = ok and bool(shipped.keys())",
        "    # Every third-party module the client reaches for, imported INSIDE the bundle.",
        "    # The build checks its own environment; this checks what actually got packed,",
        "    # which is the only one of the two a player experiences. Each of these is",
        "    # imported inside a function somewhere, so none of them fails at startup —",
        "    # `httpx` first surfaced as a missing module at the moment of uploading.",
        f"    for module in {[m for m, _i, _w in RUNTIME_IMPORTS if m != WINDOW_CAPTURE]!r}:",
        "        try:",
        "            __import__(module)",
        "            print('  %-24s %s' % (module, 'in the bundle'))",
        "        except Exception as exc:",
        "            ok = False",
        "            print('  %-24s MISSING (%s)' % (module, exc))",
        "    # Asked as the question that matters rather than as an import: the client reaches",
        "    # this one through a shim that satisfies the OpenCV import the package makes and",
        "    # this build deliberately leaves out. Importing it directly here would report a",
        "    # failure the client does not have.",
        "    from wddrop_client.capture import wgc",
        "",
        "    try:",
        "        wgc._native()",
        "        why = ''",
        "    except Exception as exc:",
        "        why = ' (%s: %s)' % (type(exc).__name__, exc)",
        "    print('  %-24s %s%s' % ('window capture',",
        "                            'ready' if not why else 'NO, falls back to the screen', why))",
        "    ok = ok and not why",
        "    # And the GUI stack itself. A frozen Qt app that cannot find its platform",
        "    # plugin fails at the first window, not at import, so importing is not the test.",
        "    import os",
        "",
        "    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')",
        "    try:",
        "        from PySide6 import QtWidgets",
        "",
        "        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])",
        "        window = ui.MainWindow(__import__('wddrop_client.config', fromlist=['x'])",
        "                               .ClientConfig.load())",
        "        print('  %-24s %s' % ('window', window.windowTitle() or 'built'))",
        "        # The state a FRESH install lands in — the shipped calibration has to be",
        "        # enough to start, or the exe asks for a fit it already carries.",
        "        # A FIRST RUN STARTS BUILDING THE ATLAS, so wait for it before asking",
        "        # whether the client is ready — otherwise this reports the state of a",
        "        # machine mid-setup, which is not the question. Waited for explicitly:",
        "        # close() on a window that was never shown does not reliably deliver",
        "        # closeEvent, and that is where the window itself waits.",
        "        worker = getattr(window, '_atlas_worker', None)",
        "        if worker is not None:",
        "            print('  %-24s %s' % ('atlas', 'building from the game, waiting'))",
        "            worker.wait(120000)",
        "            window._refresh_setup()",
        "        print('  %-24s %s' % ('calibration', window.cal_label.text()))",
        "        print('  %-24s %s' % ('ready to record', window._ready))",
        "        # WHAT THE BUILD IS RESPONSIBLE FOR, NOT WHAT THE PLAYER'S MACHINE SUPPLIES.",
        "        #",
        "        # Requiring `_ready` was right while the atlas shipped inside the exe: a",
        "        # fresh install was then ready the moment it started, and anything less was",
        "        # a broken bundle. Since 0.5.0 the atlas is built from the player's own copy",
        "        # of the GAME, so on a machine without the game installed — every CI runner",
        "        # there will ever be — not being ready is the correct state, and demanding it",
        "        # made this check unpassable rather than strict.",
        "        #",
        "        # So it is only a failure when an atlas WAS available and the client still",
        "        # could not get ready: that is the bundle's fault and nobody else's.",
        "        atlas = found.get('atlas.{locale}.json') is not None",
        "        if not window._ready and not atlas:",
        "            print('  %-24s %s' % ('', 'no atlas yet, and no game on this machine to'",
        "                                  ' build one from — expected off a player\\'s PC'))",
        "        ok = ok and (bool(window._ready) or not atlas)",
        "        window.close()",
        "        app.quit()",
        "        app.processEvents()",
        "        app.processEvents()",
        "    except Exception as exc:",
        "        ok = False",
        "        print('  %-24s %s: %s' % ('window', type(exc).__name__, exc))",
        "    print('OK' if ok else 'MISSING DATA')",
        "    # Out THE HARD WAY, on purpose. Everything above has been printed and flushed;",
        "    # what remains is Qt and the interpreter unwinding each other, which aborts with",
        "    # 0xC0000409 and no message. That is a property of tearing a QApplication down",
        "    # from a script, not of the client — but an exit code that says 'crashed' when",
        "    # the checks passed is worse than useless to whoever reads it in CI.",
        "    sys.stdout.flush()",
        "    sys.stderr.flush()",
        "    os._exit(0 if ok else 1)",
        "",
        "",
        "if __name__ == '__main__':",
        "    # Without this a one-file exe re-runs the whole program in every worker it",
        "    # spawns, which shows up as the window opening several times.",
        "    multiprocessing.freeze_support()",
        "    if '--selftest' in sys.argv:",
        "        raise SystemExit(selftest())",
        "    raise SystemExit(ui.main())",
        "",
    ]
    target.write_text("\n".join(body), encoding="utf-8")
    return target


# What every release page says regardless of what changed in it: how to run the thing.
RELEASE_PREAMBLE = """Download `wddrop.exe` and run it. Nothing to install.

Set the game to 1920 x 1080 or 1600 x 900 (Options -> Screen size). Both are read without
setting anything up here, in a window or full screen. The tall 704 x 1241 window works too.
"""


def _client_version() -> str:
    """What the client calls itself, read rather than duplicated — the tag, the exe and the
    changelog all have to agree, and three copies of a number agree until they do not."""
    import re

    text = (HERE / "client" / "wddrop_client" / "config.py").read_text(encoding="utf-8")
    found = re.search(r'CLIENT_VERSION\s*=\s*"([^"]+)"', text)
    if not found:
        raise SystemExit("[!] client/wddrop_client/config.py has no CLIENT_VERSION")
    return found.group(1)


def release_notes(version: str, changelog: Path | None = None) -> str:
    """The release page's text: the standing how-to-run note, then THIS version's changelog.

    Read out of CHANGELOG.md rather than generated from the commit log, because the two would
    then say different things about the same release and the generated one would say less.
    A tool like git-cliff can only reach what fits in a commit SUBJECT; the entry that
    matters here is the one saying which recordings a fix affects and whether they are worth
    re-verifying, and that is written by hand.

    Raises if the version has no section: a release page that quietly says nothing about what
    changed is worse than a build that does not go out.
    """
    import re

    path = Path(changelog) if changelog else HERE / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    # From this version's heading to the next heading of the same level.
    # Up to the next version heading, the file's closing rule, or its link definitions —
    # the LAST section runs to the end of the file otherwise, and the release page picks up
    # the footer with it.
    found = re.search(
        r"^## \[%s\][^\n]*\n(.*?)(?=^## |^---\s*$|^\[[^\]]+\]:|\Z)" % re.escape(version),
        text, re.M | re.S)
    if not found:
        raise SystemExit(
            f"[!] CHANGELOG.md has no section for {version}.\n"
            f"    Add one under a `## [{version}] - <date>` heading — the release page is "
            f"built from it, and a release with nothing to say about itself is a release "
            f"nobody can decide about."
        )
    return RELEASE_PREAMBLE + "\n" + found.group(1).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--requirements", action="store_true",
                    help="print what has to be installed to build, one per line, so a "
                         "workflow can install exactly this list instead of keeping its own")
    ap.add_argument("--release-notes", metavar="VERSION", nargs="?", const="",
                    help="print the release page's text for VERSION (default: the version "
                         "the client calls itself), taken from CHANGELOG.md")
    ap.add_argument("--with-atlas", action="store_true",
                    help="bundle a prebuilt atlas, for a build that must work without the "
                         "game installed (see the licence note)")
    ap.add_argument("--name", default="wddrop")
    ap.add_argument("--console", action="store_true", help="keep a console window for logs")
    ap.add_argument("--production", action="store_true",
                    help="leave out the parts that are still in development (calibration)")
    args = ap.parse_args(argv)

    if args.release_notes is not None:
        version = args.release_notes or _client_version()
        # WRITTEN AS UTF-8 REGARDLESS OF THE CONSOLE. The release job runs this on Windows
        # and redirects it into NOTES.md, where stdout is cp1252 — and the notes quote the
        # game, so they carry 「10,000バイン紙幣」 and 「だれが開ける？」 and every × in a
        # quantity. That is a UnicodeEncodeError at character 4,469 and a release that does
        # not happen, for a reason no one would guess from "the build failed".
        sys.stdout.buffer.write(release_notes(version).encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    if args.requirements:
        # ONE PER LINE, for a requirements file rather than a command line. Half of these
        # carry a `>=`, which is a redirection operator to the shell that would expand them
        # — the same reason the message below quotes them — and a file has no such opinion.
        #
        # Exists so a build workflow does not keep a second copy of this list. It kept one,
        # the two drifted, and the exe would have shipped without the module that reads the
        # game's own window.
        print("\n".join(install for _module, install, _why in RUNTIME_IMPORTS))
        return 0

    # The interpreter that gets bundled, checked BEFORE anything is built. `uv run` picks
    # whatever it finds when nothing pins a version, and it found 3.9 here — below what the
    # client declares. The consequence is not an error: modules using newer syntax are
    # dropped from the bundle as `invalid module`, one line among hundreds of PyInstaller
    # log, and the exe is finished-looking and dead. Measured: wddrop_client.ui, over one
    # escaped apostrophe inside an f-string.
    if sys.version_info < PYTHON_FLOOR:
        print(f"[!] this is Python {'.'.join(map(str, sys.version_info[:3]))}, and the "
              f"client declares >= {'.'.join(map(str, PYTHON_FLOOR))}.\n"
              f"    Build with a matching one — `uv run --python "
              f"{'.'.join(map(str, PYTHON_FLOOR))} ...` — or the modules that need it are "
              f"silently left out of the exe.")
        return 1

    import importlib.util

    def installed(module: str) -> bool:
        """Is it importable? `find_spec` cannot answer that on its own.

        For a top-level name it returns None when absent, but for a SUBMODULE it imports the
        parent first and raises ModuleNotFoundError when that is missing. Exactly one entry
        here is a submodule — `windows_capture.windows_capture` — so the one dependency most
        likely to be missing on a fresh machine was the one that crashed this script with a
        traceback instead of printing the message written for it. Measured on the first CI
        build this repository ever ran.
        """
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    absent = [(module, install, why) for module, install, why in RUNTIME_IMPORTS
              if not installed(module)]
    if absent:
        print("[!] not installed here, so PyInstaller cannot put them in the exe:")
        for module, _install, why in absent:
            print(f"    {module:<12} {why}")
        print("\n    " + " ".join(
            ["uv run", f"--python {'.'.join(map(str, PYTHON_FLOOR))}", "--with pyinstaller"]
            # Quoted: `>=` is a redirection to every shell this is pasted into.
            + [f'--with "{install}"' for _m, install, _w in RUNTIME_IMPORTS]
            + ["build_exe.py"]))
        return 1

    wanted = [(name, why, need) for name, why, need in DATA
              if (args.with_atlas or name not in ATLAS) and ((HERE / name).exists() or need)]
    missing = [f"    {name:<24} {why}" for name, why, need in wanted
               if need and not (HERE / name).exists()]
    if missing:
        print("[!] these have to be built before the exe can be:\n" + "\n".join(missing))
        print("\n    tools/build_vocab.py, build_catalog.py, build_atlas.py make them.")
        return 1
    if not args.with_atlas:
        print("[=] no typeface inside: the client reads the game's own on the player's "
              "machine.")

    build = HERE / "build"
    build.mkdir(exist_ok=True)
    entry = entry_script(build / f"{args.name}_main.py")

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", args.name,
        "--paths", str(HERE / "client"),
        "--distpath", str(HERE / "dist"),
        "--workpath", str(build / "work"),
        "--specpath", str(build),
        # Imported inside functions all over the client, so static analysis misses them.
        "--hidden-import", "wddrop_client.ui",
        "--hidden-import", "wddrop_client.runner",
        "--hidden-import", "wddrop_client.calibration",
        "--hidden-import", "wddrop_schema.models",
        "--collect-submodules", "wddrop_client",
        "--collect-submodules", "wddrop_schema",
        # UnityPy reaches for submodules and data files at run time — without this it
        # imports and then fails on the first real call with "No module named
        # UnityPy.resources", which looks like a missing game rather than a broken build.
        "--collect-all", "UnityPy",
        # The compiled extension behind window capture. Named explicitly because nothing
        # imports it at module scope — it is reached inside a function, and only on Windows.
        "--hidden-import", "windows_capture.windows_capture",
        "--collect-binaries", "windows_capture",
        # PySide6-Essentials still carries modules the client never touches; leaving them in
        # costs ~40MB of exe for nothing.
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "matplotlib",
        "--exclude-module", "tkinter",
        # `windows_capture`'s convenience wrapper imports OpenCV at module scope to implement
        # a save-an-image helper. 42 MB, for a function the client does not call — and it
        # does not call it because it binds the native class the wrapper wraps. Excluded so
        # the exe stays the size it was; if something ever imports the wrapper, the build
        # will say so rather than quietly grow by two thirds.
        "--exclude-module", "cv2",
    ]
    if not args.console:
        cmd.append("--windowed")
    # The exe's own icon, not just a file inside it. PyInstaller converts a PNG when Pillow
    # is available, which it is here because the client needs it anyway.
    icon = HERE / "client" / "wddrop_client" / "icon.png"
    if icon.exists():
        cmd += ["--icon", str(icon)]
    for name, _why, _need in wanted:
        cmd += ["--add-data", f"{HERE / name}{';' if _windows() else ':'}."]
    if icon.exists():
        cmd += ["--add-data", f"{icon}{';' if _windows() else ':'}wddrop_client"]
    # The development marker, unless this is a production build. Its PRESENCE is what the
    # client checks, so a production exe carries no way to switch the unfinished parts back
    # on — see config.in_development.
    if not args.production:
        marker = HERE / "build" / "DEVELOPMENT"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("built without --production\n", encoding="utf-8")
        cmd += ["--add-data", f"{marker}{';' if _windows() else ':'}."]
    cmd.append(str(entry))

    print("[+] " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        return result.returncode

    exe = HERE / "dist" / (f"{args.name}.exe" if _windows() else args.name)
    if not exe.exists():
        print(f"[!] the build reported success but {exe.name} is not there")
        return 1
    print(f"[+] {exe}  ({exe.stat().st_size / 1_048_576:.0f} MB)")
    if args.with_atlas:
        print("[=] this exe contains the game's rendered font. Building it for yourself is "
              "the same as the files on your disk;\n    handing it to other people "
              "redistributes a derivative of a commercial typeface — see build_exe.py.")
    return 0


def _windows() -> bool:
    return sys.platform.startswith("win")


if __name__ == "__main__":
    raise SystemExit(main())

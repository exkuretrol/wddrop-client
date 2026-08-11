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
    vocab.<locale>.json              the item names the reader matches against
    catalog.<locale>.json            the dungeon and floor names the picker shows
    atlas.zh_tw.json + .png          the rendered glyphs — SEE THE LICENCE NOTE
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

# Everything the client looks for beside itself at runtime, and what it costs to omit each.
DATA = [
    ("DISCLAIMER.md", "the terms the player agrees to — the FIRST thing the window shows"),
    ("profiles.shipped.json", "the verified calibrations — without it every player calibrates"),
    ("vocab.zh_tw.json", "the item names — without it nothing can be recognised at all"),
    ("catalog.zh_tw.json", "the dungeon list for the picker, and the window will not start "
                           "without it"),
    ("atlas.zh_tw.json", "the glyph atlas index"),
    ("atlas.zh_tw.png", "the glyph atlas sheet"),
]
ATLAS = {"atlas.zh_tw.json", "atlas.zh_tw.png"}


def entry_script(target: Path) -> Path:
    """A launcher with no sys.path games in it.

    wddrop.py puts client/ and packages/schema/ on the path at run time, which is right for a
    checkout and wrong for a bundle: PyInstaller has to see the imports statically or it packs
    neither package.
    """
    body = [
        '"""Entry point for the bundled client — see build_exe.py."""',
        "import multiprocessing",
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
        "    from wddrop_client.config import bundled_dir, program_dir",
        "",
        "    print('frozen     :', getattr(sys, 'frozen', False))",
        "    print('exe folder :', program_dir())",
        "    print('bundled at :', bundled_dir())",
        "    ok = True",
        "    for pattern in ('vocab.{locale}.json', 'atlas.{locale}.json',",
        "                    'catalog.{locale}.json'):",
        "        hit = ui.find_data(pattern, 'zh_tw')",
        "        ok = ok and hit is not None",
        "        print('  %-24s %s' % (pattern, hit))",
        "    shipped = ProfileStore.shipped()",
        "    print('  %-24s %s' % ('shipped profiles', shipped.keys()))",
        "    ok = ok and bool(shipped.keys())",
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
        "        print('  %-24s %s' % ('calibration', window.cal_label.text()))",
        "        print('  %-24s %s' % ('ready to record', window._ready))",
        "        ok = ok and bool(window._ready)",
        "        app.processEvents()",
        "    except Exception as exc:",
        "        ok = False",
        "        print('  %-24s %s: %s' % ('window', type(exc).__name__, exc))",
        "    print('OK' if ok else 'MISSING DATA')",
        "    return 0 if ok else 1",
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-atlas", action="store_true",
                    help="leave the rendered game font out of the exe (see the licence note)")
    ap.add_argument("--name", default="wddrop")
    ap.add_argument("--console", action="store_true", help="keep a console window for logs")
    args = ap.parse_args(argv)

    wanted = [(name, why) for name, why in DATA if not (args.no_atlas and name in ATLAS)]
    missing = [f"    {name:<24} {why}" for name, why in wanted if not (HERE / name).exists()]
    if missing:
        print("[!] these have to be built before the exe can be:\n" + "\n".join(missing))
        print("\n    tools/build_vocab.py, build_catalog.py, build_atlas.py make them.")
        return 1
    if args.no_atlas:
        print("[=] no atlas inside: the exe will need atlas.zh_tw.json/.png beside it.")

    build = HERE / "build"
    build.mkdir(exist_ok=True)
    entry = entry_script(build / f"{args.name}_main.py")

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", args.name,
        "--paths", str(HERE / "client"),
        "--paths", str(HERE / "packages" / "schema"),
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
        # PySide6-Essentials still carries modules the client never touches; leaving them in
        # costs ~40MB of exe for nothing.
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "matplotlib",
        "--exclude-module", "tkinter",
    ]
    if not args.console:
        cmd.append("--windowed")
    # The exe's own icon, not just a file inside it. PyInstaller converts a PNG when Pillow
    # is available, which it is here because the client needs it anyway.
    icon = HERE / "client" / "wddrop_client" / "icon.png"
    if icon.exists():
        cmd += ["--icon", str(icon)]
    for name, _why in wanted:
        cmd += ["--add-data", f"{HERE / name}{';' if _windows() else ':'}."]
    if icon.exists():
        cmd += ["--add-data", f"{icon}{';' if _windows() else ':'}wddrop_client"]
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
    if not args.no_atlas:
        print("[=] this exe contains the game's rendered font. Building it for yourself is "
              "the same as the files on your disk;\n    handing it to other people "
              "redistributes a derivative of a commercial typeface — see build_exe.py.")
    return 0


def _windows() -> bool:
    return sys.platform.startswith("win")


if __name__ == "__main__":
    raise SystemExit(main())

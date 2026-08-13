"""The launcher's own dependency list, which is one of three and drifted.

`wddrop.py` carries a PEP 723 header so that `uv run wddrop.py ui` works out of a folder
with nothing installed. `build_exe.RUNTIME_IMPORTS` says what the exe needs.
`client/pyproject.toml` says what an installed client needs. Three lists for one set of
facts, and this project has already paid twice for exactly that shape:

  * the release workflow kept its own copy of the build's dependencies, drifted, and would
    have shipped an exe with no atlas builder and no window capture;
  * this header was never updated when 0.5.0 stopped shipping the atlas, so the documented
    way to run the client produced one that says "UnityPy is not installed" and can never
    build a font atlas — reported, reasonably, as recognition being broken.

The exe's list is the authority: it is the one whose omissions are measured, by a self-check
that imports every module from inside the frozen bundle.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# PySide6 is deliberately in none of them. An 80MB GUI toolkit must not become a requirement
# of reading the screen, so the window is asked for explicitly: `--with PySide6-Essentials`.
ASKED_FOR_SEPARATELY = {"PySide6"}


def _script_dependencies() -> list[str]:
    """The PEP 723 block at the top of the launcher."""
    text = (ROOT / "wddrop.py").read_text(encoding="utf-8")
    block = re.search(r"# /// script\n(.*?)# ///", text, re.S)
    assert block, "wddrop.py has lost its PEP 723 header — `uv run wddrop.py` needs it"
    body = "".join(line.lstrip("#").strip() for line in block.group(1).splitlines()
                   if not line.lstrip("#").strip().startswith("#"))
    names = re.findall(r'"([^"]+)"', body)
    return names


def test_the_launcher_asks_for_everything_the_exe_bundles():
    """Whatever the frozen client needs, the script needs — it is the same client."""
    import build_exe

    declared = " ".join(_script_dependencies()).lower()
    missing = []
    for module, install, _why in build_exe.RUNTIME_IMPORTS:
        if module in ASKED_FOR_SEPARATELY:
            continue
        # `windows_capture.windows_capture` installs as `windows-capture`; compare on the
        # INSTALL name, which is what a dependency list actually carries.
        if install.split(">=")[0].split("[")[0].lower() not in declared:
            missing.append(f"{install} ({module})")
    assert not missing, (
        "wddrop.py's PEP 723 header is missing what the exe bundles: " + ", ".join(missing)
        + ". Running from a folder would then differ from running the exe, silently.")


def test_the_launcher_does_not_drag_in_the_window():
    """A capture path that cannot run without Qt is a capture path nobody can test headless,
    and 80MB of it would be downloaded by every `uv run wddrop.py replay`."""
    declared = " ".join(_script_dependencies()).lower()
    assert "pyside6" not in declared


def test_the_python_floor_matches_the_one_the_build_enforces():
    """`build_exe` refuses to build below its floor because modules using newer syntax are
    dropped from the bundle as `invalid module` — one line in a thousand of PyInstaller log,
    and an exe that is finished-looking and dead. The launcher must not invite an older one."""
    import build_exe

    text = (ROOT / "wddrop.py").read_text(encoding="utf-8")
    stated = re.search(r'requires-python\s*=\s*">=(\d+)\.(\d+)"', text)
    assert stated, "the launcher no longer states a Python floor"
    assert tuple(int(v) for v in stated.groups()) == build_exe.PYTHON_FLOOR

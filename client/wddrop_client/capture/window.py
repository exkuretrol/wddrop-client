"""
Finding and measuring the game window on Windows.

Capturing the whole monitor does not work in practice: the player's screen is 2560x1440
while the game runs windowed at 1920x1080, so a full-monitor grab never matches a profile
calibrated from a window screenshot — and every region in that profile is absolute pixels.

Implemented with ctypes against user32 rather than pywin32, so there is no extra dependency
to install and the client stays a plain `uv run` away.

The CLIENT rect is used, not the window rect: the client area excludes the title bar and
borders, which is what a window screenshot contains and therefore what the profile was fitted
to. Using the window rect would offset everything by the title bar height.

DPI awareness is set before any measurement. Without it Windows reports *virtualised*
coordinates on a scaled display, so the numbers would silently disagree with the physical
pixels that actually get captured.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

log = logging.getLogger("wddrop.window")

# The game's executable, which is always WizardryVariantsDaphne. This is the identifying
# signal: titles are not ours to control, and matching them by substring picked a DISCORD
# window whose channel happened to be named after the game -- the client then tried to read a
# 2560x1392 chat window as if it were the game.
GAME_PROCESS_NAMES = ("wizardryvariantsdaphne.exe", "daphne.exe")

# Fallback for when the process cannot be read. Deliberately EXACT titles only, not
# substrings: a loose "Wizardry" or "Daphne" is precisely what matched a chat window. A user
# who passes --source "window:<text>" is asking for a substring and gets one.
DEFAULT_TITLE_EXACT = ("WizardryVariantsDaphne", "Wizardry Variants Daphne", "ウィザードリィ ヴァリアンツ ダフネ")


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    left: int
    top: int
    width: int
    height: int
    process: str = ""

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def as_region(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height


def _require_windows() -> None:
    if not sys.platform.startswith("win"):
        raise SystemExit(
            "[!] window capture is Windows-only.\n"
            "    On other platforms use --source screen, or replay a recording."
        )


def set_dpi_aware() -> None:
    """Ask Windows for physical pixels.

    Must happen before any window is measured. On a scaled display an unaware process is fed
    virtualised coordinates, which would disagree with the pixels actually captured — and the
    mismatch is silent, producing a profile that reads the wrong part of the screen.
    """
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception as exc:
            log.warning("wddrop: could not set DPI awareness (%s); coordinates may be scaled", exc)


def list_windows(min_size: tuple[int, int] = (200, 200)) -> list[WindowInfo]:
    """Visible top-level windows with a title, largest first."""
    _require_windows()
    import ctypes
    from ctypes import wintypes

    set_dpi_aware()
    user32 = ctypes.windll.user32
    found: list[WindowInfo] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        info = _client_rect(hwnd, buf.value, _process_name(hwnd))
        if info and info.width >= min_size[0] and info.height >= min_size[1]:
            found.append(info)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    found.sort(key=lambda w: w.width * w.height, reverse=True)
    return found


def _process_name(hwnd: int) -> str:
    """Executable behind a window, lowercased. Empty if it cannot be read.

    Read via QueryFullProcessImageNameW with PROCESS_QUERY_LIMITED_INFORMATION, which needs
    no elevation for a normal user process.
    """
    import ctypes
    from ctypes import wintypes

    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _client_rect(hwnd: int, title: str, process: str = "") -> WindowInfo | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return WindowInfo(hwnd, title, origin.x, origin.y, width, height, process)


def client_region(handle: int) -> tuple[int, int, int, int] | None:
    """Where a KNOWN window's client area is right now, or None if it is gone.

    Two syscalls, no enumeration and no process lookup — cheap enough to ask on every frame,
    which is the point: `find_window` costs an EnumWindows plus a process name per candidate
    and is a once-per-session operation, and a region read once per session is a region that
    is wrong the moment the player drags the window.
    """
    if not sys.platform.startswith("win"):
        return None
    info = _client_rect(handle, "")
    return info.as_region() if info else None


def find_window(title: str | None = None, expect_size: tuple[int, int] | None = None) -> WindowInfo:
    """Locate the game window.

    Candidates are ranked rather than taken first-match, because the weakest signal (a title
    substring) is the one most likely to collide with something else on the desktop:

        1. the executable is the game's                     -- decisive, and always available
        2. the client size equals the calibrated size       -- strong
        3. the title matches EXACTLY                        -- good
        4. the title contains the substring the CALLER gave -- only when asked for

    Note what is absent: a built-in substring search. The game process is always
    WizardryVariantsDaphne, so guessing from titles buys nothing and costs correctness.
    """
    windows = list_windows()
    if not windows:
        raise SystemExit("[!] no visible windows found")

    exact_titles = [title] if title else list(DEFAULT_TITLE_EXACT)
    # Substring matching only when the caller explicitly asked for a title.
    substrings = [title] if title else []

    def rank(w: WindowInfo) -> tuple:
        by_process = w.process in GAME_PROCESS_NAMES
        by_size = bool(expect_size) and (w.width, w.height) == tuple(expect_size)
        exact = any(w.title.lower() == h.lower() for h in exact_titles)
        partial = any(h.lower() in w.title.lower() for h in substrings)
        return (by_process, by_size, exact, partial)

    best = max(windows, key=rank)
    score = rank(best)
    if not any(score):
        listing = "\n".join(f"        {w.width}x{w.height}  {w.process or '?'}  {w.title}"
                            for w in windows[:12])
        raise SystemExit(
            (f"[!] no window matched {title!r}.\n" if title else
             "[!] could not find the game window automatically.\n")
            + f"    Pass --source \"window:<part of the title>\". Visible windows:\n{listing}"
        )
    log.info("wddrop: window %r (%s) at %dx%d (%d,%d) [matched on %s]",
             best.title, best.process or "unknown process", best.width, best.height,
             best.left, best.top,
             "process" if score[0] else "size" if score[1] else "exact title" if score[2]
             else "title substring")
    return best


# WHAT THE GAME ITSELF IS SET TO RENDER
# -------------------------------------
# Unity writes the player's chosen resolution to the registry, so this is the game's own
# answer rather than an inference from pixels. It matters when the captured frame is larger
# than the calibration (see calibration.scaled_from): the layout is correct after resampling
# whatever the game renders at — the render resolution cancels, because a UI element is
# `units * height / 1920` at every step — but the INK does not. Measured over 15 confirmed
# chest lines, read with the 1920x1080 fit after the full chain:
#
#     game 1920x1080 -> 2560x1440 screen -> resampled    mean 0.8905   min 0.8473
#     game 1280x 720 -> 2560x1440 screen -> resampled    mean 0.8284   min 0.5626  <- under
#                                                                                     the gate
# The last one loses a reading with no error anywhere: an under-gate line is dropped by
# design. So a game rendering below the calibration is worth saying out loud.
GAME_PREFS_KEY = r"Software\drecom\WizardryVariantsDaphne"
# Unity appends a hash of the preference name; these are that key's actual value names.
_WIDTH_VALUE = "Screenmanager Resolution Width_h182942802"
_HEIGHT_VALUE = "Screenmanager Resolution Height_h2627697771"


def rendered_resolution() -> tuple[int, int] | None:
    """The resolution the game is rendering at, from its own settings. None off Windows,
    or when the game has never saved a resolution."""
    try:
        import winreg
    except ImportError:                                # not Windows: nothing to read
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GAME_PREFS_KEY) as key:
            width, _ = winreg.QueryValueEx(key, _WIDTH_VALUE)
            height, _ = winreg.QueryValueEx(key, _HEIGHT_VALUE)
    except OSError:
        return None
    if not width or not height:
        return None
    return int(width), int(height)

"""
How the window looks, and why.

THE BRIEF, PINNED DOWN
----------------------
This window is not the thing being looked at. The player is playing a dungeon crawler in
another window for hours; they glance at this one before starting, occasionally mid-run, and
at the end. So its job is not to be admired — it is to answer, in about a second and from
across a desk, one question:

    "is my session being recorded properly, or am I wasting my evening?"

Everything below follows from that. The setup controls are used once and then never again in
a session, so they shrink away. The live log is what gets glanced at for hours, so it gets
the room. And anything wrong has to be impossible to miss without being alarming when
nothing is.

THE INK IS THE GAME'S OWN
-------------------------
The primary text colour is #E2CCB2, which is not a taste decision: it is the exact colour
the game paints its drop messages with, lifted from `DungeonTreasure@DropItem` in the
client_texts table —

    <color=#E2CCB2>獲得了{0}！！</color>

So every line this instrument records is shown in the same ink the game wrote it in. It is
the one flourish in the design, and it is the only one that could not belong to any other
project.

The rest of the palette is torchlight in a stone dungeon: warm near-black rather than the
blue-black that dark UIs default to, an ember for attention, and a lichen green for "this is
working". Both states are muted on purpose — a bright acid accent on near-black is the house
style of every generated dark interface, and this one is meant to look like an instrument in
a specific game's world, not like a dashboard.

TYPE
----
Three roles, all from faces Windows already has, because a font that fails to load takes the
layout with it:

    display   Cambria        — the section and state voice. A serif, because the subject is
                               a gothic RPG and its own UI is serif.
    body      Segoe UI       — controls and prose. It also carries the CJK fallback well.
    data      Consolas       — elapsed times, counters, quantities. Anything the eye scans
                               down a column rather than reads.

No emoji anywhere: they render as tofu boxes wherever the font lacks the glyph, and the
window must not depend on which fonts a player happens to have.
"""
from __future__ import annotations

# -- palette ---------------------------------------------------------------------
PITCH = "#14110F"      # background: warm near-black, the dark of a lit stone room
STONE = "#1F1B18"      # raised surfaces
RULE = "#3A322C"       # hairlines
INK = "#E2CCB2"        # the game's own drop-message colour; primary text
VELLUM = "#F3E8D8"     # emphasis, headings
MUTED = "#8A7C6D"      # secondary text, labels
EMBER = "#C4622D"      # attention: something needs the player's eye
MOSS = "#7E8B5A"       # recording, healthy

# Declared as lists and rendered into the sheet, so the QFont built in code and the
# font-family written into the CSS can never drift apart.
DISPLAY_FAMILIES = ("Cambria", "Georgia", "serif")
BODY_FAMILIES = ("Segoe UI", "Microsoft JhengHei", "Yu Gothic UI", "sans-serif")
DATA_FAMILIES = ("Consolas", "Menlo", "monospace")


def _css(families) -> str:
    return ", ".join(f'"{f}"' if " " in f else f for f in families)


DISPLAY = _css(DISPLAY_FAMILIES)
BODY = _css(BODY_FAMILIES)
DATA = _css(DATA_FAMILIES)


BODY_PIXELS = 13


def apply_font(app) -> None:
    """Give the application itself a PIXEL-sized font, to match the sheet.

    Windows hands Qt a default application font measured in POINTS (9pt Segoe UI), while
    every size in the sheet below is in pixels. A widget built under the sheet is fine; one
    that starts from the application font and has pixel sizes applied over it leaves the two
    units to be reconciled.

    This was written to remove a "QFont::setPointSize: Point size <= 0 (-1)" and it did NOT
    remove it — see BENIGN below. It is kept because one unit for type is right anyway, not
    because it fixed anything; the note is here so the next person does not read a cure into
    it. The sheet keeps its own `font-size` rules, so nothing depends on this having run.
    """
    from PySide6 import QtGui

    font = QtGui.QFont(app.font())
    font.setFamilies(list(BODY_FAMILIES))
    font.setPixelSize(BODY_PIXELS)
    app.setFont(font)


# One message, dropped by name. Qt emits it while polishing the widget tree under the
# Windows 11 style, rejects its own invalid call, and carries on — nothing renders wrong.
#
# It is filtered rather than fixed because it could not be placed. It survived giving the
# application a pixel-sized font, and it survived moving popup construction out of startup;
# every rule in this sheet that names a font also gives it an explicit px size, so there is
# no relative size for Qt to adjust; and it does not reproduce on another platform's style
# with the same sheet, the same font and the same widgets reparented the same way. The stack
# it fires from has no Python frame nearer than `addWidget`, so there is nothing here to
# change with any confidence that it is the thing.
#
# Everything else Qt says still reaches the console, and WDDROP_QT_TRACE=1 shows this one
# too, with a stack.
BENIGN = ("QFont::setPointSize: Point size <= 0",)


def is_benign(message: str) -> bool:
    """Exact prefixes only. A filter that matched loosely would hide the next real one."""
    return any(message.startswith(known) for known in BENIGN)


def install_message_filter() -> None:
    """Keep Qt's console output, minus one message that cannot be acted on.

    WDDROP_QT_TRACE=1 turns this into the opposite: everything is printed, with a Python
    stack, which is how the last one was traced to the widget tree being polished.
    """
    import os
    import sys
    import traceback

    from PySide6 import QtCore

    tracing = os.environ.get("WDDROP_QT_TRACE") == "1"
    if tracing:
        # Say so. Tracing prints everything INCLUDING what is normally filtered, so without
        # this line a diagnostic left switched on in a shell looks exactly like a bug that
        # was never fixed — which is how it read once already.
        print("[qt] tracing on (WDDROP_QT_TRACE=1) — filtered messages are shown too. "
              "Unset it with:  Remove-Item Env:WDDROP_QT_TRACE", flush=True)

    def handler(mode, context, message):
        if tracing:
            print(f"[qt] {message}", flush=True)
            # Qt names no call site, so the Python frames are the only evidence of what was
            # being built when it fired.
            traceback.print_stack()
            return
        if is_benign(message):
            return
        print(message, file=sys.stderr, flush=True)

    QtCore.qInstallMessageHandler(handler)


def data_font(pixels: int = 12):
    """The column-scanning face, sized in PIXELS like everything else here.

    Sized in pixels deliberately: the sheet gives every widget a pixel size, and mixing the
    two units leaves Qt reconciling them. One unit throughout, for the same reason as
    `apply_font` — and, like it, this did not silence the warning it was written for.
    """
    from PySide6 import QtGui

    font = QtGui.QFont()
    font.setFamilies(list(DATA_FAMILIES))
    font.setPixelSize(pixels)
    return font


def apply_item_highlight(view) -> None:
    """Theme the bar that appears on the cell a player clicks. The sheet cannot do this.

    Both tables are NoSelection, so `QTableWidget::item:selected` below never matches — and
    yet clicking a cell still paints a bar, because Qt marks that cell CURRENT and the style
    fills it from the palette's Highlight brush. That brush is the platform accent: a blue
    gradient, i.e. the one cold colour in a room lit by torchlight, arriving on the one
    surface the player just pointed at.

    Qt offers no `::item:current` pseudo-state, so the fix is the palette rather than the
    sheet. RULE on VELLUM is the pairing the sheet already uses for a chosen row in the
    combobox popup, so "the thing I clicked" looks the same everywhere in the window.

    Inactive carries the same pair deliberately. Qt greys that group out when the window
    loses focus, and this window is glanced at FROM another window that has focus — a bar
    that changes colour on the way past would read as a state change that has not happened.
    """
    from PySide6 import QtGui

    palette = view.palette()
    for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
        palette.setColor(group, QtGui.QPalette.Highlight, QtGui.QColor(RULE))
        palette.setColor(group, QtGui.QPalette.HighlightedText, QtGui.QColor(VELLUM))
    view.setPalette(palette)


# THE SCROLLBAR, AS ITS OWN RULES.
#
# A widget given its own stylesheet stops inheriting the application's for everything it
# contains — including its scrollbar, which then falls back to the platform's. The guide page
# sets a stylesheet for its padding, and so had the one scrollbar in the window that did not
# match the others: a wide grey system bar in a column of thin dark ones. Anything setting a
# local stylesheet has to paste these in with it.
# Written with DOUBLED braces because it is a `.format` template, and inserted into the
# sheet through `scrollbar()` — never as `{SCROLLBAR}`. The sheet itself is an f-string, so
# dropping the raw template in leaves `{{` and `{PITCH}` sitting in the CSS, Qt discards
# every rule it cannot parse, and the whole window loses its scrollbars while the one widget
# that called `scrollbar()` keeps them. That is precisely how this was first shipped.
SCROLLBAR = """
    QScrollBar:vertical {{ background: {PITCH}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {RULE}; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: {PITCH}; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {RULE}; min-width: 30px; }}
"""


def scrollbar() -> str:
    """The scrollbar rules, resolved — for a widget that carries its own stylesheet."""
    return SCROLLBAR.format(PITCH=PITCH, RULE=RULE)


def stylesheet() -> str:
    """One sheet for the whole window.

    Qt's style sheets are CSS-like but not CSS: selectors are class names, there is no
    cascade to speak of, and a property Qt does not know is dropped in silence. So this stays
    deliberately flat — no nesting, no reliance on inheritance beyond colour and font.
    """
    return f"""
    QWidget {{
        background: {PITCH};
        color: {INK};
        font-family: {BODY};
        font-size: {BODY_PIXELS}px;
    }}
    /* A LABEL IS TEXT, NOT A SURFACE.
       The blanket rule above gives every widget an opaque {PITCH} background, labels
       included — so every piece of text sitting on the ribbon or the footer, both of which
       are raised to {STONE}, painted its own darker rectangle on top of them. The title and
       the state line became dark plates floating on the ribbon, and the navigation became
       four separate tiles with the ribbon showing through as seams between them.
       Transparent, they are part of the surface they sit on, and it stays continuous. */
    QLabel {{ background: transparent; }}
    QLabel#state {{
        font-family: {DISPLAY};
        font-size: 15px;
        color: {MUTED};
    }}
    /* The navigation had no rule of its own at all, so it inherited the dark plate and its
       current-page property styled nothing — the page you were on was not marked. */
    QLabel#nav {{
        color: {MUTED};
        padding: 3px 9px;
        border-bottom: 1px solid transparent;
    }}
    QLabel#nav:hover {{ color: {INK}; }}
    QLabel#nav[current="true"] {{
        color: {VELLUM};
        border-bottom: 1px solid {MOSS};
    }}
    /* The name, in the display face and the brightest ink the ribbon uses. It sits on the
       same row as the navigation, so it has to read as a heading and not as a fifth thing
       that can be clicked — hence the weight. */
    QLabel#wordmark {{
        font-family: {DISPLAY};
        font-size: 15px;
        color: {VELLUM};
        padding-right: 6px;
    }}
    QLabel#state[tone="recording"] {{ color: {MOSS}; }}
    QLabel#state[tone="attention"] {{ color: {EMBER}; }}
    QLabel#meta {{
        font-family: {DATA};
        font-size: 12px;
        color: {MUTED};
    }}
    QFrame#ribbon {{
        background: {STONE};
        border: none;
        border-bottom: 1px solid {RULE};
    }}
    QFrame#footer {{
        background: {STONE};
        border: none;
        border-top: 1px solid {RULE};
    }}
    QLabel#section {{
        font-family: {DISPLAY};
        font-size: 12px;
        color: {MUTED};
    }}
    QLabel#hint {{ color: {MUTED}; }}
    /* The page before it has anything to show. Set in the display face at reading size and
       centred in the space the ledger will occupy: it is a sentence addressed to the
       player, not a caption on an absence. */
    QLabel#empty {{
        font-family: {DISPLAY};
        font-size: 15px;
        color: {MUTED};
        padding: 40px;
    }}
    /* The tally. These are the numbers the page exists to show, so they are the only place
       in the window where the display face runs large. */
    QLabel#headline {{
        font-family: {DISPLAY};
        font-size: 22px;
        color: {VELLUM};
    }}
    QLabel#erasure {{ color: {MUTED}; font-size: 11px; }}

    /* Every corner in this window is square. Qt's own styles round some of these by
       default, so it is stated rather than left to whichever style Windows hands us. */
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        background: {STONE};
        border: 1px solid {RULE};
        border-radius: 0;
        padding: 5px 8px;
        color: {INK};
        selection-background-color: {RULE};
    }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
        border: 1px solid {INK};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    /* The popup is a raised surface with a border, and Qt gives its rows no padding at all
       — so 56 dungeon names sat flush against the frame with their descenders touching each
       other. Anything drawn on {STONE} gets room to breathe. */
    QComboBox QAbstractItemView {{
        background: {STONE};
        color: {INK};
        selection-background-color: {RULE};
        border: 1px solid {RULE};
        border-radius: 0;
        padding: 4px;
        outline: none;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 6px 10px;
        min-height: 20px;
        border: none;
    }}
    QComboBox QAbstractItemView::item:selected {{ background: {RULE}; color: {VELLUM}; }}
    /* No ::separator rule here. It is honoured for a menu and ignored for a list view,
       which is what a combobox popup is — set here it produced a 32px gap and no line. The
       rule between groups of dungeons is drawn by RoomyRows, in ui.py. */
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {RULE};
        background: {PITCH};
    }}
    QCheckBox::indicator:checked {{ background: {INK}; }}
    QCheckBox:disabled {{ color: {MUTED}; }}

    QPushButton {{
        background: {STONE};
        border: 1px solid {RULE};
        border-radius: 0;
        padding: 7px 16px;
        color: {INK};
    }}
    QPushButton:hover {{ border: 1px solid {INK}; }}
    QPushButton:disabled {{ color: {MUTED}; border: 1px solid {RULE}; }}
    /* Same SIZE as its neighbours, and told apart by colour instead. It sat in a row of
       ordinary buttons at a larger font and heavier padding, which read as a misaligned
       control rather than as emphasis — and the row it lives in is the one place where a
       ragged baseline is obvious. */
    QPushButton#primary {{
        color: {VELLUM};
        border: 1px solid {MOSS};
    }}
    QPushButton#primary:hover {{ background: {RULE}; }}
    QPushButton#primary:disabled {{ color: {MUTED}; border: 1px solid {RULE}; }}
    QPushButton#primary[running="true"] {{ border: 1px solid {EMBER}; color: {EMBER}; }}

    QTableWidget {{
        background: {PITCH};
        border: none;
        gridline-color: transparent;
        font-size: 14px;
    }}
    QTableWidget::item {{ padding: 7px 10px; border-bottom: 1px solid {RULE}; }}
    QTableWidget::item:selected {{ background: {STONE}; color: {INK}; }}
    QHeaderView::section {{
        background: {PITCH};
        color: {MUTED};
        border: none;
        border-bottom: 1px solid {RULE};
        padding: 4px 10px;
        font-family: {DATA};
        font-size: 11px;
    }}
    {scrollbar()}

    QTextBrowser {{ background: {STONE}; border: 1px solid {RULE}; padding: 14px 16px; }}
    QToolTip {{
        background: {STONE};
        color: {INK};
        border: 1px solid {RULE};
        padding: 5px 8px;
    }}
    """


# -- the window's own frame ------------------------------------------------------
#
# The title bar is drawn by Windows, not by Qt, so a themed window with a stock caption is
# a dark instrument wearing a light hat. These are DWM attributes rather than anything Qt
# offers: 20 turns the caption dark (Windows 10 1809+; the same flag was 19 on early
# builds), and 34/35/36 set exact colours on Windows 11 22000+. Unsupported attributes
# return a failure code and change nothing, so asking for all of them is how one call
# covers every version.
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36

DWMWCP_DONOTROUND = 1


def icon_path():
    """The wordmark, shared with the dungeon map so the two read as one set of tools."""
    from pathlib import Path

    icon = Path(__file__).resolve().parent / "icon.png"
    return icon if icon.exists() else None


def apply_icon(app) -> bool:
    """Give the window and the taskbar the wordmark instead of a generic Python feather.

    The AppUserModelID is the part that is easy to miss. Windows groups taskbar buttons by
    it, and a process started through an interpreter inherits the interpreter's — so without
    this the window shows our icon while the taskbar shows Python's, which is both wrong and
    the thing people actually look at.
    """
    from PySide6 import QtGui

    icon = icon_path()
    if icon is None:
        return False
    import sys

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kuaz.wddrop")
        except Exception:                              # noqa: BLE001 — decoration only
            pass
    app.setWindowIcon(QtGui.QIcon(str(icon)))
    return True


def _colorref(hex_colour: str) -> int:
    """#RRGGBB -> COLORREF, which is 0x00BBGGRR — byte-reversed from the web order."""
    value = hex_colour.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (b << 16) | (g << 8) | r


def _set_attribute(widget, which: int, value: int) -> bool:
    import sys

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(widget.winId())), ctypes.c_uint(which),
            ctypes.byref(ctypes.c_int(value)), ctypes.sizeof(ctypes.c_int))
        return True
    except Exception:                                  # noqa: BLE001 — decoration only
        return False


def _plain_style_class():
    """Fusion, with one answer changed: a dropdown is a LIST, not a menu.

    Fusion says yes to SH_ComboBox_Popup and `windows11` says no, and that single hint is
    the difference between two quite different widgets:

        yes   a menu — drawn OVER the combobox, centred on the current entry, inside a
              container with its own frame and scroll indicators. Those indicators are the
              white bars that appeared above and below the list, and the overlap is why the
              control disappeared behind its own dropdown.
        no    a list — dropped BELOW the combobox, no container chrome, and `maxVisibleItems`
              is honoured again.

    The rest of Fusion is what squares the corners, so it is kept and only this is answered
    differently.
    """
    from PySide6 import QtWidgets

    class PlainPopup(QtWidgets.QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, data=None):   # noqa: N802 (Qt)
            if hint == QtWidgets.QStyle.StyleHint.SH_ComboBox_Popup:
                return 0
            return super().styleHint(hint, option, widget, data)

    return PlainPopup


# The style the application is drawn with, kept alive for as long as the process is. Qt does
# NOT take ownership of a style passed to setStyle, and one collected while it is still in
# use takes the process with it.
_STYLE = None
# ...and how it is recognised again. A QProxyStyle does not inherit the name of the style it
# wraps — it reports an empty one — so "is this already applied?" cannot be asked by name
# unless a name is put there. Without it every call wrapped the wrapper: proxy over proxy
# over Fusion, one layer per window built.
STYLE_NAME = "wddrop-plain"


def apply_style(app) -> bool:
    """Draw the whole application with a plain style. Call once, before any window exists.

    THIS IS WHAT SQUARES THE DROPDOWNS, and it has to be the application's style rather than
    each popup's.

    From Qt 6.7 the default style on Windows 11 is `windows11`, which paints a combobox popup
    as a rounded flyout. The style sheet cannot reach that — `border-radius: 0` describes the
    frame the sheet draws, not the one the style does — and neither can the compositor
    attribute below, which is about the window and not about what is painted inside it.

    Setting the style on the popup widget was tried first and does NOT work: a widget with a
    style sheet is wrapped in Qt's own style-sheet style, and handing that widget another
    base style leaves the wrapper drawing the frame. Measured by screenshotting a real popup
    of the real window: still an arc some eight pixels deep. Set here, the same popup comes
    out square, with the sheet colouring it exactly as before.

    Idempotent, so an entry point that does not know whether another already called it can
    call it anyway.
    """
    global _STYLE

    from PySide6 import QtWidgets

    if app is None or app.style().objectName() == STYLE_NAME:
        return False
    base = QtWidgets.QStyleFactory.create("Fusion")
    if base is None:                                   # a Qt build without it: nothing to do
        return False
    _STYLE = _plain_style_class()(base)
    _STYLE.setObjectName(STYLE_NAME)
    app.setStyle(_STYLE)
    return True


def square_corners(widget, view=None) -> bool:
    """Stop the COMPOSITOR rounding a popup. No-op anywhere but Windows.

    The other half of the problem, and the smaller one: Windows 11 rounds every window it
    draws, and a combobox's list is its own window. That rounding is DWM's, applied to the
    window rather than to the widget, so it is turned off per window as each one appears.

    What is painted INSIDE the window is Qt's business — see `apply_style`, which is what
    stops the style itself drawing a rounded flyout.
    """
    return _set_attribute(widget, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)


def apply_titlebar(window) -> bool:
    """Paint the native title bar to match the ribbon. No-op anywhere but Windows.

    Called after the window exists, because it needs a real HWND. Everything here is
    best-effort by design: this is decoration, and a decoration must never be able to stop
    a capture client from opening.
    """
    import sys

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        handle = ctypes.c_void_p(int(window.winId()))
        dwm = ctypes.windll.dwmapi

        def attribute(which: int, value: int) -> None:
            dwm.DwmSetWindowAttribute(
                handle, ctypes.c_uint(which),
                ctypes.byref(ctypes.c_int(value)), ctypes.sizeof(ctypes.c_int))

        attribute(DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        attribute(DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1)
        # Square, like everything inside it. Windows 11 rounds top-level windows by default.
        attribute(DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)
        # STONE, not PITCH: the caption sits directly above the ribbon, and matching that
        # makes the frame end where the window ends rather than at an arbitrary seam.
        attribute(DWMWA_CAPTION_COLOR, _colorref(STONE))
        attribute(DWMWA_TEXT_COLOR, _colorref(VELLUM))
        attribute(DWMWA_BORDER_COLOR, _colorref(RULE))
        return True
    except Exception:                                  # noqa: BLE001 — decoration only
        return False

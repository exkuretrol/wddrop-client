"""
The window's own language.

Two failures are worth a test. A string added without its translations degrades quietly to
English in five languages — which is how the guide came to be an English page inside a
Chinese window, found by a player rather than by us. And a translation that drops a
`{placeholder}` does not degrade at all: it raises KeyError at format time, in front of the
player, on whatever line it happens to be.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

from wddrop_client.i18n import (FALLBACK, LOCALES, NATIVE_NAMES, STRINGS,  # noqa: E402
                                Translator, match_locale)

TRANSLATED = [code for code in LOCALES if code != FALLBACK]


def test_every_string_exists_in_every_language():
    missing = sorted(
        (code, key) for key, values in STRINGS.items()
        for code in TRANSLATED if code not in values)
    assert not missing, f"{len(missing)} untranslated: {missing[:5]}"


def test_a_translation_never_loses_or_invents_a_placeholder():
    """`t(...)` formats with keyword arguments, so a dropped `{n}` is a KeyError shown to
    the player and an invented one is a KeyError every time that line is drawn."""
    for key, values in STRINGS.items():
        expected = set(re.findall(r"{(\w+)}", key))
        for code, text in values.items():
            assert set(re.findall(r"{(\w+)}", text)) == expected, \
                f"{code} changed the placeholders of {key!r}"


def test_every_placeholder_string_actually_formats():
    for key, values in STRINGS.items():
        names = re.findall(r"{(\w+)}", key)
        if not names:
            continue
        arguments = {name: "X" for name in names}
        for code in LOCALES:
            Translator(code)(key, **arguments)


def test_an_unknown_string_falls_back_to_the_english_it_was_keyed_by():
    """The key IS the English text, so a missing entry reads correctly instead of showing
    something like `ui.record.state.idle` to a player."""
    assert Translator("ja")("Not a string in the table") == "Not a string in the table"


def test_every_language_is_named_in_its_own_language():
    """A language list written in English is no use to the person who needs it."""
    assert set(NATIVE_NAMES) == set(LOCALES)
    assert NATIVE_NAMES["zh_tw"] == "繁體中文" and NATIVE_NAMES["ko"] == "한국어"


def test_traditional_and_simplified_never_collapse_into_each_other():
    """They are different vocabularies; mixing them would fail every match."""
    for raw in ("zh_TW", "zh_TW.UTF-8", "zh-Hant", "Chinese (Traditional)_Taiwan",
                "zh_HK", "zh_MO"):
        assert match_locale(raw) == "zh_tw", raw
    for raw in ("zh_CN", "zh-Hans", "Chinese (Simplified)_China"):
        assert match_locale(raw) == "zh_cn", raw
    assert match_locale("") == FALLBACK
    assert match_locale("pt_BR") == FALLBACK


def test_the_sharing_offer_does_not_call_itself_research():
    """What is offered is that a player's drop records join everyone else's to work out a
    dungeon's rates. Calling it research makes an ordinary, optional thing sound like being
    enrolled in something."""
    label = "Share my drop records"
    assert label in STRINGS
    for code, text in STRINGS[label].items():
        for word in ("研究", "調査", "연구", "Studie", "study"):
            assert word not in text, f"{code}: {text}"


# -- console noise -----------------------------------------------------------------

def test_only_the_one_known_qt_message_is_filtered():
    """It is dropped because it could not be placed, not because warnings are unwelcome. A
    filter that matched loosely would hide the next real one."""
    from wddrop_client import theme

    assert theme.is_benign(
        "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0")
    for real in (
        "QLayout: Attempting to add QLayout to a widget that already has one",
        "QObject::connect: No such signal",
        "QPixmap::scaled: Pixmap is a null pixmap",
        "QFont::setPixelSize: Pixel size <= 0 (0)",
    ):
        assert not theme.is_benign(real), real


def test_every_string_the_window_shows_has_a_translation():
    """`t()` falls back to the English it was keyed by, which is the right behaviour at run
    time and a silent hole at development time: a new sentence simply appears in English for
    every player who did not choose it. Three did, in one sitting — the whole point of the
    window speaking six languages is that it speaks them consistently.
    """
    import ast

    source = (ROOT / "client" / "wddrop_client" / "ui.py").read_text(encoding="utf-8")
    missing = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name != "t":
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value not in STRINGS:
                missing.append(f"ui.py:{node.lineno}  {first.value[:70]}")
    assert not missing, "untranslated:\n  " + "\n  ".join(missing)


def _backslashes_in_f_string_expressions(source: str, where: str) -> list[str]:
    """Every `{...}` inside an f-string whose text contains a backslash.

    Legal from 3.12, a SyntaxError before it. Checked by reading the source segment of each
    formatted value rather than by re-parsing, because `ast.parse(feature_version=(3, 11))`
    does NOT restore the old rule — a guard written that way passes on the very file that
    broke the build, which is how this one was first written.
    """
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr):
            continue
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            segment = ast.get_source_segment(source, part.value) or ""
            if "\\" in segment:
                found.append(f"{where}:{part.value.lineno}  {segment[:60]}")
    return found


def test_no_f_string_expression_carries_a_backslash():
    """`requires-python = ">=3.11"`, and the build has to agree with it.

    Caught the hard way. An escaped apostrophe inside an f-string parsed fine on the machine
    it was written on, and the Windows build — which picked up a 3.9 interpreter — dropped
    `wddrop_client.ui` from the bundle over it. PyInstaller reports that as one line of
    `invalid module` among hundreds, and the exe it produces looks finished right until it is
    run, where it is a traceback before the window.
    """
    # The detector first, on the line that actually broke: a check that cannot fail is worse
    # than no check, because it is read as evidence.
    sample = 'x = f"{t(\'the game\\\'s own typeface\')}"'
    assert _backslashes_in_f_string_expressions(sample, "sample"), "the detector is asleep"

    broken = []
    for path in sorted((ROOT / "client" / "wddrop_client").rglob("*.py")):
        broken += _backslashes_in_f_string_expressions(
            path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    assert not broken, "f-string expressions with a backslash:\n  " + "\n  ".join(broken)

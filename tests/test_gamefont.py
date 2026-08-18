"""
Reading the game's own typeface out of the player's installation.

Nothing here is decrypted or worked around: `resources.assets` is plain Unity data beside the
executable and the fonts are ordinary objects inside it. That is what lets this live in the
open — and it is why the guide asks a player to set the game to Japanese, because the face in
that file is the one the game draws Japanese with. The Traditional Chinese face is in the
encrypted bundles, which this deliberately does not touch.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import pytest  # noqa: E402

import paths  # noqa: E402
from wddrop_client import gamefont  # noqa: E402

GAME = paths._path("WDDROP_TEST_GAME")
needs_game = pytest.mark.skipif(GAME is None, reason="set WDDROP_TEST_GAME to the install")


def test_a_machine_without_the_game_is_an_ordinary_outcome(tmp_path):
    """A player may replay someone else's frames on a machine that never had it installed.
    The client then asks them for a font — it must not fail, and must not half-answer."""
    assert gamefont.game_data_dir(tmp_path) is None
    assert gamefont.extract_fonts(tmp_path / "out", tmp_path) == []
    assert gamefont.game_font(tmp_path) is None


@needs_game
def test_the_faces_come_out_of_the_players_own_install(tmp_path):
    fonts = gamefont.extract_fonts(tmp_path, GAME)

    assert [p.name for p in fonts] == ["BaseFont.ttf", "ScenarioFont.ttf"], \
        "the order is the fallback order, and it matters"
    for font in fonts:
        assert font.stat().st_size > gamefont.MIN_FONT_BYTES
    # A real face, not a numeric display font: it has to be loadable and cover the digits a
    # quantity is written with.
    from fontTools.ttLib import TTFont

    cmap = set()
    for table in TTFont(str(fonts[0]), fontNumber=0, lazy=True)["cmap"].tables:
        cmap |= set(table.cmap)
    for ch in "0123456789×！":
        assert ord(ch) in cmap, ch


@needs_game
def test_it_covers_the_japanese_vocabulary_completely(tmp_path):
    """The reason the guide asks for Japanese. Every character of every name, or the names
    containing what is missing could never be recognised at all."""
    import json

    vocab_path = ROOT / "data" / "vocab.ja.json"
    if not vocab_path.exists():
        pytest.skip("ja vocabulary not built")
    from fontTools.ttLib import TTFont

    fonts = gamefont.extract_fonts(tmp_path, GAME)
    cmap = set()
    for font in fonts:
        for table in TTFont(str(font), fontNumber=0, lazy=True)["cmap"].tables:
            cmap |= set(table.cmap)

    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    names = [i["name"] for i in vocab["items"]] + [e["name"] for e in vocab.get("equipment", [])]
    missing = {c for name in names for c in name if ord(c) not in cmap}
    assert not missing, f"{len(missing)} characters the game's own font cannot draw: {missing}"


@needs_game
def test_the_font_is_copied_out_once_and_reused(tmp_path, monkeypatch):
    """So the atlas can be rebuilt with the game uninstalled, and nothing reads from Program
    Files while a session is running."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    first = gamefont.game_fonts(GAME)
    assert first and all(p.exists() for p in first)

    # Second time it must not need the install at all.
    again = gamefont.game_fonts(None)
    assert [p.name for p in again] == [p.name for p in first]

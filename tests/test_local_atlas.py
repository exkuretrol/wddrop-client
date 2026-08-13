"""
Building the atlas on the player's machine.

The recogniser compares rendered candidates against the screen, so it needs the typeface the
game draws with — and that typeface is licensed, so the client does not carry it. It builds
one instead, from a font already on the machine, which means nothing font-derived is ever
distributed.

Nothing here knows anything about the game: a font file goes in, a sheet comes out. Where
the font came from is the caller's problem, and that separation is the point — it is what
lets this half live in the open.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

pytest.importorskip("PIL.Image", reason="pillow not installed")

from wddrop_client.atlas import build, charset_for  # noqa: E402

# Any font with the characters the test names. A Windows install has these; a machine
# without them skips, because the point is a font that is ALREADY THERE.
CANDIDATES = [Path("/mnt/c/Windows/Fonts/mingliu.ttc"), Path("/mnt/c/Windows/Fonts/msjh.ttc"),
              Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc")]
FONT = next((p for p in CANDIDATES if p.exists()), None)

VOCAB = {"locale": "zh_tw", "equipment": [],
         "items": [{"name": "蒼藍礦石"}, {"name": "100拜恩紙幣"}],
         "templates": {"drop_item": "獲得了{0}！！"}}


def test_the_charset_is_everything_a_name_could_need():
    """A character missing from the atlas is a name that can never match, so the set is
    taken from the vocabulary itself rather than from a guess at the script."""
    chars = charset_for(VOCAB)
    for needed in "蒼藍礦石100拜恩紙幣獲得了！×0123456789":
        assert needed in chars, needed
    # Whitespace is kept: it renders blank but still advances, and dropping it would shift
    # every glyph after it in a name that contains one.
    assert " " in chars


@pytest.mark.skipif(FONT is None, reason="no font on this machine to build from")
def test_an_atlas_can_be_built_from_a_font_that_is_already_here(tmp_path):
    """The whole point: no game file, no licensed font travelling anywhere."""
    result = build(FONT, VOCAB, tmp_path, "zh_tw")

    assert result["png"].exists() and result["meta"].exists()
    meta = json.loads(result["meta"].read_text(encoding="utf-8"))
    assert meta["glyphs"] == len(charset_for(VOCAB))
    assert meta["fonts"] == [FONT.name]
    # Every character is placed and carries the advance of the font that DREW it.
    for ch in "蒼藍礦石":
        assert ch in meta["index"] and meta["index"][ch]["advance"] > 0


@pytest.mark.skipif(FONT is None, reason="no font on this machine to build from")
def test_the_atlas_it_writes_is_one_the_recogniser_can_use(tmp_path):
    """Built and then USED, because "the file exists" is not the claim being made."""
    from wddrop_client.capture.glyph import ink_bbox, make_renderer

    build(FONT, VOCAB, tmp_path, "zh_tw")
    renderer = make_renderer(str(tmp_path / "atlas.zh_tw.json"), 26, (600, 44))
    box = ink_bbox(renderer.render("獲得了蒼藍礦石"))
    assert box is not None and box[2] - box[0] > 100, "it rendered nothing legible"


@pytest.mark.skipif(FONT is None, reason="no font on this machine to build from")
def test_characters_no_font_can_draw_are_reported_not_dropped(tmp_path):
    """Silently dropping one shifts every glyph after it in that name, so a name that can
    never match must be visible as such rather than as unexplained failures later."""
    exotic = {"locale": "zh_tw", "equipment": [], "templates": {},
              "items": [{"name": "\U0002A6B2\U0002A6B1"}]}   # far-plane CJK: rarely present
    result = build(FONT, exotic, tmp_path, "zh_tw")
    meta = json.loads(result["meta"].read_text(encoding="utf-8"))
    assert set(result["unresolved"]) == set(meta["unresolved"])
    assert all(ch in meta["index"] for ch in "\U0002A6B2\U0002A6B1"), "a character vanished"

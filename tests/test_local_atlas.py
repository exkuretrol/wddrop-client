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
from pathlib import Path


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


# -- an atlas older than the item table it is about to read ---------------------------

def test_an_atlas_missing_one_character_is_reported(tmp_path):
    """The whole defect in one assertion.

    The sheet is built once, on a fresh install; before this, the only things that rebuilt it
    were the file being absent and the scenario sheet being absent. So a client update
    carrying a NEW item table kept the old sheet, and every name needing a character it
    lacked became unmatchable — not misread, which would show up as a wrong name, but drawn
    with a hole, refused on margin, and simply absent from a record that still looks
    complete.

    Measured on the table that prompted this, ja 1.34.5 -> 1.35.0: one new character, 夕, and
    one name that cannot be read without it — 夕凪の女傑の印, which was half the reason that
    release existed.
    """
    import json

    from wddrop_client.atlas import uncovered

    atlas = tmp_path / "atlas.json"
    atlas.write_text(json.dumps({"index": {c: {} for c in charset_for(VOCAB)}}),
                     encoding="utf-8")
    assert uncovered(VOCAB, atlas) == set()

    # The real table added one character to a vocabulary that already had the rest; this
    # fixture is small, so the same name arrives needing six. What is being asserted is the
    # same thing either way: every character the name needs and the sheet lacks, and no more.
    grown = {**VOCAB, "items": VOCAB["items"] + [{"name": "夕凪の女傑の印"}]}
    assert uncovered(grown, atlas) == set("夕凪の女傑印")


def test_a_sheet_carrying_MORE_than_the_table_asks_for_is_not_stale(tmp_path):
    """A subset test, never equality — and this is the case that makes the difference.

    A rebuild needs the one thing a player may no longer have: the game installed. An atlas
    built from a wider table (or from a locale's fuller vocabulary) draws every name this one
    asks for, so treating "different" as "stale" would spend that on nothing, and would fail
    on exactly the machines that cannot afford it.
    """
    import json

    from wddrop_client.atlas import uncovered

    atlas = tmp_path / "atlas.json"
    wider = charset_for(VOCAB) | set("夕凪の女傑印北穿幽霊城")
    atlas.write_text(json.dumps({"index": {c: {} for c in wider}}), encoding="utf-8")
    assert uncovered(VOCAB, atlas) == set()


def test_an_unreadable_sheet_is_not_reported_as_a_coverage_problem(tmp_path):
    """Missing or corrupt is already "no atlas" to the caller, and it rebuilds for that
    reason. Answering "these characters are missing" would send it down a path whose whole
    premise is that there is a sheet to compare against."""
    from wddrop_client.atlas import uncovered

    assert uncovered(VOCAB, tmp_path / "nothing.json") == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert uncovered(VOCAB, bad) == set()


def test_a_character_the_sheet_lacks_is_said_out_loud_once(tmp_path, caplog):
    """`missing` was written down and read by nothing, so a hole in the sheet left no trace.

    ONCE per character, and that bound is the whole reason this can be logged at all:
    `_glyph` runs per character of every candidate and building an index renders thousands
    of them, so a line that could repeat would bury the trace it sits in. The cache is what
    makes the branch unreachable a second time — this asserts the cache, not the wording.
    """
    import logging

    from wddrop_client.capture.glyph import AtlasRenderer

    if FONT is None:
        pytest.skip("no font with the characters this needs")
    build(FONT, VOCAB, tmp_path, "zh_tw")
    renderer = AtlasRenderer(str(tmp_path / "atlas.zh_tw.json"), 22, (400, 40))

    unknown = chr(1) + chr(2) + chr(1)
    with caplog.at_level(logging.DEBUG, logger="wddrop.glyph"):
        renderer.render(unknown)
        renderer.render(unknown)

    said = [r for r in caplog.records if "cannot draw" in r.getMessage()]
    assert len(said) == 2, "one line per character, however often it is rendered"
    assert renderer.missing == {chr(1), chr(2)}

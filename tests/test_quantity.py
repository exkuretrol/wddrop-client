"""
Reading the 「× N」 on a line whose name is already known.

This had no tests until 2026-08-10, and three separate attempts at improving it each looked
right on one line and silently broke another. The cost of a wrong reading is not a failed
run: `quantity` goes into the study as a measurement, so a fabricated number is
indistinguishable from a real one afterwards. Everything here is therefore about the two
failures that produce a NUMBER rather than an error:

  * a line that shows no quantity at all (equipment, boosted drops) must stay unknown;
  * an ambiguous digit must stay unknown rather than become the best guess.

The synthetic tests render with the game's own font and read back, so they run anywhere the
fonts are extracted. The cross-font tests render with the game's font and read with a
DIFFERENT one, which is the situation on a machine that has no licence to the game's -- and
the reason the reader was rewritten to anchor on the separator.
"""
from __future__ import annotations

import pathlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

pytest.importorskip("numpy", reason="numpy not installed")
pytest.importorskip("PIL.Image", reason="pillow not installed")

from wddrop_client.capture.glyph import (  # noqa: E402
    QTY_MIN_MARGIN, SEPARATOR_MIN_SCORE, WINDOW, make_renderer, recognize_quantity,
    required_window,
)

PREFIX, SUFFIX, SEP = "獲得了", "！！", "×"
NAME = "初始的冥刻雜物"
NAMES = ["蒼藍礦石", NAME, "北穿幽靈城的妖異乳白色雜物"]


def _game_fonts() -> list[str]:
    root = paths.FONTS
    if root is None or not root.is_dir():
        return []
    versions = sorted(d for d in root.iterdir() if d.is_dir())
    return sorted(str(p) for p in versions[-1].glob("*/Font/*ChineseTraditional.ttf")) \
        if versions else []


FONTS = _game_fonts()
pytestmark = pytest.mark.skipif(not FONTS, reason="game fonts not extracted")


@pytest.fixture(scope="module")
def canvas():
    """Sized from the longest name, as the runner does. The default WINDOW is 380px and the
    longest name here renders past it, which clips the 「！！」 the reader anchors on — the
    reader then reports unknown, correctly, for a line it was never shown all of."""
    # Deliberately huge, as the client's own callers do: ink_width can only measure what
    # fits on the renderer it is given, so a small probe silently under-sizes the window.
    probe = make_renderer(FONTS[0], 26, (1600, 80))
    return required_window(probe, PREFIX, NAMES)


@pytest.fixture(scope="module")
def game(canvas):
    return make_renderer(FONTS[0], 26, canvas)


def line(renderer, name: str = NAME, quantity: int | None = None):
    text = f"{PREFIX}{name}{SUFFIX}" if quantity is None else \
           f"{PREFIX}{name}{SEP}{quantity}{SUFFIX}"
    return renderer.render(text)


def read(window, renderer, name: str = NAME):
    return recognize_quantity(window, renderer, PREFIX, name, SUFFIX, separator=SEP)


# -- the number that is there ---------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 5, 6, 9, 10, 12, 47, 62, 99, 100, 600, 999,
                                   1000, 4321, 12345, 99999])
def test_every_quantity_reads_back(game, n):
    """Recordings only ever showed 1, 2 and 3. A reader that cannot count past three passes
    the whole archive and then fails the first time a chest pays out six of something."""
    assert read(line(game, quantity=n), game)[0] == n


def test_a_quantity_over_the_cap_is_unknown_rather_than_wrong(game):
    """MAX_QUANTITY is a plausibility cap, not a search bound — the reader itself has none.
    Over it the line must come back unknown: a gap in the data is recoverable, a wrong number
    entered as a measurement is not. Raise the cap the day a real line pays more."""
    from wddrop_client.capture.glyph import MAX_QUANTITY

    assert MAX_QUANTITY == 99_999
    assert read(line(game, quantity=MAX_QUANTITY), game)[0] == MAX_QUANTITY
    # One digit past the cap is also one digit past MAX_DIGITS: the two are aligned, so a
    # number too long to be read and a number too large to be believed are the same refusal.
    assert read(line(game, quantity=MAX_QUANTITY + 1), game)[0] is None


def test_the_quantity_that_prompted_the_cap_reads(game):
    """600 Gil out of one chest, recorded as unknown because 99 was the ceiling."""
    assert read(line(game, quantity=600), game)[0] == 600


def test_a_five_digit_payout_reads(game):
    """A player reports single lines paying five figures. Too low a cap turns a real payout
    into `qty_unknown`, which is indistinguishable from a reading failure afterwards."""
    for n in (10_000, 45_678, 99_999):
        assert read(line(game, quantity=n), game)[0] == n


def test_the_quantity_does_not_depend_on_the_name(game):
    """The failure this reader replaced: the whole line was rendered and correlated, so the
    NAME's width voted on the number and a longer name changed the answer."""
    for name in NAMES:
        assert read(line(game, name, 3), game, name)[0] == 3


# -- the number that is NOT there -----------------------------------------------------

def test_a_line_with_no_quantity_stays_unknown(game):
    """Equipment and boosted lines carry no number. Reading one here is the worst failure
    available: it enters the study as a measurement and nothing downstream can detect it."""
    assert read(line(game, quantity=None), game)[0] is None


def test_a_line_with_no_quantity_is_unknown_for_every_name_length(game):
    """The old reader picked the candidate whose total width fit best, so a name that
    happened to be a few pixels short acquired a digit to make up the difference."""
    for name in NAMES:
        assert read(line(game, name, None), game, name)[0] is None


def test_an_ambiguous_digit_is_unknown_rather_than_the_best_guess(game):
    """With no margin gate a substitute font returned 18 wrong numbers over 1..99 instead of
    23 unknowns. Unknown is a gap in the data; wrong is a false measurement."""
    window = line(game, quantity=2)
    quantity, margin = read(window, game)
    assert quantity == 2 and margin >= QTY_MIN_MARGIN
    # Same read, with the gate raised above the observed margin: the answer must disappear
    # rather than survive as a guess.
    import wddrop_client.capture.glyph as glyph

    original = glyph.QTY_MIN_MARGIN
    glyph.QTY_MIN_MARGIN = margin + 0.01
    try:
        assert read(window, game)[0] is None
    finally:
        glyph.QTY_MIN_MARGIN = original


def test_an_empty_window_is_unknown_not_one(game):
    import numpy as np

    assert read(np.zeros_like(line(game, quantity=1)), game)[0] is None


# -- reading with a font that is not the game's ---------------------------------------

WINDOWS_FONT = pathlib.Path("/mnt/c/Windows/Fonts/mingliu.ttc")


@pytest.fixture(scope="module")
def substitute(canvas):
    """Same canvas as the game font: in production the window is cut from the frame at the
    size the profile fitted, so a drawer and a reader never disagree about its height."""
    if not WINDOWS_FONT.exists():
        pytest.skip("no substitute font available")
    # 24px at +2.0 letter spacing is what fitting this face against a real frame produced.
    return make_renderer(str(WINDOWS_FONT), 24, canvas, 2.0)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 6, 7, 8, 9])
def test_a_substitute_font_reads_single_digits(game, substitute, n):
    """Why the reader was rewritten: the licence to the game's own font does not extend to
    shipping a rasterisation of it, so a player's own font has to be able to read the number.
    Whole-line matching made every ×2 into ×62; anchoring on the separator does not."""
    assert read(line(game, quantity=n), substitute)[0] == n


def test_a_substitute_font_never_invents_a_quantity(game, substitute):
    """The gate that matters most for a font that is not the game's: it may fail to read a
    number, but it may not produce one where the line has none."""
    assert read(line(game, quantity=None), substitute)[0] is None


def test_a_substitute_font_is_wrong_or_unknown_but_rarely_wrong(game, substitute):
    """Documents the measured standing of a substitute font, so a change that quietly trades
    unknowns for wrong answers fails here. Measured 2026-08-10: 75 right, 1 wrong, 23 unknown
    over 1..99."""
    wrong = unknown = 0
    for n in range(1, 100):
        got = read(line(game, quantity=n), substitute)[0]
        if got is None:
            unknown += 1
        elif got != n:
            wrong += 1
    assert wrong <= 2, f"{wrong} wrong readings -- a wrong number is worse than no number"
    assert unknown <= 30, f"{unknown} unknown -- the reader has stopped reading"


def test_the_separator_floor_sits_between_present_and_absent(game):
    """The floor decides when a number can be fabricated at all, so it is asserted rather
    than left as a tuned constant: 0.60 sits between a real 「×」 (0.92-0.96 for the game's
    font, 0.81-0.85 for PMingLiU) and the tail of a glyph mistaken for one (0.37-0.48)."""
    assert 0.50 < SEPARATOR_MIN_SCORE < 0.80

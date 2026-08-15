"""
Reading the NAME out of a line that is mostly not the name.

A drop line is the item's name wrapped in the locale's own sentence, and the recogniser only
ever renders the name (plus whatever the template puts BEFORE it). Everything after — the
「×3」 and the words — is ink no candidate covers, and it drags every score down in proportion
to how much of the line it is. That share is what changes between locales:

    zh_tw   獲得了{0}！！              two characters of tail
    ja      {0}を手に入れた!!          seven, on names that are often seven

which is why a client that read Chinese perfectly recorded NOTHING at all in Japanese: the
right name was still ranked first, at 0.543, under a 0.60 gate.

Four separate things had to be true for a Japanese session to read, and each is a test here:
the line must be anchored where the text actually starts, the invariant tail must be masked
out of the comparison, the tie-break between one-character rivals must be allowed to search
for its own alignment, and a line whose glyphs do not advance the way the atlas says they do
must get a second look at a wider one.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

pytest.importorskip("numpy", reason="numpy not installed")
pytest.importorskip("PIL.Image", reason="pillow not installed")

import numpy as np  # noqa: E402

from wddrop_client.capture.glyph import (  # noqa: E402
    ANCHOR_RECOVER_MAX, INK_LEVEL, MIN_NAME_PX, REFIT_RADIUS, RenderRecognizer, _text_left,
    anchor_window, break_tie, centred_shifts, ink_bbox, make_renderer, mask_after_name,
)


def _game_fonts() -> list[str]:
    root = paths.FONTS
    if root is None or not root.is_dir():
        return []
    versions = sorted(d for d in root.iterdir() if d.is_dir())
    return sorted(str(p) for p in versions[-1].glob("*/Font/*ChineseTraditional.ttf")) \
        if versions else []


FONTS = _game_fonts()
pytestmark = pytest.mark.skipif(not FONTS, reason="game fonts not extracted")

# A name-first locale, written with the characters this font has. The point is the SHAPE of
# the template — name, then a long invariant tail — not which language it is.
SUFFIX = "を手に入れた"
SEP = "×"
NAME = "初始的冥刻雜物"
NAMES = [NAME, "蒼藍礦石", "北穿幽靈城的妖異乳白色雜物", "初始的乳白色雜物"]
WINDOW = (720, 46)


@pytest.fixture(scope="module")
def renderer():
    return make_renderer(FONTS[0], 26, WINDOW)


def frame(renderer, text: str, at: int = 20):
    """One rendered line, placed in a full-width strip the way a screen would show it."""
    drawn = renderer.render(text)
    out = np.zeros((drawn.shape[0], drawn.shape[1] + at))
    out[:, at:] = drawn
    return out


# -- masking the invariant tail -------------------------------------------------------

def test_the_name_scores_far_better_once_the_tail_is_masked(renderer):
    """The measurement this exists for. Same frame, same vocabulary, same gate."""
    observed = renderer.render(f"{NAME}{SEP}3{SUFFIX}!!")
    index = RenderRecognizer(renderer, "", NAMES, min_score=0.0, min_margin=0.0)

    whole = index.recognize(observed)
    named, cut = mask_after_name(observed, renderer, f"{SUFFIX}!!", SEP)
    masked = index.recognize(named)

    assert whole.best == masked.best == NAME, "the ranking was never the problem"
    assert cut is not None
    assert masked.score > whole.score + 0.15, (
        f"masking gained only {masked.score - whole.score:.3f} "
        f"({whole.score:.3f} -> {masked.score:.3f})")


def test_masking_cuts_at_the_separator_not_at_the_words(renderer):
    """The number is read separately, by a reader that anchors on this same 「×」. Leaving it
    in costs what the words cost."""
    observed = renderer.render(f"{NAME}{SEP}12{SUFFIX}!!")
    _named, cut = mask_after_name(observed, renderer, f"{SUFFIX}!!", SEP)
    name_only = renderer.render(NAME)
    lit = np.where((name_only > INK_LEVEL).sum(axis=0) > 0)[0]
    assert cut is not None
    assert lit.max() <= cut <= lit.max() + 3 * renderer.size, (
        f"cut at {cut}, but the name's own ink ends at {lit.max()}")


def test_a_line_with_no_quantity_is_cut_at_the_words(renderer):
    """Equipment and boosted lines carry no number at all, and must still be masked."""
    observed = renderer.render(f"{NAME}{SUFFIX}!!")
    named, cut = mask_after_name(observed, renderer, f"{SUFFIX}!!", SEP)
    assert cut is not None
    assert not (named[:, cut:] > 0).any()
    index = RenderRecognizer(renderer, "", NAMES, min_score=0.0, min_margin=0.0)
    assert index.recognize(named).best == NAME


@pytest.mark.parametrize("name", ["モニヨン銀貨", "乂乂乂", NAME])
def test_a_quantity_less_line_is_never_cut_inside_the_name(renderer, name):
    """The separator is looked for across the whole name, and on a line that HAS no quantity
    the best of a hundred positions still wins something. Measured with the game's own font,
    against a rendered 「×」:

        a real separator          0.97 - 1.00
        the best false one        0.50 - 0.71   <- over the old 0.60 gate

    「モニヨン銀貨を手に入れた!!」 was cut at 87px — mid-name — and read as nothing at all. It
    silently cost whole drops: one archived session gained a fourth chest when this was
    fixed, a 「ランペール金貨」 that had simply never been recorded.

    A name spelled with 乂 is the same hazard made obvious; nothing about either item is
    unusual beyond which characters it happens to use.
    """
    ink = ink_bbox(renderer.render(name))
    observed = renderer.render(f"{name}{SUFFIX}!!")
    named, cut = mask_after_name(observed, renderer, f"{SUFFIX}!!", SEP)
    assert cut is not None
    assert cut >= ink[2], f"cut at {cut}, inside a name whose ink runs to {ink[2]}"
    assert not (named[:, cut:] > 0).any()


def test_a_tail_that_is_not_there_leaves_the_window_alone(renderer):
    """A locale, a font or a frame this does not work on must be no worse off than before it
    existed — never a window with its name masked away."""
    observed = renderer.render(f"{NAME}{SEP}3")
    named, cut = mask_after_name(observed, renderer, "這串字不在畫面上", SEP)
    assert cut is None
    assert named is observed


def test_the_tail_is_never_matched_on_top_of_the_name(renderer):
    """A confident match at column zero would blank the whole line, and every reading after
    it would be of nothing at all."""
    observed = renderer.render(SUFFIX)          # the tail, and NOTHING else
    _named, cut = mask_after_name(observed, renderer, SUFFIX, SEP)
    assert cut is None or cut >= MIN_NAME_PX


def test_an_empty_tail_is_not_a_reason_to_mask(renderer):
    """A template that ends at the name has nothing to find, and must not guess."""
    observed = renderer.render(NAME)
    named, cut = mask_after_name(observed, renderer, "", SEP)
    assert cut is None and named is observed


# -- anchoring ------------------------------------------------------------------------

def test_a_thin_leading_stroke_does_not_move_the_anchor(renderer):
    """A column needs two lit pixels to count as text, which is what keeps one stray pixel
    from anchoring the window. But the glyph a line STARTS with may open thinner than that —
    「上」 opens with a tick lighting one pixel per column — and the anchor then lands inside
    the character, where every rendered candidate is compared against a line shifted out from
    under it."""
    band = np.zeros((20, 200))
    band[9, 40:49] = 255.0                      # a thin opening stroke: one pixel per column
    band[4:16, 49:70] = 255.0                   # ...and the body of the glyph after it
    assert _text_left(band, 49) == 40


def test_an_isolated_pixel_still_does_not_move_the_anchor(renderer):
    """The recovery walks over ink that CONTINUES; a stray pixel has empty columns beside it,
    which is exactly what separates the two cases."""
    band = np.zeros((20, 200))
    band[3, 5] = 255.0                          # scene bleed, far from the text
    band[4:16, 60:90] = 255.0
    assert _text_left(band, 60) == 60


def test_the_recovery_cannot_walk_off_to_the_left_edge(renderer):
    """A lit background would otherwise take the anchor with it."""
    band = np.full((20, 400), 255.0)
    assert _text_left(band, 300) == 300 - ANCHOR_RECOVER_MAX


def test_a_fixed_origin_is_still_honoured(renderer):
    """The message band has one measured at calibration, and it beats anything derived from
    whatever happens to be lit on that row."""
    band = np.zeros((30, 400))
    band[10:20, 100:200] = 255.0
    window = anchor_window(band, (0, 30), (120, 30), x0_fixed=180)
    assert (window[:, :20] > 0).any() and not (window[:, 40:] > 0).any()


# -- breaking a one-character tie -----------------------------------------------------

def test_the_tie_break_finds_its_own_alignment(renderer):
    """下級/中級/上級 differ by one character in an otherwise identical line, so the whole-line
    margin is hair's-breadth and the caller falls back to comparing just the columns that
    differ. Those are thirty columns around one character: a whole line absorbs a pixel of
    misalignment almost for free, and this does not. Fixed at (0,0) it scored the right
    answer at 0.14 and the row went unread.

    It is worse than a low score, too: over so few columns a misaligned 上 can look more like
    a 下 than the 上 that is actually there, so a fixed alignment does not merely fail to
    prove the right answer — it picks the wrong one.
    """
    top, rival = "上級鐵礦石", "下級鐵礦石"          # a real one-character family
    observed = np.roll(np.roll(renderer.render(top), 1, 0), 2, 1)   # a pixel or two out

    fixed = break_tie(observed, renderer, "", top, rival, shifts=(range(0, 1), range(0, 1)))
    searched = break_tie(observed, renderer, "", top, rival)

    assert searched[0] == top and searched[2] > 0.8, "the aligned answer is the right one"
    assert fixed[0] != top or fixed[2] < searched[2] - 0.3, (
        f"a fixed alignment gave {fixed[0]!r} at {fixed[2]:.3f}, which is not worse than "
        f"{searched[0]!r} at {searched[2]:.3f} — this test is no longer measuring anything")


def test_the_tie_break_still_refuses_two_bad_readings(renderer):
    """Searching for an alignment must not turn noise into confidence: the score it reports
    is what stops a confident choice between two candidates that both fit nothing."""
    noise = np.zeros((WINDOW[1], WINDOW[0]))
    noise[5:20, 30:300] = 90.0
    _winner, _margin, fit = break_tie(noise, renderer, "", NAME, NAMES[3])
    assert fit < 0.6


# -- the message band gets the panel's tie-break -----------------------------------------

FAMILY = ("北穿の幽霊城の四鱗のガラクタ", "北穿の幽霊城の双葉のガラクタ")


def _band_runner(renderer, names):
    """A runner with only what `_recognise` touches, so the real decision path runs."""
    from wddrop_client.calibration import Profile
    from wddrop_client.capture.glyph import RenderRecognizer, centred_shifts
    from wddrop_client.runner import CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner._spacing_renderers = {}
    runner._spacing_votes = {}
    runner.profile = Profile(frame_size=(704, 1241), message_band=(0, 30),
                             font_path=FONTS[0], font_size=26, offset=(0, 0),
                             calibration_score=0.9)
    runner.renderer = renderer
    runner.prefix = ""
    runner.recognizer = RenderRecognizer(renderer, "", list(names),
                                         shifts=centred_shifts((0, 0), 1))
    runner.stats = {"recognised": 0}
    runner.review_queue = None
    runner._quantities = {}
    runner._sources = {}
    runner._confidences = {}
    runner._frame_src = None
    runner._recognised_key = None
    runner._last_text = ""
    runner.fmt = None
    runner.items = None
    return runner


def test_the_band_breaks_a_one_word_family_tie_like_the_panel_does(renderer, monkeypatch):
    """Chest junk comes in families that differ by ONE word — 四鱗 / 双葉 / 冥刻 of the same
    place — so the identical rest of the line drowns the difference and the ambiguity gate
    correctly refuses both. The panel has re-scored over the differing columns since it was
    written; the band never did, and a real chest lost one of its three items to exactly
    that.

    The recogniser is stood in for, with the numbers that real chest produced — 0.7795
    against 0.7604, a margin of 0.0191 under a 0.03 gate — because a tie that fine cannot be
    reproduced from a clean rendering: a rendering matches its own template perfectly. What
    is under test is the WIRING, and `break_tie` still runs on real rendered glyphs.
    """
    from wddrop_client.capture.glyph import RenderMatch
    from wddrop_client.runner import CaptureRunner

    truth, rival = FAMILY
    runner = _band_runner(renderer, FAMILY)

    class Ambiguous:
        """What the full index returned for that line: right answer, refused on margin."""

        def recognize(self, window, observed_ink_width=None):
            return RenderMatch(name=None, best=truth, score=0.7795, margin=0.0191,
                               accepted=False, runner_up=rival, template_width=357)

    runner.recognizer = Ambiguous()
    monkeypatch.setattr(CaptureRunner, "_name_only", lambda self, w, *a, **k: (w, None))
    monkeypatch.setattr(CaptureRunner, "_read_quantity", lambda self, w, name: None)
    monkeypatch.setattr(CaptureRunner, "_as_line", lambda self, name: name)

    assert runner._recognise(renderer.render(truth), now=0.0, key=None) == truth
    assert runner.stats.get("tie_broken") == 1, "the band never consulted the tie-break"


def test_a_wrapped_line_still_names_the_item(renderer):
    """The game WRAPS a long message rather than clipping it: 「…を手に入れ」 on the first line
    and 「た!!」 on a second, below the calibrated band. So the tail the mask looks for may be
    half missing, and for the longest names the NAME itself is cut in two.

    Both still read, and that is the claim worth keeping: every candidate is compared over
    the same fixed window, so a shorter name that happens to be a prefix leaves observed ink
    unmatched and loses. A miss would be a gap; a wrong name would be a measurement.
    """
    from wddrop_client.capture.glyph import RenderRecognizer, ink_bbox

    index = RenderRecognizer(renderer, "", NAMES, min_score=0.0, min_margin=0.0)
    for name in NAMES:
        whole = renderer.render(f"{name}{SUFFIX}!!")
        box = ink_bbox(whole)
        if box is None:
            continue
        # Cut where the game would wrap: partway through, wherever that lands.
        wrapped = whole.copy()
        wrapped[:, box[0] + int((box[2] - box[0]) * 0.75):] = 0.0
        assert index.recognize(wrapped).best == name, f"a wrapped {name} read as something else"


# -- a second look, at a wider alignment -------------------------------------------------

# Names written in digits, and the ones they lose to. 「100拜恩紙幣」 is a real drop that a real
# chest recorded without it.
DIGITS = "100拜恩紙幣"
WITH_DIGITS = [DIGITS, "10,000拜恩紙幣", "莫尼翁銀幣", "朗佩爾金幣", "大巨岩符咒",
               "倫戈南戈翠貝殼幣", "高級治療劑", "聖劍碎片", *NAMES]
# WHAT THE SECOND LOOK IS FOR, NOW THAT THE DIGITS NO LONGER DRIFT.
#
# It was written for 「100拜恩紙幣」: the game's digits are narrower than the atlas drew them,
# so the line drifted left along its own length and no single +-1 offset fitted all of it.
# That is fixed at the source — `letter_spacing` was being added to narrow glyphs it does
# not belong to (AtlasRenderer._spacing_for, and test_glyph asserts the model) — and this
# test said so itself the moment it stopped measuring anything: "the narrow search accepted
# '100拜恩紙幣' at 0.8199".
#
# A cumulative fixture cannot replace it. Full-width drift spreads the error symmetrically,
# so the +-1 search finds a compromise offset and a wider one recovers almost nothing:
# measured, 13 characters at -0.7 give 0.5122 at +-1 and 0.5695 at +-3, both refused.
#
# What a WIDER ALIGNMENT genuinely recovers is a line whose START is further from the
# calibrated offset than +-1 — a window that moved, a different UI scale, jitter past what
# calibration pinned. Measured on this fixture:
#
#     shifted 2px    +-1 0.7678 accepted      +-3 1.0000
#     shifted 3px    +-1 0.4781 REFUSED       +-3 1.0000 accepted
#     shifted 4px    +-1 0.3779 REFUSED       +-3 0.7678 accepted
DRIFTS = "北穿幽靈城的妖異乳白色雜物"
OFFSET_PX = 3


def _drifting(renderer, text: str):
    """The line as a client whose window has moved sees it: right glyphs, wrong origin."""
    import numpy as np

    return np.roll(renderer.render(text), OFFSET_PX, axis=1)


def test_a_line_that_advances_differently_is_found_at_a_wider_alignment(renderer):
    """The measurement this exists for.

    The band searches +-1px around the calibrated offset, which assumes the only error is
    where the line STARTS. A name written in ASCII digits breaks that assumption: the error
    accumulates per character, so by the last glyph the observation sits several pixels left
    of its own template and no single +-1 offset fits the whole line. Measured on the real
    frame this came from, 「100バイン紙幣×2を手に入れた!!」:

        +-1   0.5428   refused, under the 0.60 gate — and top of the ranking all along
        +-3   0.6884   accepted, margin 0.1942

    It was the second of three items in that chest, and it was recorded as two.
    """
    observed = _drifting(renderer, DRIFTS)
    index = RenderRecognizer(renderer, "", WITH_DIGITS, shifts=centred_shifts((0, 0), 1))

    first = index.recognize(observed)
    again = index.refit(observed, first.shortlist,
                        shifts=centred_shifts((0, 0), REFIT_RADIUS))

    assert first.best == DRIFTS and not first.accepted, (
        f"the narrow search accepted {first.best!r} at {first.score:.4f} — this test is no "
        f"longer measuring anything")
    assert again.accepted and again.name == DRIFTS, (
        f"the second look gave {again.best!r} at {again.score:.4f}, margin {again.margin:.4f}")


def test_the_second_look_does_not_lower_the_bar(renderer):
    """A wider alignment is a different measurement, not a weaker gate.

    Every way a reading can be wrong still has to fail: text that is not in the vocabulary at
    all, and ink that is not text. Both are scored again under the SAME two gates, and both
    must still be refused — otherwise this would buy the digits back by admitting everything.
    """
    index = RenderRecognizer(renderer, "", [n for n in WITH_DIGITS if n != DIGITS],
                             shifts=centred_shifts((0, 0), 1))
    absent = renderer.render(DIGITS)                    # a name the index does not have
    noise = np.zeros((WINDOW[1], WINDOW[0]))
    noise[5:20, 30:300] = 90.0                          # ink, but not writing

    for observed, what in ((absent, "a name not in the vocabulary"), (noise, "noise")):
        first = index.recognize(observed)
        again = index.refit(observed, first.shortlist,
                            shifts=centred_shifts((0, 0), REFIT_RADIUS))
        assert not again.accepted, (
            f"{what} was accepted as {again.best!r} at {again.score:.4f}, "
            f"margin {again.margin:.4f}")


def test_the_second_look_only_costs_the_shortlist(renderer):
    """Why it is a second look rather than simply a wider search.

    Widening the search for the WHOLE vocabulary was measured at 35ms -> 228ms per
    recognition against a 50ms frame budget at 20fps, which would drop frames — and a frame
    not sampled is a line that can never be recovered. So the refit is handed a shortlist,
    and must not quietly go back to the full index.
    """
    index = RenderRecognizer(renderer, "", WITH_DIGITS, shifts=centred_shifts((0, 0), 1))
    observed = _drifting(renderer, DIGITS)

    two = index.refit(observed, [DIGITS, "莫尼翁銀幣"],
                      shifts=centred_shifts((0, 0), REFIT_RADIUS))

    assert two.best == DIGITS
    assert two.runner_up == "莫尼翁銀幣", (
        f"the refit ranked {two.runner_up!r} second, which is not in the shortlist it was "
        f"given — it is scoring the whole vocabulary")


def test_the_band_asks_for_a_second_look_before_giving_up(renderer, monkeypatch):
    """The wiring, through the runner's own decision path.

    `refit` fixing the score is worth nothing if `_recognise` never calls it, which is
    exactly how the tie-break spent its first months: implemented, tested, and reached only
    from the mining panel.
    """
    from wddrop_client.runner import CaptureRunner

    runner = _band_runner(renderer, WITH_DIGITS)
    monkeypatch.setattr(CaptureRunner, "_name_only", lambda self, w, *a, **k: (w, None))
    monkeypatch.setattr(CaptureRunner, "_read_quantity", lambda self, w, name: None)
    monkeypatch.setattr(CaptureRunner, "_as_line", lambda self, name: name)

    read = runner._recognise(_drifting(renderer, DRIFTS), now=0.0, key=None)

    assert read == DRIFTS
    assert runner.stats.get("realigned") == 1, "the band never asked for a second look"


def test_an_ambiguous_reading_is_not_offered_a_second_look(renderer, monkeypatch):
    """The failure this recovery can cause, and the rule that stops it.

    The two gates fail for different reasons. A low SCORE says the right name may simply have
    been placed badly, which is what a wider alignment is for. A thin MARGIN says two
    candidates are indistinguishable — and measuring an ambiguous line more carefully does not
    make it less ambiguous, it just lets one of them creep over the bar.

    Measured on a real recording: a 17-pixel speck of dungeon wall, in an episode with no
    message in it at all, was refused as 「箒」 at margin 0.0225 and came back from the second
    look at 0.0316. It was recorded as a chest containing a broom. The tie-break exists for
    genuine ambiguity and looks at the columns the candidates disagree on; this must not
    stand in for it.
    """
    from wddrop_client.capture.glyph import MIN_MARGIN, RenderMatch
    from wddrop_client.runner import CaptureRunner

    runner = _band_runner(renderer, WITH_DIGITS)
    asked = []

    class Ambiguous:
        """Good enough to look like a fit, too close to the runner-up to be one."""

        min_margin = MIN_MARGIN

        def recognize(self, window, observed_ink_width=None):
            return RenderMatch(name=None, best="箒", score=0.6290, margin=0.0225,
                               accepted=False, runner_up="布の靴", template_width=22,
                               shortlist=("箒", "布の靴"))

        def refit(self, window, names, *, shifts):
            asked.append(names)
            return RenderMatch(name="箒", best="箒", score=0.6450, margin=0.0316,
                               accepted=True, runner_up="布の靴", template_width=22)

    runner.recognizer = Ambiguous()
    monkeypatch.setattr(CaptureRunner, "_name_only", lambda self, w, *a, **k: (w, None))
    monkeypatch.setattr(CaptureRunner, "_read_quantity", lambda self, w, name: None)
    monkeypatch.setattr(CaptureRunner, "_as_line", lambda self, name: name)

    read = runner._recognise(np.zeros((WINDOW[1], WINDOW[0])), now=0.0, key=None)

    assert read == "", f"a speck of wall was recorded as {read!r}"
    assert not asked, "an ambiguous reading was sent for a second look anyway"


# -- a name has to fit on one row, and one of them already does not ----------------------

# WHAT CAN COME OUT OF A DUNGEON is decided in `wddrop_client.items`, not here.
#
# It used to be defined in this file, and then the client grew the same rule for a different
# reason — trimming its own candidate index from 3,268 names to 2,154 — and two copies of a
# judgement is the exact shape of mistake this session has now paid for three times. The
# client's is the one that decides what a player's recogniser can read, so the client's is
# the one this test measures.

def test_every_droppable_name_still_fits_on_the_row_the_client_can_see():
    """THE SECOND ROW OF A WRAPPED LINE DOES NOT EXIST FOR THE CLIENT.

    The game wraps a long message rather than clipping it, and live capture grabs strips:
    the message band is read at y999-1020 and captured at y995-1024, while the second row of
    a real wrapped frame starts at y1037. Outside the strip is composited black, so a name
    that crosses the break is not read badly — half of it is not there at all.

    THE FRIGHTENING NUMBER IS THE WRONG NUMBER. 519 of 3,268 LINES are wider than the band,
    but a line is `name + ×N + を手に入れた!!` and it is the TAIL that wraps; the reader masks
    the tail off before matching anyway. Measured on a real frame: 「北穿の幽霊城の妖なる乳白色
    のガラクタを手 / に入れた!!」 read at 0.7282 with half its wording on the row below, and
    the same junk family read its 「×3」 correctly in another session.

    What would cost an item is a NAME that crosses the break. Of everything that can actually
    fall out of a chest, none does — the widest is 539px against 611 usable, about three
    characters of margin. So this is a canary, not a bug report: one longer junk name in a
    game update and a real drop is lost silently, and this fails instead. On the day it
    does, the reader has to learn to read two rows, which is a real change to the comparison
    and is not worth making before something that can drop needs it.
    """
    import json

    import pytest

    from wddrop_client.calibration import ProfileStore
    from wddrop_client.capture.glyph import make_renderer

    vocab = ROOT / "data" / "vocab.ja.json"
    atlas = ROOT / "data" / "atlas.ja.json"
    if not (vocab.exists() and atlas.exists()):
        pytest.skip("the Japanese vocabulary/atlas is not built")
    profile = ProfileStore.shipped("ja").get((704, 1241))
    if profile is None:
        pytest.skip("no shipped fit for 704x1241")

    from wddrop_client.capture.ocr import Vocabulary
    from wddrop_client.items import droppable as droppable_names

    droppable = set(droppable_names(Vocabulary.load(vocab).entries))

    # A canvas far wider than the frame, so this measures rather than clips.
    renderer = make_renderer(str(atlas), profile.font_size, (4000, 60),
                             profile.letter_spacing)
    usable = profile.frame_size[0] - profile.text_x0
    widths = {name: renderer.ink_width(name) for name in droppable}

    too_wide = {n: w for n, w in widths.items() if w > usable}
    assert not too_wide, (
        f"{len(too_wide)} droppable name(s) no longer fit the row the client reads "
        f"({usable}px), so half of each is on a row capture never takes: "
        + ", ".join(f"{n} ({w}px)" for n, w in sorted(too_wide.items(),
                                                      key=lambda kv: -kv[1])[:5]))
    # The margin is the thing worth watching, so it is asserted rather than assumed.
    widest, width = max(widths.items(), key=lambda kv: kv[1])
    assert usable - width >= 40, (
        f"only {usable - width}px of headroom left — {widest} is {width}px of {usable}. "
        f"One more character and a real drop goes unread.")


# -- a line whose wording wrapped onto a row the client cannot see ------------------------

def test_a_wrapped_line_is_cut_at_its_separator():
    """The game wraps rather than clips, and the window is ONE row.

    「北穿の幽霊城の常なる冥刻のガラクタ×3を」 leaves a single character of 「を手に入れた!!」 on
    the row the client reads. Measured on that frame: the full wording scores 0.390 there and
    the 「を」 alone 0.918, so nothing was masked and the 「×3を」 that no candidate covers
    dragged the true name from 0.86 to 0.67 — under every gate. The chest was recorded one
    item short, and the line was in the recording all along.

    The separator carries the cut on its own, but only on evidence that this IS a wrap: an
    unmistakable 「×」 with the wording's first character within a few digits' width after it.
    """
    from wddrop_client.capture.glyph import mask_after_name, make_renderer

    atlas = ROOT / "data" / "atlas.ja.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    import numpy as np

    r = make_renderer(str(atlas), 22, (740, 45), 0.0)
    after = "を手に入れた!!"

    wrapped = r.render("北穿の幽霊城の常なる冥刻のガラクタ×3を")
    _masked, cut = mask_after_name(wrapped, r, after, "×")
    assert cut is not None, "a wrapped line was left with its tail in the comparison"
    name_only = r.render("北穿の幽霊城の常なる冥刻のガラクタ")
    from wddrop_client.capture.glyph import ink_bbox

    assert abs(cut - ink_bbox(name_only)[2]) < 12, "the cut is not at the end of the name"

    # And a name that merely CONTAINS an ×-like stroke is not cut at it: nothing follows the
    # separator that looks like the wording, so there is no wrap to believe in.
    plain = r.render("下級鉄鉱石")
    _m2, cut2 = mask_after_name(plain, r, after, "×")
    assert cut2 is None


def test_the_whole_wording_still_wins_when_it_is_on_the_row():
    """The wrap path is a fallback, not a replacement: an unwrapped line is cut at the words,
    which is the stronger evidence and where the cut has always been."""
    from wddrop_client.capture.glyph import ink_bbox, mask_after_name, make_renderer

    atlas = ROOT / "data" / "atlas.ja.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    r = make_renderer(str(atlas), 22, (740, 45), 0.0)
    line = r.render("下級鉄鉱石×3を手に入れた!!")
    _masked, cut = mask_after_name(line, r, "を手に入れた!!", "×")
    assert cut is not None
    assert abs(cut - ink_bbox(r.render("下級鉄鉱石"))[2]) < 12


def test_a_narrow_digit_cannot_stand_in_for_a_wide_one():
    """A digit's WIDTH is part of its shape, and `_fitted` throws it away: it resizes both
    axes onto the observed box, so a 「1」 stretched to the width of a 「4」 is compared as if
    it had always been that wide.

    At 18px (1600x900) that is enough to win. A real 「4」 8px wide was read as 「1」 at 0.4886
    against the 4's own 0.3847, and 「×14」 was recorded as ×11 — a chest's quantity, in the
    study's own data. Keeping the aspect makes the same comparison 0.7868 against 0.1186.

    `_fitted` stays as the fallback: a substitute face genuinely draws narrower digits (its
    「1」 is 3px where the game's is 6px) and stretching is the only way to read one at all.
    """
    import numpy as np

    from wddrop_client.capture.glyph import (_digit_shapes, _fitted, _fitted_aspect,
                                             ink_bbox, make_renderer, zncc)

    atlas = ROOT / "data" / "atlas.ja.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    renderer = make_renderer(str(atlas), 18, (200, 40), 0.9)
    shapes = dict(_digit_shapes(renderer))

    # Softened, because a CAPTURED digit is: the difference between the two fits only shows
    # on ink that has been through a screen and a PNG. On a pixel-perfect render both
    # separate fine, which is why this was not caught before a chest was recorded wrong.
    from PIL import Image, ImageFilter

    drawn = np.asarray(renderer.render("4"), dtype=float)
    drawn = np.asarray(Image.fromarray(drawn.astype("uint8")).filter(
        ImageFilter.GaussianBlur(0.4)), dtype=float)
    box = ink_bbox(drawn, min_column_ink=1)
    seen = drawn[box[1]:box[3], box[0]:box[2]]

    def best(fit):
        scored = sorted(((zncc(seen, fit(shapes[d], seen)), d) for d in range(10)
                         if fit(shapes[d], seen) is not None), reverse=True)
        return scored[0][1], scored[0][0] - scored[1][0]

    winner, margin = best(_fitted_aspect)
    assert winner == 4, "the digit that is there did not win on shape"
    _stretched, stretched_margin = best(_fitted)
    assert margin > stretched_margin, (
        f"keeping the aspect should separate them better: {margin:.4f} vs "
        f"{stretched_margin:.4f}")

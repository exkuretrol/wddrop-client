"""
Tests for the render-and-compare recogniser.

Synthetic tests always run: they render a line with the game's own font and feed it straight
back, which exercises the real matching path without needing a screen capture.

The recording test needs a frame and the extracted fonts, and skips otherwise. Measured on
that frame against the full 2,558-name vocabulary: calibration picks
the zh_tw face @ 26px offset (0,-1), and recognition returns 初始的冥刻雜物 at score 0.862 /
margin 0.084 in 233 ms.
"""
from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

pytest.importorskip("numpy", reason="numpy not installed")
Image = pytest.importorskip("PIL.Image", reason="pillow not installed")

from wddrop_client.capture.glyph import (  # noqa: E402
    GlyphRenderer, RenderRecognizer, anchor_window, calibrate, centred_shifts, ink_bbox, zncc,
)

def _fonts_for_newest_version() -> list[str]:
    """Fonts from ONE game version only.

    Mixing versions makes calibration ambiguous: the same face ships in several bundles, so
    two files can score identically and the winner is decided by iteration order rather than
    by fit. Pinning to one version keeps the test asserting about fit.
    """
    root = paths.FONTS
    if root is None or not root.is_dir():
        return []
    versions = sorted(d for d in root.iterdir() if d.is_dir())
    if not versions:
        return []
    return sorted(str(p) for p in versions[-1].glob("*/Font/*ChineseTraditional.ttf"))


FONTS = _fonts_for_newest_version()
FRAME = os.environ.get("WDDROP_TEST_FRAME")
PREFIX = "獲得了"
NAMES = ["初始的冥刻雜物", "初始的雙葉雜物", "初始的尋常雜物", "蒼藍礦石", "雪兇鳥羽冠"]

pytestmark = pytest.mark.skipif(not FONTS, reason="game fonts not extracted")


def base_font() -> str:
    return next(f for f in FONTS if "BaseFont" in f)


def test_zncc_is_one_for_identical_and_low_for_unrelated():
    import numpy as np

    r = GlyphRenderer(base_font(), 26)
    a = r.render(PREFIX + "初始的冥刻雜物")
    assert zncc(a, a) == pytest.approx(1.0, abs=1e-9)
    assert zncc(a, r.render(PREFIX + "蒼藍礦石")) < 0.95


def test_rendered_line_recognises_itself_over_the_vocabulary():
    """The core round trip: render a candidate, feed it back, get it out again."""
    renderer = GlyphRenderer(base_font(), 26)
    rec = RenderRecognizer(renderer, PREFIX, NAMES, shifts=(range(0, 1), range(0, 1)))
    for name in NAMES:
        match = rec.recognize(renderer.render(PREFIX + name))
        assert match.accepted, f"{name} not accepted (score {match.score:.3f})"
        assert match.name == name


def test_short_and_long_names_are_distinguished():
    """A fixed window is used precisely so name LENGTH is part of the signal — tight
    ink-cropping loses that and drops the true answer to rank 4-9."""
    renderer = GlyphRenderer(base_font(), 26)
    rec = RenderRecognizer(renderer, PREFIX, NAMES, shifts=(range(0, 1), range(0, 1)))
    short = rec.recognize(renderer.render(PREFIX + "蒼藍礦石"))
    long_ = rec.recognize(renderer.render(PREFIX + "初始的冥刻雜物"))
    assert short.name == "蒼藍礦石"
    assert long_.name == "初始的冥刻雜物"


def test_unknown_text_is_refused_rather_than_forced():
    renderer = GlyphRenderer(base_font(), 26)
    rec = RenderRecognizer(renderer, PREFIX, NAMES, shifts=(range(0, 1), range(0, 1)))
    match = rec.recognize(renderer.render(PREFIX + "完全不存在的東西名稱"))
    assert match.name is None and not match.accepted


def test_margin_gate_rejects_an_undiscriminated_match():
    """Score alone is not enough: a wrong font size still yields a confident-looking 0.43
    with a 0.003 margin. Both gates must be required."""
    renderer = GlyphRenderer(base_font(), 26)
    rec = RenderRecognizer(
        renderer, PREFIX, NAMES,
        min_score=0.0, min_margin=0.99,        # impossible margin
        shifts=(range(0, 1), range(0, 1)),
    )
    assert not rec.recognize(renderer.render(PREFIX + NAMES[0])).accepted


def test_width_prefilter_does_not_change_the_answer():
    renderer = GlyphRenderer(base_font(), 26)
    rec = RenderRecognizer(renderer, PREFIX, NAMES, shifts=(range(0, 1), range(0, 1)))
    img = renderer.render(PREFIX + "初始的冥刻雜物")
    box = ink_bbox(img)
    wide = rec.recognize(img)
    filtered = rec.recognize(img, observed_ink_width=box[2] - box[0])
    assert wide.name == filtered.name == "初始的冥刻雜物"


def test_centred_shifts_brackets_the_offset():
    dx, dy = centred_shifts((3, -1), radius=1)
    assert list(dx) == [2, 3, 4] and list(dy) == [-2, -1, 0]


def test_base_and_scenario_fonts_are_the_same_file_for_this_locale():
    """Documents why calibration is not asserted to pick a font BY NAME: for zh_tw (and
    zh_cn/ko/de/en) BaseFont_<locale>.ttf and ScenarioFont_<locale>.ttf are byte-identical,
    so which name wins is decided by iteration order, not by fit. Only ja differs."""
    import hashlib

    digests = {
        pathlib.Path(f).name: hashlib.md5(pathlib.Path(f).read_bytes()).hexdigest()
        for f in FONTS
    }
    assert len(set(digests.values())) == 1, digests


def test_calibrate_recovers_size_and_offset_from_a_rendered_line():
    """Size and offset are what calibration must actually fit — one pixel of size is the
    difference between rank 1 (margin 0.084) and rank 7 (margin 0.003) on a real frame."""
    truth_size = 26
    window = GlyphRenderer(base_font(), truth_size).render(PREFIX + "初始的冥刻雜物")
    font, size, offset, score, spacing = calibrate(
        window, "初始的冥刻雜物", PREFIX, FONTS, range(22, 30)
    )
    assert size == truth_size
    assert offset == (0, 0)
    assert score > 0.95
    # Whichever name won, it must render identically to the font we drew with.
    assert GlyphRenderer(font, size).render("獲得了初始的冥刻雜物").tolist() == window.tolist()


# -- against a real captured frame ------------------------------------------------
@pytest.mark.skipif(not FRAME, reason="set WDDROP_TEST_FRAME to a captured game frame")
def test_recognises_a_real_screen_frame():
    import json

    band = (995, 1030)
    frame = Image.open(FRAME).convert("L")
    window = anchor_window(frame, band)
    assert window is not None, "no text found in the calibrated band"

    font, size, offset, score, spacing = calibrate(window, "初始的冥刻雜物", PREFIX, FONTS, range(20, 34))
    assert size == 26 and score > 0.8

    md = str(paths.ITEMS or "")
    names = [r["name"] for r in json.load(open(md, encoding="utf-8")) if r.get("name")]
    rec = RenderRecognizer(
        GlyphRenderer(font, size), PREFIX, names, shifts=centred_shifts(offset, 1)
    )
    match = rec.recognize(window)
    assert match.accepted
    assert match.name == "初始的冥刻雜物"
    assert match.margin > 0.03

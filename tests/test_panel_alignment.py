"""Where a panel row's alignment actually falls, and why no fixed rule finds it.

The message band has a calibrated origin — `text_x0`, the pen. A panel row has none:
`anchor_window` takes it from the first lit column and `_text_left` then walks back over
columns too thin to count, so the anchor lands somewhere between the PEN and the first
glyph's INK — and where, depends on how bright the text is at that size.

Measured on the same line, 「下級鉄鉱石×3 を入手した」, at both fitted resolutions:

     704x1241   true alignment dx +2    下級 0.8951, and 上級/中級 0.856/0.851
    1920x1080   true alignment dx -5    下級 0.8929, and 上級/中級 0.863/0.850

「下」 opens with a thin horizontal stroke — about nine pixels of left side bearing — and
「上」「中」 open with almost none, which is why exactly this family is where it shows: at the
wrong alignment the true name loses to two rivals that are not even close.

Sliding the TEMPLATES to their own ink instead was tried, and it is worse than either fixed
rule: it fixes 1920x1080 by construction and breaks 704x1241 by the same construction,
because the two anchors differ by precisely the amount it assumes. Two mining panels in a
704x1241 recording came back as "1 line could not be read" from it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

ATLAS = ROOT / "data" / "atlas.ja.json"
pytestmark = pytest.mark.skipif(not ATLAS.exists(), reason="atlas not built")

FAMILY = ("下級鉄鉱石", "中級鉄鉱石", "上級鉄鉱石")


def _best(observed, template, shifts):
    import numpy as np

    from wddrop_client.capture.glyph import zncc

    dxs, dys = shifts
    return max(zncc(np.roll(np.roll(observed, -dy, 0), -dx, 1), template)
               for dy in dys for dx in dxs)


def _drawn(size: int, dx: int, text: str):
    """The line as the game drew it, at an anchor `dx` away from where a template lands."""
    import numpy as np

    from wddrop_client.capture.glyph import make_renderer

    line = make_renderer(str(ATLAS), size, (520, size + 2), 0.0).render(text)
    return np.roll(line, dx, axis=1)


@pytest.mark.parametrize("size,offset", [(25, 2), (22, -5)])
def test_the_true_name_wins_once_the_search_reaches_its_alignment(size, offset):
    """Both measured cases, as the recordings produced them."""
    from wddrop_client.capture.glyph import make_renderer
    from wddrop_client.runner import PANEL_SHIFTS

    renderer = make_renderer(str(ATLAS), size, (520, size + 2), 0.0)
    observed = _drawn(size, offset, "下級鉄鉱石")
    scored = sorted(((_best(observed, renderer.render(n), PANEL_SHIFTS), n) for n in FAMILY),
                    reverse=True)
    assert scored[0][1] == "下級鉄鉱石", scored
    assert scored[0][0] - scored[1][0] >= 0.03, f"too close to call: {scored}"


def test_both_measured_alignments_are_inside_the_search():
    """The two numbers this was written from, pinned. A synthetic render is cleaner than a
    captured frame — at +-2 it still reads a rolled render correctly — so what a test can
    honestly hold is the RANGE: the anchors these recordings actually produced, dx +2 at
    704x1241 and dx -5 at 1920x1080, must both be reachable."""
    from wddrop_client.runner import PANEL_SHIFTS

    dxs = PANEL_SHIFTS[0]
    assert 2 in dxs and -5 in dxs, f"a measured alignment is outside {dxs}"


def test_the_search_covers_the_widest_bearing_in_the_font():
    """The range is not a guess: it has to reach the anchor's own spread, which is the
    first glyph's bearing. 「下」 is the outlier this was measured on."""
    from wddrop_client.capture.glyph import PAD, ink_bbox, make_renderer
    from wddrop_client.runner import PANEL_SHIFTS

    renderer = make_renderer(str(ATLAS), 25, (520, 27), 0.0)
    bearings = [ink_bbox(renderer.render(n))[0] - PAD for n in
                ("下級鉄鉱石", "中級鉄鉱石", "上級鉄鉱石", "透明な小石", "銀鉱石")]
    dxs = PANEL_SHIFTS[0]
    assert min(dxs) <= -max(bearings), f"the search cannot reach a {max(bearings)}px bearing"

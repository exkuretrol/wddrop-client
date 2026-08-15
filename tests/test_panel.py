"""
The mining result panel.

Chests and mining announce through different UI, which is why a client that read only the
bottom message band saw nothing at all across three sessions of mining:

    chest    「獲得了下級鐵礦石 × 3！！」   one line, bottom band, 26px
    mining   「得到了下級鐵礦石 × 3。」     centred panel, several lines, 25px

That one-pixel size difference is not cosmetic: the same line reads at 0.897 at 25px and
0.46-0.55 at 24 or 26, so the panel's size is derived from the rows on screen rather than
inherited from the calibration.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

pytest.importorskip("numpy")
pytest.importorskip("PIL.Image")

from wddrop_client.capture.panel import (INK_HEIGHT_RATIO, panel_rows,  # noqa: E402
                                         panel_signature, same_text, size_from_rows)

FRAME = (paths.CAPTURES or Path("/nonexistent")) / ("session-20260809-075143/"
             "episode-005/f_00003.png")
needs_frame = pytest.mark.skipif(not FRAME.exists(), reason="mining recording not available")


def load(path):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(path).convert("L"), dtype=float)


def test_size_is_derived_from_the_row_height():
    """21px rows are a 25px font. The panel is NOT the calibrated 26px, and at 26px the same
    line scores 0.46 instead of 0.897 — so inheriting the calibration would read nothing."""
    assert size_from_rows([(591, 612), (622, 643)]) == 25
    assert size_from_rows([]) is None
    # The ratio is measured, not assumed: 21/25 and 22/26 both land on it.
    assert 21 / 25 == pytest.approx(INK_HEIGHT_RATIO, abs=0.005)
    assert 22 / 26 == pytest.approx(INK_HEIGHT_RATIO, abs=0.005)


@needs_frame
def test_the_real_panel_is_found():
    assert panel_rows(load(FRAME)) == [(591, 612), (622, 643)]


@needs_frame
def test_a_walking_frame_has_no_panel():
    """Otherwise every frame would propose rows and pay for a match."""
    walking = FRAME.parent.parent / "episode-004" / "f_00094.png"
    if not walking.exists():
        pytest.skip("frame not available")
    assert panel_rows(load(walking)) == []


@needs_frame
def test_the_signature_ignores_the_scene_behind_the_panel():
    """The panel is drawn over a live, blurred 3D scene, so raw pixels differ every frame
    even when the text is identical — hashing them made a whole session record nothing."""
    import numpy as np

    gray = load(FRAME)
    noisy = np.clip(gray + np.random.default_rng(0).uniform(0, 40, gray.shape), 0, 149)
    assert same_text(panel_signature(gray), panel_signature(np.maximum(gray, noisy)))


@needs_frame
def test_two_panels_differing_only_in_a_digit_are_not_told_apart_by_pixels():
    """Which is WHY a new swing is decided by the panel going away, not by its content: two
    successive yields differed only in 「×6」 against 「×3」 and overlapped by 0.96, so no
    pixel comparison can separate them and deduplicating on content deletes a real yield."""
    vein = paths.capture("session-20260809-091115") or Path("/nonexistent")
    first, second = vein / "episode-003/f_00003.png", vein / "episode-004/f_00003.png"
    if not (first.exists() and second.exists()):
        pytest.skip("the one-vein recording is not available")
    # 下級鐵礦石x3/透明鵝卵石x6 against 下級鐵礦石x3/透明鵝卵石x3 — two real, separate yields.
    assert same_text(panel_signature(load(first)), panel_signature(load(second)))


@needs_frame
def test_the_panel_reads_back_at_the_derived_size():
    """End to end on the real frame, through the same recogniser the runner uses."""
    import re

    from wddrop_client.capture.glyph import RenderRecognizer, anchor_window, make_renderer

    vocab_path = ROOT / "data" / "vocab.zh_tw.json"
    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not (vocab_path.exists() and atlas.exists()):
        pytest.skip("vocabularies/atlases not built")
    import json

    raw = json.loads(vocab_path.read_text(encoding="utf-8"))
    prefix = re.sub(r"<[^>]+>", "", raw["templates"]["get_item"]).split("{0}")[0]
    assert prefix == "得到了", "mining uses 得到了, not the chest's 獲得了"

    names, seen = [], set()
    for e in raw["items"] + raw["equipment"]:
        n = e.get("name")
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    gray = load(FRAME)
    rows = panel_rows(gray)
    size = size_from_rows(rows)
    rec = RenderRecognizer(make_renderer(str(atlas), size, (520, 44), 0.0), prefix, names)
    read = [rec.recognize(anchor_window(gray, r, (520, 44))) for r in rows]
    accepted = [m.name for m in read if m.accepted]
    assert "透明鵝卵石" in accepted, [(m.name, round(m.score, 3)) for m in read]


def test_the_atlas_can_draw_the_mining_verb():
    """「到」 occurs in no zh_tw item name, so it entered the atlas only once Common@GetItem
    was listed as a template. Before that the renderer drew 「得 了…」 with a hole in it and
    scored 0.40 instead of 0.91 — silently, because nothing knew the character was wanted."""
    import json

    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    index = json.loads(atlas.read_text(encoding="utf-8"))["index"]
    for ch in "得到了。":
        assert ch in index, f"{ch!r} missing from the atlas"


def test_live_capture_actually_grabs_the_panel():
    """Everything outside the captured strips is composited BLACK, so a region left out does
    not read poorly — it does not exist. Mining reported in a band nobody grabbed, so it
    worked perfectly on recordings (whole frames) and never once in a live session."""
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, str(ROOT / "packages" / "schema"))
    from wddrop_client.__main__ import _capture_strips
    from wddrop_client.capture.panel import SEARCH_BOTTOM, SEARCH_TOP

    profile = SimpleNamespace(frame_size=(704, 1241), message_band=(1000, 1022),
                              hud_region=(0.88, 0.157, 0.96, 0.193))
    strips = _capture_strips(profile, record=False)

    def covered(y: int) -> bool:
        return any(top <= y < top + height for _l, top, _w, height in strips)

    # The rows of the real mining panel, measured from the recording.
    assert covered(591) and covered(643), strips
    # And the message band is still covered, obviously.
    assert covered(1000) and covered(1021)
    # Recording whole frames needs no strips at all.
    assert _capture_strips(profile, record=True) is None
    # The panel band can be turned off, so this stays a decision rather than an assumption.
    assert not any(int(1241 * SEARCH_TOP) == top for _l, top, _w, _h
                   in _capture_strips(profile, record=False, mining=False))
    assert SEARCH_TOP < 591 / 1241 < SEARCH_BOTTOM


def test_a_one_character_rival_is_separated_by_the_columns_that_differ():
    """Graded families — 下級/中級/上級/特級鐵礦石 — differ by ONE character in an otherwise
    identical line, so whole-line correlation puts the true answer and its rival within 0.02
    and the ambiguity gate correctly refuses both. Measured on a real mining panel:

        whole line   下級鐵礦石 0.8624 vs 上級鐵礦石   margin +0.0213   (gate is 0.03)
        differing columns only                        margin +0.1825

    The fix is more evidence, not a lower threshold: lowering the gate would have admitted
    genuinely ambiguous readings everywhere else in the vocabulary.
    """
    from wddrop_client.capture.glyph import break_tie, discriminating_columns, make_renderer

    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    r = make_renderer(str(atlas), 25, (520, 27), 0.0)

    # Only the one differing character's columns are compared, not the whole line.
    cols = discriminating_columns(r.render("得到了下級鐵礦石"), r.render("得到了上級鐵礦石"))
    assert 0 < len(cols) < 60, f"expected one character's worth of columns, got {len(cols)}"

    observed = r.render("得到了下級鐵礦石")
    winner, margin, _fit = break_tie(observed, r, "得到了", "下級鐵礦石", "上級鐵礦石")
    assert winner == "下級鐵礦石" and margin > 0.1
    # And it must pick the RIVAL when the rival is what is on screen.
    winner, margin, _fit = break_tie(r.render("得到了上級鐵礦石"), r, "得到了",
                               "下級鐵礦石", "上級鐵礦石")
    assert winner == "上級鐵礦石" and margin > 0.1


def test_identical_candidates_are_not_decided_by_iteration_order():
    """Two names that render identically are genuinely indistinguishable; reporting a zero
    margin is honest, quietly preferring the first is not."""
    from wddrop_client.capture.glyph import break_tie, make_renderer

    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    r = make_renderer(str(atlas), 25, (520, 27), 0.0)
    winner, margin, _fit = break_tie(r.render("得到了透明鵝卵石"), r, "得到了",
                               "透明鵝卵石", "透明鵝卵石")
    assert margin == 0.0


@needs_frame
def test_the_window_must_not_reach_into_the_next_panel_line():
    """The panel's line pitch is 31px. A 44px window reached into the line below, which the
    candidate does not have, and the mismatch cost 0.17 of score — enough to sink the read."""
    import json
    import re

    from wddrop_client.capture.glyph import RenderRecognizer, anchor_window, make_renderer

    vocab_path = ROOT / "data" / "vocab.zh_tw.json"
    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not (vocab_path.exists() and atlas.exists()):
        pytest.skip("data not built")
    raw = json.loads(vocab_path.read_text(encoding="utf-8"))
    prefix = re.sub(r"<[^>]+>", "", raw["templates"]["get_item"]).split("{0}")[0]
    names = [e["name"] for e in raw["items"] if e.get("name")]

    gray = load(FRAME)
    rows = panel_rows(gray)
    scores = {}
    for height in (27, 44):
        w = (520, height)
        rec = RenderRecognizer(make_renderer(str(atlas), 25, w, 0.0), prefix, names,
                               min_margin=0.0)
        scores[height] = rec.recognize(anchor_window(gray, rows[0], w)).score
    assert scores[27] > scores[44] + 0.1, scores


# -- the ▼ advance marker ---------------------------------------------------------
CAPTURES = paths.CAPTURES or Path("/nonexistent")
needs_captures = pytest.mark.skipif(not CAPTURES.is_dir(), reason="recordings not available")


@needs_captures
def test_the_marker_says_the_panel_is_finished():
    """The ▼ is the GAME stating the panel is done, so it beats inferring completeness from
    pixels settling: it is present on the FIRST frame the panel is complete, where the
    settle test needs a second frame to compare against. That gap is exactly what a player
    who clicks quickly falls into."""
    from wddrop_client.capture.panel import advance_marker

    complete = CAPTURES / "session-20260809-123515/episode-005/f_00002.png"
    animating = CAPTURES / "session-20260809-123515/episode-005/f_00001.png"
    if not (complete.exists() and animating.exists()):
        pytest.skip("the fast-click recording is not available")
    for path, expected in ((complete, True), (animating, False)):
        gray = load(path)
        assert advance_marker(gray, panel_rows(gray)) is expected, path.name


@needs_captures
def test_the_marker_is_a_shape_not_an_ink_count():
    """Ink alone accepts 60 frames of battle noise from a session with no mining in it at
    all — the noise runs 40-63 lit pixels in boxes like 24x3 and 39x70, against the marker's
    116-120 in a 15x16 box."""
    from wddrop_client.capture.panel import advance_marker, size_from_rows

    session = CAPTURES / "session-20260809-034520"
    if not session.is_dir():
        pytest.skip("the chest-only recording is not available")
    false_positives = 0
    for episode in sorted(p for p in session.iterdir() if p.is_dir()):
        for frame in sorted(episode.glob("f_*.png")):
            try:
                gray = load(frame)
            except Exception:
                continue
            rows = panel_rows(gray)
            size = size_from_rows(rows)
            if not rows or size is None or abs(size - 26) > 4:
                continue
            false_positives += advance_marker(gray, rows)
    assert false_positives == 0


def test_a_tie_is_only_broken_when_the_winner_actually_fits():
    """The margin says which of two candidates fits BETTER; it does not say either fits.

    A family of 111 精煉石（...）entries differs only in the digits inside the brackets. On a
    real panel the discriminating columns gave the wrong entry 0.2173 against the right one's
    0.1096 — a 0.108 margin, clear of the tie gate, between two readings that were both
    noise. It overturned a whole-line pass that had the RIGHT answer, and the wrong stone was
    recorded. Correct breaks measured on the same panels score 0.77-0.91.
    """
    from wddrop_client.capture.glyph import break_tie, make_renderer
    from wddrop_client.runner import MINING_TIE_MARGIN, MINING_TIE_MIN_SCORE

    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    r = make_renderer(str(atlas), 25, (520, 27), 0.0)
    observed = r.render("得到了下級鐵礦石")
    winner, margin, fit = break_tie(observed, r, "得到了", "下級鐵礦石", "上級鐵礦石")
    assert winner == "下級鐵礦石"
    assert margin >= MINING_TIE_MARGIN and fit >= MINING_TIE_MIN_SCORE

    # Nothing like either candidate: whichever wins, it must not clear the fit gate.
    noise = r.render("得到了透明鵝卵石")
    _winner, _margin, fit = break_tie(noise, r, "得到了", "下級鐵礦石", "上級鐵礦石")
    assert fit < MINING_TIE_MIN_SCORE, f"noise scored {fit:.4f} — the gate would pass it"


# -- the index exists before the first swing, not because of it -------------------------

def _runner_for(profile, names, dungeon_names=None):
    from wddrop_client.runner import CaptureRunner

    from wddrop_client.calibration import panel_columns, read_columns, ui_scale

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.profile = profile
    # Derived, not passed None: the columns are part of how the panel is read now, and a
    # hand-built runner that skips them tests a reader the client no longer has.
    runner.columns = read_columns(profile)
    runner.panel_box = panel_columns(profile)
    runner.ui_scale = ui_scale(profile.frame_size)
    runner._mining_names = names
    runner._mining_indexes = {}
    runner._mining_renderers = {}
    runner._probe_renderers = {}
    runner._mining_renderer = None
    runner._panel_window = (520, 44)
    runner._panel_fit = (profile.panel_font_size, profile.panel_letter_spacing)
    runner._render_source = profile.font_path
    runner._data_version = "test"
    runner.mining_prefix = "得到了"
    return runner


@pytest.fixture(scope="module")
def _shipped_profile():
    import json

    from wddrop_client.calibration import Profile

    # WRITTEN OUT HERE, not read from profiles.shipped.json. These fixtures are Chinese
    # panels and a fit is tied to the language it was scored in — but the game language is
    # fixed at Japanese now and the Chinese fits no longer ship. The geometry is still the
    # geometry those recordings were made at, so the tests keep their subject by carrying it.
    profile = Profile(
        frame_size=(704, 1241), message_band=(1000, 1022), font_path="",
        font_size=25, offset=(0, 0), calibration_score=0.9014774499379775,
        letter_spacing=1.1, window=(749, 51), text_x0=91, locale="zh_tw",
        panel_font_size=24, panel_letter_spacing=1.1,
        panel_data_version="b5b4fab0494322eb")
    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not atlas.exists():
        pytest.skip("atlas not built")
    profile.font_path = str(atlas)
    return profile


def test_the_mining_index_is_ready_before_the_first_swing(_shipped_profile):
    """It takes ~2.9s to build over 2,655 candidates and a panel is on screen for one to
    two seconds, so building it on the first panel meant the first swing of every fresh
    process was read before the index existed. Invisible on a recording, where the loop is
    synchronous and simply waits for it."""
    runner = _runner_for(_shipped_profile, ["下級鐵礦石", "透明鵝卵石"])
    assert runner._mining_indexes == {}

    assert runner.warm_mining_index(7015) is True
    assert runner._mining_indexes, "nothing was built before capture"
    assert runner._mining_renderer is not None


def test_a_dungeon_without_veins_pays_nothing(_shipped_profile):
    """The reason it was lazy in the first place, and still a good reason."""
    runner = _runner_for(_shipped_profile, ["下級鐵礦石"])
    assert runner.warm_mining_index(2000) is False
    assert runner._mining_indexes == {}
    assert runner.warm_mining_index(None) is False


def test_warming_uses_the_geometry_the_profile_already_knows(_shipped_profile):
    """Built at the panel's own size and spacing, not the message band's — one pixel of size
    is the difference between reading a panel and reading nonsense.

    The WINDOW is part of the key as well as the size, because it is part of the templates:
    warming one shape and then asking for another built an index nothing could use, and the
    mismatch surfaced deep inside a matmul as "size 3380 is different from 5720".
    """
    size = _shipped_profile.panel_font_size
    runner = _runner_for(_shipped_profile, ["下級鐵礦石"])
    runner.warm_mining_index(7015)
    assert list(runner._mining_indexes) == [
        runner._index_key(size, _shipped_profile.panel_letter_spacing,
                          runner._panel_window_for(size))]


def test_the_warm_index_is_the_one_the_fit_asks_for(_shipped_profile):
    """Warming a shape the fit never requests leaves the real build still to be done — on
    the first panel, which is exactly what warming exists to prevent."""
    size = _shipped_profile.panel_font_size
    runner = _runner_for(_shipped_profile, ["下級鐵礦石"])
    runner.warm_mining_index(7015)
    built = dict(runner._mining_indexes)

    again = runner._mining_index(size, runner._panel_window_for(size),
                                 _shipped_profile.panel_letter_spacing)
    assert again is not None
    assert dict(runner._mining_indexes) == built, "the fit had to build its own after all"


# -- the panel's letter spacing is FITTED, not inherited from the message band ------------
#
# 「ウロボロス鉱石」 went unread in session-20260812-171855 while every other line on the same
# panels read fine. It was rank 1 at 0.5595, under the 0.60 gate, because the panel was being
# rendered at the BAND's +1.1 letter spacing where it is drawn at +0.0. Spacing is added per
# CHARACTER, so the error accumulates along the line and the damage is a function of NAME
# LENGTH — measured on that frame, best alignment per character:
#
#     ウ:-1  ロ:-2  ボ:-3  ロ:-4  ス:-5  鉱:-6  石:-7        -1.1px per character
#
# and the whole-line scores, same frame, same vocabulary:
#
#     25px +1.1   中級鉄鉱石 0.712   透明な小石 0.706   ウロボロス鉱石 0.560   REFUSED
#     25px +0.0   中級鉄鉱石 0.890   透明な小石 0.895   ウロボロス鉱石 0.847   read
#
# Five-character names sail through the wrong geometry, which is why it survived: it shipped
# in profiles.shipped.json, and every check it had to pass was made with a name too short to
# fail.

JA_ATLAS = ROOT / "data" / "atlas.ja.json"
needs_ja = pytest.mark.skipif(not JA_ATLAS.exists(), reason="the Japanese atlas is not built")


def _ja_runner(names, panel_fit, band_spacing=1.1, size=25):
    """A runner with just enough wired up to fit a panel."""
    from wddrop_client.calibration import Profile
    from wddrop_client.capture.ocr import MessageFormat
    from wddrop_client.runner import CaptureRunner

    profile = Profile(
        frame_size=(704, 1241), message_band=(999, 1020), font_path=str(JA_ATLAS),
        font_size=size, offset=(-2, 1), calibration_score=0.91, letter_spacing=band_spacing,
        window=(976, 51), text_x0=93, locale="ja")
    from wddrop_client.calibration import panel_columns, read_columns, ui_scale

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.profile = profile
    runner.columns = read_columns(profile)
    runner.panel_box = panel_columns(profile)
    runner.ui_scale = ui_scale(profile.frame_size)
    runner._mining_names = list(names)
    runner._mining_indexes, runner._mining_renderers, runner._mining_renderer = {}, {}, None
    runner._probe_renderers = {}
    runner._panel_window = (520, 44)
    runner._panel_fit = panel_fit
    runner._spacing_fitted = False
    runner._render_source = str(JA_ATLAS)
    runner._data_version = "test"
    runner._profile_path = None
    runner.mining_prefix = ""
    runner.mining_min_score = 0.70
    # 「{0} を入手した」 is the panel's own wording, and 「{0}×{1}」 the quantity form.
    runner.mining_format = MessageFormat("{0} を入手した", "{0}×{1}")
    runner.fmt = runner.mining_format
    return runner


def _panel_frame(text, size=25, spacing=0.0, at=(150, 589)):
    """A frame with one panel line drawn on it, at a known spacing.

    The line is cut to what fits, exactly as `anchor_window` does on a real frame: the
    comparison window is wider than the space between the panel's left edge and the edge of
    a 704px screen.
    """
    import numpy as np

    from wddrop_client.capture.glyph import make_renderer

    line = make_renderer(str(JA_ATLAS), size, (520, size + 2), spacing).render(text)
    frame = np.zeros((1241, 704))
    x, y = at
    width = min(line.shape[1], frame.shape[1] - x)
    frame[y:y + line.shape[0], x:x + width] = line[:, :width]
    return frame


@needs_ja
def test_the_panel_spacing_is_fitted_absolutely_not_offset_from_the_band():
    """The search used to be offsets from the band's spacing (+-0.3, +-0.6), and an offset
    search cannot reach a value far from what it is an offset from: from the band's +1.1 it
    spanned 0.5 to 1.7, and the panel is drawn at +0.0. The right answer was never a
    candidate — not ranked low, not present."""
    from wddrop_client.runner import PANEL_SPACINGS

    assert min(PANEL_SPACINGS) <= 0.0 <= max(PANEL_SPACINGS)

    names = ["ウロボロス鉱石", "中級鉄鉱石", "透明な小石", "青銅の両手鎚矛", "銀等級の認識票"]
    frame = _panel_frame("ウロボロス鉱石 を入手した×3", spacing=0.0)
    runner = _ja_runner(names, panel_fit=(25, 1.1), band_spacing=1.1)

    best = runner._fit_panel(frame, (589, 610), 25)
    assert best is not None
    assert best[2] == pytest.approx(0.0, abs=0.05), (
        f"fitted spacing {best[2]:+.1f}, which is the band's, not the panel's")
    assert runner._panel_fit[1] == pytest.approx(0.0, abs=0.05)
    assert best[5] == "ウロボロス鉱石"


@needs_ja
def test_a_stored_panel_fit_is_still_checked_once():
    """The wrong spacing SHIPPED, in profiles.shipped.json — the band's value written into
    the panel's slot, never measured as a panel fit. A stored fit that is trusted forever is
    what let it survive, so it is measured once per session even when one arrives ready-made.
    """
    names = ["ウロボロス鉱石", "中級鉄鉱石", "透明な小石"]
    frame = _panel_frame("ウロボロス鉱石 を入手した×3", spacing=0.0)
    runner = _ja_runner(names, panel_fit=(25, 1.1))
    runner._fit_panel(frame, (589, 610), 25)
    assert runner._spacing_fitted, "a fit from the profile was taken on trust"
    assert runner._panel_fit == (25, pytest.approx(0.0, abs=0.05))

    # ...and once only. It describes how this client renders, not the panel in front of it,
    # so paying for it per swing buys the same number twice.
    runner._panel_fit = (25, 1.1)
    runner._fit_panel(frame, (589, 610), 25)
    assert runner._panel_fit == (25, 1.1), "the spacing was fitted more than once"


@needs_ja
def test_a_sentence_that_starts_with_an_item_name_is_not_that_item():
    """「北穿の金のつるはしが壊れてしまった」 BEGINS with the pickaxe's own item name, so it is
    a prefix match in exactly the way a half-drawn line is — and the panel has no termination
    rule to catch it.

    It was unreachable while the panel's geometry was wrong: the sentence scored 0.653 and
    was refused, which looked like a guard and was luck. The moment the spacing was fixed,
    two pickaxe BREAKS were recorded as two mined PICKAXES.

    What separates them is not score — it is that the name accounts for half the ink on the
    row. Measured over seven recorded sessions: item lines leave 71-168px against names that
    render 72-169px; the break sentence leaves 421px against a name that renders 221px.
    """
    from wddrop_client.capture.glyph import RenderRecognizer, make_renderer

    names = ["北穿の金のつるはし", "ウロボロス鉱石", "透明な小石"]
    renderer = make_renderer(str(JA_ATLAS), 25, (520, 27), 0.0)
    index = RenderRecognizer(renderer, "", names)
    runner = _ja_runner(names, panel_fit=(25, 0.0))

    sentence = renderer.render("北穿の金のつるはしが壊れてしまった")
    match = index.recognize(sentence)
    assert match.name == "北穿の金のつるはし", "the prefix does match — that is the problem"
    assert not runner._row_is_the_name(sentence, match), (
        "a break message was accepted as a mined pickaxe")

    # The item line it must not become confused with.
    item = renderer.render("ウロボロス鉱石")
    assert runner._row_is_the_name(item, index.recognize(item))


@needs_ja
def test_the_long_name_reads_on_the_real_frame():
    """End to end on the pixels that reported this, rather than on a rendering of them."""
    import json

    from wddrop_client.capture.glyph import (RenderRecognizer, anchor_window,
                                             make_renderer, mask_after_name)

    session = paths.capture("session-20260812-171855")
    frame = (session / "episode-004/f_00005.png") if session else None
    vocab_path = ROOT / "data" / "vocab.ja.json"
    if not (frame and frame.exists() and vocab_path.exists()):
        pytest.skip("the Japanese mining recording is not available")

    raw = json.loads(vocab_path.read_text(encoding="utf-8"))
    names = list(dict.fromkeys(
        e["name"] for e in raw["items"] + raw["equipment"] if e.get("name")))
    gray = load(frame)
    row, window = (589, 610), (520, 27)

    def read(spacing):
        renderer = make_renderer(str(JA_ATLAS), 25, window, spacing)
        crop = anchor_window(gray, row, window)
        named, _cut = mask_after_name(crop, renderer, " を入手した", "×", dy=1)
        return RenderRecognizer(renderer, "", names,
                                shifts=(range(-2, 3), range(-2, 3))).recognize(named)

    fitted = read(0.0)
    assert fitted.name == "ウロボロス鉱石", (fitted.best, round(fitted.score, 4))
    assert fitted.score > 0.80 and fitted.margin > 0.10

    # The state this shipped in, kept as the measurement rather than as a memory.
    inherited = read(1.1)
    assert inherited.best == "ウロボロス鉱石" and not inherited.accepted, (
        "the band's spacing no longer reproduces the failure this test is about")


# -- opening a chest must not cost an index build ----------------------------------------
#
# 「だれが開ける？」 — the chest's own "who opens it?" prompt — is one centred line, in the
# panel's search band, at a plausible line height. The fit therefore ran on it, and the fit
# builds an index over ~2,150 names per (face, size) it tries: SIX builds, ~1.2s each.
#
# Measured in session-20260814-181205: the capture loop froze for NINE SECONDS starting on
# the frame the player pressed Open — f_00014 at 02:12:09, f_00015 at 02:12:18 — and 136
# index builds across that one session. The chest's drop lines were never sampled, because
# nothing was sampled. Reported as "NOT RECORDING when I open the first chest".
#
# The panel says what it is: every yield line ends in 「 を入手した」. Finding that needs one
# renderer and one slide, and no vocabulary at all.

def test_a_row_without_the_panels_wording_costs_no_index_build():
    from wddrop_client.runner import CaptureRunner

    if not JA_ATLAS.exists():
        pytest.skip("atlas not built")
    runner = _ja_runner(["下級鉄鉱石", "透明な小石"], None, band_spacing=0.0, size=22)
    runner.profile.frame_size = (1920, 1080)

    import numpy as np

    from wddrop_client.calibration import panel_columns, ui_scale
    from wddrop_client.capture.glyph import make_renderer

    runner.panel_box = panel_columns(runner.profile)
    runner.ui_scale = ui_scale(runner.profile.frame_size)

    # A frame carrying one centred line that is NOT a yield line, drawn in the game's own
    # face so this is a fair test rather than a test of noise.
    prompt = make_renderer(str(JA_ATLAS), 22, (520, 44), 0.0).render("だれが開ける？")
    frame = np.zeros((1080, 1920), dtype=float)
    row = (637, 637 + 21)
    frame[row[0] - 3:row[0] - 3 + prompt.shape[0], 830:830 + prompt.shape[1]] = prompt

    built = []
    original = runner._mining_index

    def counting(size, window=None, spacing=None):
        built.append(size)
        return original(size, window, spacing)

    runner._mining_index = counting
    assert runner._fit_panel(frame, row, 22) is None
    assert built == [], f"the chest prompt built {len(built)} index(es): {built}"


def test_the_shortlist_points_at_the_size_the_panel_is_actually_drawn_at():
    """The wording is five characters and its slide score is sharp about size — sharp enough
    to choose the geometry, so a real panel costs ONE build rather than six."""
    from wddrop_client.capture.glyph import make_renderer

    if not JA_ATLAS.exists():
        pytest.skip("atlas not built")
    import numpy as np

    from wddrop_client.calibration import panel_columns

    runner = _ja_runner(["下級鉄鉱石"], None, band_spacing=0.0, size=22)
    runner.profile.frame_size = (1920, 1080)
    runner.panel_box = panel_columns(runner.profile)

    line = make_renderer(str(JA_ATLAS), 22, (520, 44), 0.0).render("下級鉄鉱石×3 を入手した")
    frame = np.zeros((1080, 1920), dtype=float)
    row = (512, 512 + 21)
    frame[row[0] - 3:row[0] - 3 + line.shape[0], 830:830 + line.shape[1]] = line

    shortlist = runner._yield_line_geometries(frame, row, [22, 21, 23])
    assert shortlist, "the panel's own wording was not found in a panel line"
    assert shortlist[0][2] == 22, [(round(s, 3), Path(f).name, z) for s, f, z in shortlist]


def test_a_break_is_kept_where_verify_can_find_it():
    """`on_pickaxe` is a callback for the window's counter; it leaves nothing behind. The
    reading itself — which pickaxe, when, and off which frame — is kept on the runner, so a
    tool that replays a recording can show it and ask whether it really happened.

    Deliberately NOT on `on_event`: that stream is the spool, and the wire has no provenance
    for a break. Sending one would be a schema change made by accident.
    """
    from datetime import datetime, timedelta, timezone

    from wddrop_client.capture.pickaxe import BROKE
    from wddrop_client.runner import CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.stats = {}
    runner.pickaxes_left = 3
    runner.pickaxes = None
    runner.on_pickaxe = None
    runner.pickaxe_events = []
    runner._break_index = 0
    runner._frame_src = "/somewhere/episode-016/f_00025.png"
    runner._dungeon_id = 7015
    start = datetime(2026, 8, 13, 14, 57, tzinfo=timezone.utc)
    runner.tracker = type("T", (), {"started_at": start})()

    runner._on_pickaxe_event((BROKE, "北穿の金のつるはし"), start + timedelta(seconds=6))
    assert runner.pickaxes_left == 2, "a break still spends a pickaxe"
    assert len(runner.pickaxe_events) == 1
    kept = runner.pickaxe_events[0]
    assert kept["provenance"] == "pickaxe_break"
    assert kept["dive"]["break_index_in_dive"] == 1
    assert kept["dive"]["elapsed_seconds"] == 6
    assert kept["contents"][0]["item_name"] == "北穿の金のつるはし"
    assert kept["contents"][0]["source_frame"] == "episode-016/f_00025.png"

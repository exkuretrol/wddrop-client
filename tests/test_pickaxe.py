"""
Pickaxes: the cost side of mining.

A pickaxe that breaks is spent, and running out is what silently ends a mining run — from
then on every mining spot only says "you could mine this if you had a pickaxe", which is
easy to walk past for a whole session. Both messages are the game's own strings, in all six
locales, so they can be matched exactly instead of guessed at.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

from wddrop_client.capture.pickaxe import BROKE, NONE_LEFT, PickaxeWatch  # noqa: E402

ZH_TW = dict(break_template="{0}壞掉了", none_text="如果有十字鎬的話應該能採掘。",
             pickaxe_names=["北穿的十字鎬", "北穿的黃金十字鎬"])


def test_a_broken_pickaxe_is_counted_per_kind():
    """Two pickaxes exist and they are not interchangeable — the gold one is the expensive
    one, so "3 broke" is not the whole story."""
    w = PickaxeWatch(**ZH_TW)
    assert w.feed("北穿的十字鎬壞掉了") == (BROKE, "北穿的十字鎬")
    assert w.feed("北穿的十字鎬壞掉了") == (BROKE, "北穿的十字鎬")
    assert w.feed("北穿的黃金十字鎬壞掉了") == (BROKE, "北穿的黃金十字鎬")
    assert w.total_broken == 3
    assert dict(w.broken) == {"北穿的十字鎬": 2, "北穿的黃金十字鎬": 1}


def test_running_out_is_a_state_not_a_count():
    """The out-of-pickaxes line fires once per mining spot walked up to, so counting it
    would report "you lost 9 pickaxes" to somebody who lost none."""
    w = PickaxeWatch(**ZH_TW)
    for _ in range(5):
        assert w.feed("如果有十字鎬的話應該能採掘。") == (NONE_LEFT, "")
    assert w.out_of_pickaxes and w.total_broken == 0
    assert "restock" in w.summary()


def test_an_ordinary_drop_line_is_not_a_pickaxe_message():
    w = PickaxeWatch(**ZH_TW)
    assert w.feed("獲得了北穿幽靈城的四鱗雜物！！") is None
    assert w.feed("") is None
    assert w.summary() == ""


def test_full_width_punctuation_folds():
    """The templates carry full-width punctuation, and the reader and the renderer see it in
    different forms — comparing raw strings would miss on exactly the locales that use it."""
    w = PickaxeWatch(**ZH_TW)
    assert w.feed("如果有十字鎬的話應該能採掘｡") is None or w.out_of_pickaxes
    assert w.feed("  北穿的十字鎬壞掉了  ") == (BROKE, "北穿的十字鎬")


def test_a_vocabulary_without_the_templates_disables_it(tmp_path):
    """Vocabularies built before mining existed simply turn the feature off — they must not
    crash, and they must not silently pretend to be watching."""
    path = tmp_path / "vocab.zh_tw.json"
    path.write_text(json.dumps({"locale": "zh_tw", "templates": {}, "items": []}),
                    encoding="utf-8")
    w = PickaxeWatch.from_vocab(path)
    assert len(w) == 0 and w.candidates == []
    assert w.feed("anything") is None


# -- against the real, built vocabularies ------------------------------------------
LOCALES = ["zh_tw", "ja", "en", "de", "ko", "zh_cn"]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_locale_yields_matchable_messages(locale):
    """The break template's placeholder sits in a different place per locale, and Korean
    even adds a particle (「{0}이(가) 망가졌다.」), so a pattern written for one locale would
    match nothing anywhere else."""
    path = ROOT / "data" / f"vocab.{locale}.json"
    if not path.exists():
        pytest.skip("vocabularies not built")
    w = PickaxeWatch.from_vocab(path)
    assert len(w) == 3, f"{locale}: expected 2 pickaxes + 1 none-line, got {w.candidates}"
    for line in w.candidates:
        assert w.feed(line) is not None, f"{locale}: cannot match its own {line!r}"


@pytest.mark.parametrize("locale", ["zh_tw", "ja"])
def test_the_messages_survive_render_and_compare(locale):
    """The real path: render the sentence the way the game would, then read it back through
    the same recogniser the runner uses. A message that cannot be told from the item
    vocabulary would be worse than useless."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL.Image")
    vocab_path = ROOT / "data" / f"vocab.{locale}.json"
    atlas = ROOT / "data" / f"atlas.{locale}.json"
    if not (vocab_path.exists() and atlas.exists()):
        pytest.skip("vocabularies/atlases not built")

    from wddrop_client.capture.glyph import RenderRecognizer, make_renderer

    w = PickaxeWatch.from_vocab(vocab_path)
    renderer = make_renderer(str(atlas), 26, (900, 46), 0.0)
    rec = RenderRecognizer(renderer, "", w.candidates)
    for line in w.candidates:
        match = rec.recognize(renderer.render(line))
        assert match.accepted and match.name == line, \
            f"{locale}: {line!r} read back as {match.name!r} ({match.score:.3f})"


def test_a_drop_line_is_not_swallowed_by_the_pickaxe_index():
    """THE dangerous direction. The pickaxe index runs FIRST and returns early on a hit, so
    a drop line that matched it would not merely be misread — it would be dropped silently,
    losing the chest. Three candidates against a 3,400-name vocabulary is exactly the setup
    where a lone survivor can win by default, so the gates have to hold."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL.Image")
    vocab_path = ROOT / "data" / "vocab.zh_tw.json"
    atlas = ROOT / "data" / "atlas.zh_tw.json"
    if not (vocab_path.exists() and atlas.exists()):
        pytest.skip("vocabularies/atlases not built")

    from wddrop_client.capture.glyph import RenderRecognizer, make_renderer

    w = PickaxeWatch.from_vocab(vocab_path)
    renderer = make_renderer(str(atlas), 26, (900, 46), 0.0)
    rec = RenderRecognizer(renderer, "", w.candidates)
    for line in ("獲得了北穿幽靈城的妖異冥刻雜物×3！！",
                 "獲得了100拜恩紙幣×2！！",
                 "獲得了高級治療劑！！",
                 "獲得了10,000拜恩紙幣！！"):
        match = rec.recognize(renderer.render(line))
        assert not match.accepted, \
            f"{line!r} was taken for the pickaxe message {match.name!r} ({match.score:.3f})"

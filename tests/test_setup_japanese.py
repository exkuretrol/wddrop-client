"""
Setting the client up when the game is in Japanese — which is what every player is asked to
do, and what the setup path had never been exercised against.

Two failures lived here, both from the same assumption: that the invariant part of a drop
message comes BEFORE the item name.

    zh_tw   獲得了{0}！！              the invariant leads
    ja      {0}を手に入れた!!          the invariant trails

`propose_item_name` fits its geometry against that invariant so it can read the item name and
fill the box in for the player. Given a leading one it renders it at the origin; given a
trailing one it was handed the empty string, fitted nothing (size 12 at score 0.000), and
returned None for every Japanese shot ever taken. The player then had to find their item among
~3,500 names, in a script their keyboard may not have.
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

pytest.importorskip("numpy", reason="numpy not installed")
pytest.importorskip("PIL.Image", reason="pillow not installed")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

ATLAS = ROOT / "data" / "atlas.ja.json"
VOCAB = ROOT / "data" / "vocab.ja.json"
# The trailing invariant, as vocab.ja.json states it.
SUFFIX = "を手に入れた!!"
NAME = "モニヨン銀貨"


def _frame(name: str, size: int = 25, spacing: float = 1.1):
    """A frame with one drop line on it, where the game puts it.

    Rendered rather than recorded so the test carries its own evidence: what is under test is
    that a line whose invariant TRAILS can be read at all, and that property does not depend
    on owning a screenshot.
    """
    from wddrop_client.capture.glyph import make_renderer

    renderer = make_renderer(str(ATLAS), size, (704, 40), spacing)
    line = np.asarray(renderer.render(f"{name}×2{SUFFIX}"), dtype=float)
    frame = np.zeros((1241, 704), dtype=float)
    frame[1000:1000 + line.shape[0], :line.shape[1]] = line[:, :704]
    return Image.fromarray(np.clip(frame, 0, 255).astype("uint8"), mode="L")


@pytest.mark.skipif(not (ATLAS.exists() and VOCAB.exists()),
                    reason="needs the built data files")
def test_the_item_name_is_read_from_a_japanese_shot():
    """The measurement this exists for. Before the fix this returned None, always."""
    from wddrop_client.calibration import propose_item_name

    names = [e["name"] for e in json.loads(VOCAB.read_text(encoding="utf-8"))["items"]]
    guess = propose_item_name(_frame(NAME), "", [str(ATLAS)], names, suffix=SUFFIX)

    assert guess is not None, "nothing was read from a Japanese drop line"
    read, _score, margin = guess
    assert read == NAME, read
    # Separation, never absolute score — see the calibration module.
    assert margin > 0.05, margin


@pytest.mark.skipif(not (ATLAS.exists() and VOCAB.exists()),
                    reason="needs the built data files")
def test_without_the_trailing_invariant_it_declines_rather_than_guesses():
    """A template with nothing invariant at either end cannot be fitted against, and the
    honest answer is no answer: the box stays empty and the player types.

    This is the case the old code hit silently — it had a prefix to render, the prefix was
    empty, and it went on to 'read' whatever the resulting garbage geometry produced."""
    from wddrop_client.calibration import propose_item_name

    names = [e["name"] for e in json.loads(VOCAB.read_text(encoding="utf-8"))["items"]]
    assert propose_item_name(_frame(NAME), "", [str(ATLAS)], names, suffix="") is None


def test_keeping_the_frames_survives_a_restart(tmp_path, monkeypatch):
    """Both recording checkboxes were the only controls on the settings page that lived in
    the widget and nowhere else, so every launch reset them to off.

    The way that fails is what makes it worth a test: nothing looks wrong at the time. A
    player turns recording on to explain a miss, restarts, dives — and the frames for the
    very session they wanted are not there, with no message to say so.
    """
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from wddrop_client.config import ClientConfig

    cfg = ClientConfig.load()
    assert cfg.keep_frames is False and cfg.keep_all_frames is False, "off until asked for"

    cfg.keep_frames = True
    cfg.keep_all_frames = True
    cfg.save()

    assert ClientConfig.load().keep_frames is True
    assert ClientConfig.load().keep_all_frames is True


def test_hiding_a_file_is_not_done_by_renaming_it(tmp_path):
    """A leading dot hides nothing on Windows — it is a Unix convention, and Explorer shows
    `.atlas.ja.json` like any other name. So the atlas is marked with the file ATTRIBUTE,
    and the name it is written under never changes.

    Off Windows there is nothing to set and the call reports so rather than pretending.
    """
    from wddrop_client.config import hide

    target = tmp_path / "atlas.ja.json"
    target.write_text("{}", encoding="utf-8")

    marked = hide(target)

    assert target.exists(), "hiding must not rename or remove the file"
    assert target.name == "atlas.ja.json"
    assert marked is (sys.platform.startswith("win"))


def test_the_copied_faces_are_removed_once_the_atlas_exists(tmp_path, monkeypatch):
    """The copy exists to be rasterised. After that it is a duplicate of a commercial
    typeface sitting in a player's folder, and `game_fonts` re-extracts it from the game
    whenever it is missing — so removing it costs a rebuild a few seconds and nothing else.
    """
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from wddrop_client import gamefont
    from wddrop_client.config import config_dir

    fonts = config_dir() / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for name in gamefont.WANTED:
        (fonts / f"{name}.ttf").write_bytes(b"not really a font")

    removed = gamefont.discard_cache()

    assert sorted(removed) == sorted(f"{n}.ttf" for n in gamefont.WANTED)
    assert not fonts.exists(), "the empty folder goes too"
    assert gamefont.discard_cache() == [], "and doing it twice is harmless"


def test_skipping_the_first_shot_still_reaches_the_second(tmp_path, qt_app=None):
    """Skip is an offered choice, so it has to lead somewhere.

    The dialog inferred which step it was on from "have I got a walk shot yet", which cannot
    express the third state: skipped. Skipping advanced the WORDING to step 2 and nothing
    else — so the next capture was taken as the walk shot again, written over walk.png, and
    the dialog looped there with its button still reading Capture. The text said step 2; the
    button did step 1; the drop shot was unreachable.
    """
    pytest.importorskip("PySide6.QtWidgets", reason="Qt not installed")
    from types import SimpleNamespace

    from wddrop_client.ui import CalibrateDialog

    dialog = CalibrateDialog.__new__(CalibrateDialog)
    dialog.walk = dialog.drop = None
    dialog._walk_done = False
    dialog.t = lambda text, **kw: text
    dialog.args = SimpleNamespace(data=str(tmp_path))
    dialog.status = SimpleNamespace(setText=lambda *_: None)
    dialog.step = SimpleNamespace(setText=lambda *_: None)
    dialog.reject = lambda: pytest.fail("skipping the walk shot closed the dialog")

    assert Path(CalibrateDialog._shot_target(dialog)).name == "walk.png"
    CalibrateDialog._skip(dialog)
    assert dialog._walk_done, "the step was not recorded as finished"
    # ...and the NEXT capture is the drop shot, which is what makes the button become Fit.
    assert Path(CalibrateDialog._shot_target(dialog)).name == "drop.png"


def test_the_band_is_fitted_against_the_face_it_is_drawn_in(tmp_path):
    """Calibration swept whatever `--fonts` named, and the window names the PANEL's atlas
    there — so the band was fitted against the wrong typeface.

    It does not fail cleanly: the fit lands on a different size and spacing, scores badly,
    and is then refused by the profile's own self-check. Measured on a real shot, same frame
    and same name: 0.7943 fitted against the panel's face, 0.9242 against the band's.
    """
    from types import SimpleNamespace

    from wddrop_client.__main__ import _band_font_candidates

    panel = tmp_path / "atlas.ja.json"
    band = tmp_path / "atlas.ja.scenario.json"
    for f in (panel, band):
        f.write_text("{}", encoding="utf-8")

    args = SimpleNamespace(fonts=str(panel), band_fonts=str(band))
    assert _band_font_candidates(args) == [str(band)]

    # ...and with no band atlas built yet, it falls back rather than failing.
    assert _band_font_candidates(SimpleNamespace(fonts=str(panel), band_fonts=None)) \
        == [str(panel)]


def test_the_dialog_cannot_be_closed_while_it_is_reading(tmp_path):
    """The read takes tens of seconds and every way of interrupting it leaves the dialog
    somewhere it cannot recover from: skipping starts a second shot while the first is still
    being read, and closing deletes a widget a worker thread is about to signal — which
    crashes later, after the dialog is gone, where nothing connects it to calibration.

    The name box stays live on purpose: the vocabulary is offered as soon as it loads so the
    player can start picking while the proposal is still being worked out.
    """
    pytest.importorskip("PySide6.QtWidgets", reason="Qt not installed")
    from types import SimpleNamespace

    from wddrop_client.ui import CalibrateDialog

    enabled = {}

    def widget(key):
        return SimpleNamespace(setEnabled=lambda v, k=key: enabled.__setitem__(k, v))

    dialog = CalibrateDialog.__new__(CalibrateDialog)
    dialog.action, dialog.skip = widget("action"), widget("skip")
    dialog._working = False

    CalibrateDialog._busy(dialog, True)
    assert enabled == {"action": False, "skip": False}

    ignored = []
    CalibrateDialog.closeEvent(dialog, SimpleNamespace(ignore=lambda: ignored.append(1)))
    assert ignored, "the window closed while a thread was still using it"

    CalibrateDialog._busy(dialog, False)
    assert enabled == {"action": True, "skip": True}


def test_calibration_is_a_development_feature(tmp_path, monkeypatch):
    """Shipped fits are the ones that have been checked against recordings. A calibration
    made on a player's machine is a claim nobody has verified — and the one made here was
    fitted against the wrong typeface for three versions with nothing to say so.

    So the offer to make one is kept for builds that are still being worked on. The check is
    the PRESENCE of a marker put there at build time, which is what makes a production exe
    carry no way to turn it back on.
    """
    import wddrop_client.config as config

    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    assert config.in_development(), "a checkout is a development build by definition"

    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "bundled_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "program_dir", lambda: tmp_path / "nowhere")
    assert not config.in_development(), "a build without the marker is production"

    (tmp_path / config.DEV_MARKER).write_text("x", encoding="utf-8")
    assert config.in_development(), "a build WITH the marker still offers it"


def test_the_scenario_atlas_is_written_under_the_name_it_is_looked_up_by(tmp_path):
    """`build` composes `atlas.{stem}.json`, so a stem of "atlas.ja.scenario" produces
    `atlas.atlas.ja.scenario.json` — a file nothing ever looks for.

    Nothing failed. `find_data` simply never found it, so the settings page rebuilt the atlas
    on every launch, and the message band quietly went on reading against the mining panel's
    typeface — the exact fault the scenario atlas exists to fix. The only visible symptom was
    the rebuild, and that looks like ordinary first-run behaviour.
    """
    from wddrop_client.__main__ import _scenario_beside

    panel = tmp_path / "atlas.ja.json"
    panel.write_text("{}", encoding="utf-8")
    # What the lookup expects, spelled out rather than derived, so the two cannot drift.
    expected = tmp_path / "atlas.ja.scenario.json"
    expected.write_text("{}", encoding="utf-8")
    assert _scenario_beside(str(panel)) == str(expected)

    # ...and what build() writes for the stem its callers pass. `build` composes
    # "atlas.{stem}", so the stem is the locale and the suffix — not the whole filename.
    assert f"atlas.{'ja.scenario'}.json" == expected.name


def test_a_hidden_atlas_can_still_be_rebuilt(tmp_path, monkeypatch):
    """Windows refuses to open an existing HIDDEN file for writing, and reports it as a
    permission error naming the file — so marking the atlas made it unrebuildable, and the
    second build failed with `[Errno 13] ... atlas.ja.png`.

    The mark is cleared before writing and reapplied after. Checked by call ORDER rather than
    by attributes, because the platform this runs on has neither.
    """
    import wddrop_client.atlas as atlas_module
    import wddrop_client.config as config

    calls = []
    monkeypatch.setattr(config, "unhide", lambda p: calls.append(("unhide", Path(p).name)))
    monkeypatch.setattr(config, "hide", lambda p: calls.append(("hide", Path(p).name)))

    import paths

    root = paths.FONTS
    fonts = sorted(root.glob("*/*/Font/BaseFont.ttf")) if root and root.is_dir() else []
    if not fonts:
        pytest.skip("game fonts not extracted")
    vocab = {"locale": "ja", "items": [{"name": "金の針"}], "templates": {}}
    atlas_module.build(fonts[-1], vocab, tmp_path, "ja", reference=32)

    order = [what for what, _ in calls]
    assert order, "neither unhide nor hide was called"
    assert order.index("unhide") < order.index("hide"), (
        f"the file is written before its mark is cleared: {calls}")


def test_changing_the_interface_language_applies_at_once(tmp_path, monkeypatch):
    """Every string was translated on the way in, so changing the language changed the
    setting and nothing a player could see — they had to restart to find out it had worked.

    Rebuilding the window is the fix, and the thing that must NOT happen is doing it during a
    capture: that owns a worker thread and a spool, and tearing its window down to change a
    language is not a trade worth offering. The setting is saved either way.
    """
    pytest.importorskip("PySide6.QtWidgets", reason="Qt not installed")
    from types import SimpleNamespace

    from wddrop_client.ui import MainWindow

    rebuilt, said = [], []
    window = MainWindow.__new__(MainWindow)
    window.cfg = SimpleNamespace(ui_locale=None, save=lambda: None)
    window.ui_locale = SimpleNamespace(currentData=lambda: "ja")
    window.t = lambda text, **kw: text
    monkeypatch.setattr(MainWindow, "_say", lambda self, *a, **k: said.append(a[0]))
    monkeypatch.setattr(MainWindow, "_relaunch_in_the_new_language",
                        lambda self: rebuilt.append(1))

    MainWindow._ui_locale_changed(window, 0)
    assert window.cfg.ui_locale == "ja"
    assert rebuilt, "the language was saved but the window was never rebuilt"

    # ...and not while a recording owns the window.
    window.worker = SimpleNamespace(isRunning=lambda: True)
    monkeypatch.undo()
    monkeypatch.setattr(MainWindow, "_say", lambda self, *a, **k: said.append(a[0]))
    MainWindow._relaunch_in_the_new_language(window)
    assert said and "stops" in said[-1], said

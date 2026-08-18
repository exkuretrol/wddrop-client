"""The calibration dialog, and the button that looks at what the client sees.

Three things a player did, in this order, on the day 1600x900 was first calibrated:
pressed *See it* before calibrating (the window went away), waited through the name guess
with a box that accepted typing it was about to overwrite, and re-ran calibration from the
top because the fit had been refused — going back into the game for two more screenshots
that were already on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("PySide6", reason="the window is asked for explicitly")
pytest.importorskip("PIL.Image")

import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from wddrop_client import ui  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _args(tmp_path):
    return SimpleNamespace(data=str(tmp_path), delay=5, locale="ja", band_fonts=None,
                           vocab=str(ROOT / "data" / "vocab.ja.json"), fonts=None,
                           source="window")


def test_see_it_says_there_is_no_calibration_instead_of_leaving(app, tmp_path, monkeypatch):
    """`_select_profile` says "no calibration for this size" by raising SystemExit — it is
    written for the command line, where that IS the message. SystemExit is not an Exception,
    so it went through the handler and out of a Qt slot, and the window closed on a player
    who pressed a button to be told something."""
    from PIL import Image

    from wddrop_client import __main__ as cli

    monkeypatch.setattr(cli, "_live_size", lambda args: (1600, 900))
    monkeypatch.setattr(cli, "_select_profile",
                        lambda args, size: (_ for _ in ()).throw(SystemExit("no calibration")))
    frame = Image.new("L", (1600, 900))
    monkeypatch.setattr("wddrop_client.capture.source.open_source",
                        lambda *a, **k: SimpleNamespace(
                            frames=lambda: iter([SimpleNamespace(image=frame)])))

    dialog = ui.SeeingDialog(_args(tmp_path))
    dialog._grab()
    assert "1600x900" in dialog.note.text()
    assert dialog._frame is None


def test_the_name_box_is_locked_while_the_guess_runs(app, tmp_path):
    """It is filled in by the reader when it finishes, so a player typing meanwhile either
    has their answer kept without knowing why the box stopped, or loses it."""
    dialog = ui.CalibrateDialog(_args(tmp_path))
    dialog.name.setVisible(True)

    dialog._busy(True)
    assert not dialog.name.isEnabled()
    assert not dialog.load.isEnabled(), "a second shot must not start under a running read"

    dialog._busy(False)
    assert dialog.name.isEnabled()
    # Focus is not assertable on a dialog that was never shown — Qt assigns a focus widget
    # when there is a window to have it. `test_setup_japanese` covers the setFocus call.


def test_a_saved_shot_can_be_used_instead_of_going_back_into_the_game(app, tmp_path):
    """Calibration is re-run for reasons that have nothing to do with the pictures — a fit
    refused, a size changed, a reader improved. The files are already here."""
    from PIL import Image

    dialog = ui.CalibrateDialog(_args(tmp_path))
    assert dialog._shot_target().endswith("walk.png")

    dialog._use_saved()
    assert "capture one" in dialog.status.text(), "it should say there is nothing saved yet"
    assert dialog.walk is None

    Image.new("L", (1600, 900)).save(tmp_path / "walk.png")
    dialog._use_saved()
    assert dialog.walk == tmp_path / "walk.png"
    # ...and the size is stated, because a saved shot can be from another resolution and the
    # fit it produces belongs to THAT one.
    assert "1600x900" in dialog.status.text()
    # The step machine moved on exactly as a capture would have.
    assert dialog._walk_done and dialog._shot_target().endswith("drop.png")


def test_the_command_line_renders_with_the_atlas_the_window_uses(tmp_path, monkeypatch):
    """`--fonts` used to be required, and that made the command line a trap: the client
    renders from an ATLAS built out of the player's own copy of the game, and nothing on the
    command line said so — so the obvious thing to pass is the extracted `fonts/*.ttf`, which
    is a different typeface from the one on screen.

    Measured on a real 1600x900 shot: calibrating with BaseFont_ChineseTraditional.ttf scored
    0.547 and failed its own check, where the atlas fits the same shot at 0.823.
    """
    from types import SimpleNamespace as NS

    from wddrop_client.__main__ import _band_font_candidates, _font_candidates

    args = NS(fonts=None, locale="ja", band_fonts=None)
    chosen = _font_candidates(args)
    assert chosen, "nothing to render with, and no --fonts given"
    assert all(Path(p).suffix == ".json" for p in chosen), chosen
    # The band is fitted against BOTH faces, scenario first — the same list the window uses.
    assert Path(_band_font_candidates(args)[0]).name.endswith(".scenario.json")


def test_a_refused_self_check_names_what_the_shot_nearly_said():
    """The commonest cause is a name typed from memory — 「…の四鱗のガラクタ」 for a shot that
    says 「…の妖なる四鱗のガラクタ」 — and a bare None sends somebody looking for a fault in
    the fit instead."""
    import inspect

    from wddrop_client import calibration

    source = inspect.getsource(calibration.fit_message_profile)
    assert "The closest reading was" in source
    assert "match.runner_up" in source


def test_calibrate_does_not_demand_a_typeface_on_the_command_line():
    """`--fonts` was required, so every command line had to name one — and the honest guess
    is the extracted `fonts/*.ttf`, which is not what the game draws with."""
    import subprocess
    import sys as _sys

    out = subprocess.run([_sys.executable, str(ROOT / "wddrop.py"), "calibrate", "--help"],
                         capture_output=True, text=True, timeout=120)
    usage = out.stdout
    assert "[--fonts FONTS]" in usage, usage[:400]
    assert "required" not in usage.split("options:")[0]


def test_the_vocabulary_is_found_from_any_directory(tmp_path, monkeypatch):
    """`--vocab` defaults to a bare filename, which resolves against the working directory —
    so the command line worked from the folder the client was unpacked into and nowhere
    else, while the window searched the program's folder and the data folder."""
    from types import SimpleNamespace as NS

    from wddrop_client.__main__ import _load_vocab

    if not (ROOT / "data" / "vocab.ja.json").exists():
        pytest.skip("vocabulary not built")
    monkeypatch.chdir(tmp_path)
    vocab, fmt, raw = _load_vocab(NS(vocab="vocab.ja.json", locale="ja"))
    assert raw["templates"]["drop_item"]
    assert len(vocab.entries) > 100

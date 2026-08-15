"""
The message band and the mining panel are drawn in DIFFERENT typefaces.

The game picks a face per UI element: every text object carries a serialized font name and
`LocalizeFontManager.GetFont(language, name)` resolves it. The two surfaces this client reads
sit on opposite sides of that choice — measured against real frames, each face given its own
best alignment:

    message band  「…を手に入れた!!」   ScenarioFont 0.83-0.91   BaseFont 0.69-0.84
    mining panel  「… を入手した」      BaseFont     0.59-0.76   ScenarioFont 0.51-0.64

The client built ONE atlas, from BaseFont, and rendered both against it. The panel was right
by accident; every chest line was matched against the wrong face, which is what pushed
「100バイン紙幣」 to 0.5428 under a 0.60 gate and silently cost four chests their banknote.

The calibration is not implicated — 25px at +1.1 spacing is the best fit for BOTH faces — so
what is under test here is only WHICH FILE each reader is handed.

See the vault: Reference/UI Font System.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

from wddrop_client.__main__ import _band_source, _scenario_beside  # noqa: E402


def _atlases(tmp_path: Path, scenario: bool = True) -> str:
    """A panel atlas, optionally with its scenario twin beside it."""
    panel = tmp_path / "atlas.ja.json"
    panel.write_text("{}", encoding="utf-8")
    if scenario:
        (tmp_path / "atlas.ja.scenario.json").write_text("{}", encoding="utf-8")
    return str(panel)


def args(**kw) -> SimpleNamespace:
    return SimpleNamespace(**{"fonts": None, "band_fonts": None, **kw})


def test_the_band_reads_against_the_scenario_atlas(tmp_path):
    """The measurement this exists for: the band must not be handed the panel's face."""
    panel = _atlases(tmp_path)
    assert _band_source(args(), panel) == str(tmp_path / "atlas.ja.scenario.json")


def test_without_the_scenario_atlas_nothing_gets_worse(tmp_path):
    """A player who has not rebuilt their atlas keeps exactly the behaviour they had.

    The alternative — failing, or naming a file that is not there — would turn an upgrade
    into a client that records nothing, which is the one outcome worse than reading against
    the wrong face.
    """
    panel = _atlases(tmp_path, scenario=False)
    assert _band_source(args(), panel) == panel


def test_an_explicit_fonts_flag_still_pins_both(tmp_path):
    """`--fonts` promises that one named file is what rendered the result.

    Comparing two renderers on the same recording is the whole point of the flag, and
    silently rendering half the surfaces with a different file would break the only
    guarantee it makes — quietly, in exactly the measurement someone reached for it to take.
    """
    panel = _atlases(tmp_path)
    named = str(tmp_path / "atlas.ja.json")
    assert _band_source(args(fonts=named), panel) == panel


def test_the_gui_names_the_band_atlas_outright(tmp_path):
    """The GUI already overrides `--fonts` to force the LOCALE's atlas over whatever locale
    the profile happens to have been fitted in. One override must not silently decide the
    other, so it passes the band's face explicitly and that wins."""
    panel = _atlases(tmp_path)
    chosen = str(tmp_path / "atlas.ja.scenario.json")
    assert _band_source(args(fonts=panel, band_fonts=chosen), panel) == chosen


def test_a_scenario_atlas_is_never_given_a_scenario_twin(tmp_path):
    """Guards against `atlas.ja.scenario.scenario.json` — the derivation has to be idempotent
    or a second pass through it names a file nobody builds."""
    twin = tmp_path / "atlas.ja.scenario.json"
    twin.write_text("{}", encoding="utf-8")
    assert _scenario_beside(str(twin)) is None


def test_a_font_file_has_no_scenario_twin(tmp_path):
    """Calibrating against a raw .ttf is supported, and a font is not an atlas — deriving a
    sibling from one would name nonsense."""
    font = tmp_path / "BaseFont.ttf"
    font.write_bytes(b"")
    assert _scenario_beside(str(font)) is None


def test_the_two_readers_end_up_with_different_faces(tmp_path, monkeypatch):
    """The wiring, not the choice.

    `_band_source` returning the right path is worth nothing if the runner hands the same
    file to both readers — which is precisely the bug being fixed, and it survived this long
    because one atlas looked like a simplification rather than a mistake.
    """
    import wddrop_client.__main__ as main

    panel = _atlases(tmp_path)
    band = _band_source(args(), panel)

    assert band != panel, "the band and the panel must not share a face"
    assert Path(band).name == "atlas.ja.scenario.json"
    assert Path(panel).name == "atlas.ja.json"
    # ...and the panel's source is what the runner is told to build its indexes from.
    assert main._scenario_beside(panel) == band


# -- rebuilding an atlas that predates the second face --------------------------------

def test_an_upgrade_rebuilds_the_atlas_once_and_only_once(monkeypatch, tmp_path):
    """A player upgrading from a one-atlas build has a complete-LOOKING install that is still
    reading every chest line against the panel's face, so the settings page rebuilds.

    The danger is the loop that sits behind that: a successful build calls `_refresh_setup`,
    which asks the same question that started the build. If the build cannot produce what was
    asked for — a game shipping only one face, so no scenario atlas is written — the answer
    stays "missing" and it rebuilds forever, in a background thread, with no visible symptom
    but a pegged core.
    """
    pytest = __import__("pytest")
    pytest.importorskip("PySide6.QtWidgets", reason="Qt not installed")
    from wddrop_client.ui import MainWindow as Window

    window = Window.__new__(Window)
    window.cfg = SimpleNamespace(locale="ja")
    window._atlas_worker = None
    window.t = lambda text, **kw: text
    started = []
    monkeypatch.setattr(Window, "_say", lambda self, *a, **k: None)
    monkeypatch.setattr(
        "wddrop_client.ui.AtlasWorker",
        lambda locale, parent=None: SimpleNamespace(
            done=SimpleNamespace(connect=lambda fn: None),
            start=lambda: started.append(locale), isRunning=lambda: False),
    )

    Window._build_atlas(window)
    window._atlas_worker = None          # as `_atlas_built` leaves it
    Window._build_atlas(window)          # the refresh that follows a successful build
    Window._build_atlas(window)

    assert started == ["ja"], f"the atlas was rebuilt {len(started)} times, not once"


# -- ink alone is not a message ---------------------------------------------------------

def test_a_speck_of_wall_is_not_a_drop_line(monkeypatch):
    """The gate that stopped a chest containing a broom.

    A 17-pixel mark on a dungeon wall was read as 「箒」 and recorded as a chest. Nothing
    downstream could have caught it: the recogniser's job is to say WHICH name is on screen,
    and asked about noise it answers anyway — 0.6290 under one typeface, 0.5491 under
    another, the margin landing either side of the ambiguity gate depending on which atlas
    the client happened to build. A reading that turns on a coincidence that fine is not a
    reading, so whether a message is there AT ALL is decided before recognition runs.

    Measured at 704x1241, the two populations are an order of magnitude apart:

        real drop lines           283, 352, 380, 389, 507 px of ink
        the speck                  17 px

    so this is a wide gate, not a tuned one.
    """
    pytest = __import__("pytest")
    pytest.importorskip("numpy", reason="numpy not installed")
    import numpy as np

    from wddrop_client.capture.glyph import INK_LEVEL
    from wddrop_client.runner import MIN_LINE_INK_FRACTION, CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.profile = SimpleNamespace(message_band=(0, 22), window=(749, 51), offset=(0, 0))
    # The synthetic frame IS the band, with no dialogue box around it to bound — which is
    # what an un-fitted profile gives the real runner too, and it must still read.
    runner.columns = None
    runner.stats = {"skipped_blank": 0, "skipped_same": 0, "skipped_animating": 0}
    runner._pending = None
    runner._last_mask = None
    runner._last_band_key = None
    runner._recognised_key = None
    runner._last_text = ""
    runner._frame_src = None
    # The invariant renders 168px in ja, so the gate sits at ~100px.
    runner._min_line_px_value = int(168 * MIN_LINE_INK_FRACTION)
    monkeypatch.setattr(CaptureRunner, "_flush_pending", lambda self, now: "")
    read = []
    monkeypatch.setattr(CaptureRunner, "_recognise",
                        lambda self, *a, **k: read.append(1) or "something")

    frame = np.zeros((30, 704), dtype=np.uint8)
    frame[4:18, 300:317] = INK_LEVEL + 60          # 17px of ink: the speck

    assert runner._read_band(frame, now=0.0) == ""
    assert not read, "the recogniser was asked about a 17px mark"
    assert runner.stats["skipped_too_small"] == 1

    frame[4:18, 200:500] = INK_LEVEL + 60          # 300px: a real line's worth
    runner._read_band(frame, now=0.0)               # ...seen
    runner._read_band(frame, now=0.1)               # ...and held still, which is when it reads
    assert read, "a line wide enough to be a message was not read"


def test_the_gate_is_derived_from_the_locale_not_fixed(monkeypatch):
    """The invariant wording is 168px in Japanese and 117px in Chinese on the same client at
    the same resolution, so a constant here would be one language's number imposed on every
    other. And with nothing to derive it from, the gate must disappear rather than guess."""
    from wddrop_client.runner import MIN_LINE_INK_FRACTION, CaptureRunner

    runner = CaptureRunner.__new__(CaptureRunner)
    runner.prefix = ""
    runner.renderer = SimpleNamespace(ink_width=lambda text: 168)
    runner.fmt = SimpleNamespace(raw={"drop_item": "{0}を手に入れた!!"})
    assert runner._min_line_px() == int(168 * MIN_LINE_INK_FRACTION)

    bare = CaptureRunner.__new__(CaptureRunner)
    bare.prefix = ""
    bare.renderer = None
    bare.fmt = None
    assert bare._min_line_px() == 0, "with nothing to derive from, the gate must not exist"

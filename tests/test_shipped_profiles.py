"""
The calibrations that ship with the client.

A player at a verified resolution should not have to calibrate, and — the reason this exists
— should not have to survive calibration getting it wrong. At 1920x1080 the region search
settled on a rock face while the minimap sat elsewhere, so the stored "HUD template" was a
photograph of a wall: it matched 13 frames in 2341, episodes never closed, and four chests
were recorded as one. The fit that ships is the fit that was tested against real recordings.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

import pytest  # noqa: E402

pytest.importorskip("numpy", reason="numpy not installed")
pytest.importorskip("PIL.Image", reason="pillow not installed")

from wddrop_client.calibration import Profile, ProfileStore, decode_template  # noqa: E402

SHIPPED = ROOT / ProfileStore.SHIPPED
# Every fit that ships, as `<resolution>@<game language>` — the language is part of the
# identity, not a note about it: a fit names the atlas it was rendered from, and that atlas
# only exists for the language it was built for.
# ONE, and that is the whole supported surface. The Chinese fits were real and verified —
# the 1920x1080 one was the only wide window that ever worked — but the game language is
# fixed at Japanese now, because the face the recogniser needs is only reachable there. A fit
# for a language the client can no longer draw is a fit nothing can use.
VERIFIED = {"704x1241@ja"}


@pytest.fixture(scope="module")
def shipped() -> dict:
    if not SHIPPED.exists():
        pytest.skip(f"{ProfileStore.SHIPPED} not built")
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def test_the_fits_with_a_tested_calibration_are_the_ones_that_ship(shipped):
    """Each has been checked against recordings replayed end to end.

    Shipping a fit is not the same as RECOMMENDING the size: the guide names only the tall
    window, because 1920x1080 samples the screen more slowly and still misreads some item
    names. The 1080 fit ships anyway — a player who chooses that size is better off with a
    calibration that was tested than with one the region search improvised.

    The client asks for a Japanese game, so `ja` is the one most players will get; the
    Chinese fits stay for the players who were recording before it did.
    """
    assert set(shipped) == VERIFIED


def test_a_fit_is_only_offered_for_the_language_it_was_made_for(monkeypatch, tmp_path):
    """The locale tag still decides, even though only one language ships.

    It is what stops a fit being handed to a client that cannot draw the names it was made
    for — the run would then die at the font rather than at the choice. Kept as a test
    because the mechanism outlives the current contents of the file.
    """
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    chosen = ProfileStore.shipped("ja").get((704, 1241))
    assert chosen is not None and chosen.font_path == "atlas.ja.json"
    assert chosen.locale == "ja"
    # Nothing is offered for a language nothing was fitted in.
    assert ProfileStore.shipped("zh_tw").get((704, 1241)) is None


def test_only_the_tall_window_is_supported_now(monkeypatch, tmp_path):
    """Removing the Chinese fits removed the only 1920x1080 one with them, so the client
    supports exactly one window size. Stated here so that is a decision on the record rather
    than a thing someone discovers."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    assert ProfileStore.shipped("ja").keys() == ["704x1241"]


@pytest.mark.parametrize("key", sorted(VERIFIED))
def test_a_shipped_profile_is_complete_enough_to_capture_with(shipped, key):
    p = Profile(**shipped[key])
    size, _, locale = key.partition("@")
    assert tuple(p.frame_size) == tuple(int(v) for v in size.split("x"))
    assert p.locale == locale
    assert p.message_band[1] > p.message_band[0]
    assert p.font_size > 0 and p.calibration_score > 0.7
    # The panel fit too: without it every session re-derives the mining size by sweeping.
    assert p.panel_font_size and p.panel_letter_spacing is not None


@pytest.mark.parametrize("key", sorted(VERIFIED))
def test_a_shipped_profile_carries_its_own_hud_template(shipped, key):
    """Embedded, not a path: a sidecar file would point at the machine that made it."""
    p = Profile(**shipped[key])
    assert p.hud_region and len(p.hud_region) == 4
    assert p.hud_template_path is None
    assert decode_template(p) is not None


@pytest.mark.parametrize("key", sorted(VERIFIED))
def test_a_shipped_profile_names_no_path_from_the_machine_that_made_it(shipped, key):
    """It is copied to every player. An absolute path here resolves to nothing on theirs —
    or worse, to something."""
    raw = shipped[key]
    assert "/" not in raw["font_path"] and "\\" not in raw["font_path"]
    for value in raw.values():
        if isinstance(value, str):
            assert "Users" not in value and "home" not in value


def test_the_shipped_calibration_wins_over_one_fitted_here(tmp_path, monkeypatch):
    """This used to be the other way round, and the old reason was good at the time: the
    player had calibrated against their own machine and shipped was a stand-in for a step
    they had not taken.

    It stopped being true when calibration stopped being offered to players. A shipped fit is
    the one that has been checked against recordings; a fit left in a folder is a claim
    nobody verified, and it cannot be corrected by upgrading — it silently outranks whatever
    ships later. Measured on the player this was written for: a 24px/+2.0 profile stayed in
    use after 25px/+1.1 shipped, and because the mining panel's spacing sweep is relative to
    the band's, mining recorded NOTHING for two further sessions. Deleting the file by hand
    was the only fix.
    """
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    mine = Profile(frame_size=(704, 1241), message_band=(0, 1), font_path="x", font_size=99,
                   offset=(0, 0), calibration_score=0.9)
    store = ProfileStore()
    store.put(mine)
    store.save(tmp_path)

    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    chosen = cli._select_profile(SimpleNamespace(data=str(tmp_path)), (704, 1241))
    assert chosen.font_size != 99, "a fit left in the folder outranked the shipped one"
    assert chosen.font_size == ProfileStore.shipped("ja").get((704, 1241)).font_size


def test_a_size_nothing_ships_for_still_uses_the_players_own(tmp_path, monkeypatch):
    """Which is the case a local fit exists for. Preferring shipped must not mean ignoring
    theirs — at a resolution the client has never been tested at, theirs is all there is."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    mine = Profile(frame_size=(1280, 800), message_band=(0, 1), font_path="x", font_size=99,
                   offset=(0, 0), calibration_score=0.9)
    store = ProfileStore()
    store.put(mine)
    store.save(tmp_path)

    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    assert ProfileStore.shipped("ja").get((1280, 800)) is None, "this size must be unshipped"
    chosen = cli._select_profile(SimpleNamespace(data=str(tmp_path)), (1280, 800))
    assert chosen.font_size == 99


def test_a_resolution_with_neither_is_refused_with_both_lists(tmp_path, monkeypatch):
    """Silently capturing at the wrong calibration is how a session records nothing and
    leaves the cause to be guessed at."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    with pytest.raises(SystemExit) as exc:
        cli._select_profile(SimpleNamespace(data=str(tmp_path)), (1234, 567))
    assert "1234x567" in str(exc.value) and "Shipped" in str(exc.value)

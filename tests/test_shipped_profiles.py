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
VERIFIED = {"704x1241", "1920x1080"}


@pytest.fixture(scope="module")
def shipped() -> dict:
    if not SHIPPED.exists():
        pytest.skip(f"{ProfileStore.SHIPPED} not built")
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def test_the_resolutions_with_a_tested_fit_are_the_ones_that_ship(shipped):
    """Both have a fit checked against recordings replayed end to end.

    Shipping a fit is not the same as RECOMMENDING the size: the guide names only the tall
    window, because 1920x1080 samples the screen more slowly and still misreads some item
    names. The 1080 fit ships anyway — a player who chooses that size is better off with a
    calibration that was tested than with one the region search improvised.
    """
    assert set(shipped) == VERIFIED


@pytest.mark.parametrize("key", sorted(VERIFIED))
def test_a_shipped_profile_is_complete_enough_to_capture_with(shipped, key):
    p = Profile(**shipped[key])
    assert tuple(p.frame_size) == tuple(int(v) for v in key.split("x"))
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


def test_the_players_own_calibration_wins_over_the_shipped_one(tmp_path, monkeypatch):
    """They calibrated against their machine; shipped is a stand-in for a step not taken."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    mine = Profile(frame_size=(704, 1241), message_band=(0, 1), font_path="x", font_size=99,
                   offset=(0, 0), calibration_score=0.9)
    store = ProfileStore()
    store.put(mine)
    store.save(tmp_path)

    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    chosen = cli._select_profile(SimpleNamespace(data=str(tmp_path)), (704, 1241))
    assert chosen.font_size == 99, "the shipped profile overrode the player's own"


def test_a_resolution_with_neither_is_refused_with_both_lists(tmp_path, monkeypatch):
    """Silently capturing at the wrong calibration is how a session records nothing and
    leaves the cause to be guessed at."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    with pytest.raises(SystemExit) as exc:
        cli._select_profile(SimpleNamespace(data=str(tmp_path)), (1234, 567))
    assert "1234x567" in str(exc.value) and "Shipped" in str(exc.value)

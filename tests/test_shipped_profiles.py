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
VERIFIED = {"704x1241@ja", "1920x1080@ja", "1600x900@ja"}


@pytest.fixture(scope="module")
def shipped() -> dict:
    if not SHIPPED.exists():
        pytest.skip(f"{ProfileStore.SHIPPED} not built")
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def test_the_fits_with_a_tested_calibration_are_the_ones_that_ship(shipped):
    """Each has been checked against recordings replayed end to end.

    The 1920x1080 fit is back, and it is not the one that was withdrawn. That one was made
    by a region search that settled on a rock face; this one was fitted from a burst of
    walking frames (notes: stability 0.9996, leak -0.2074) and replayed, with NO local
    calibration present, against the recordings that found every fault this client has:

        the chest at 06:25   both items, including the wrapped 「…のガラクタ×3を」
        four mining swings   both lines each
        three breaks         read at their own size

    Shipping a fit is not the same as RECOMMENDING the size: the guide still names the tall
    window, because 1920x1080 samples the screen more slowly. A player who chooses it anyway
    is better off with a calibration that was tested than with one improvised on their
    machine.
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


def test_the_tested_window_sizes_are_offered(monkeypatch, tmp_path):
    """Three sizes ship, and no more. Stated here so the supported surface is a decision on
    the record rather than a thing someone discovers by trying."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    assert ProfileStore.shipped("ja").keys() == ["1600x900", "1920x1080", "704x1241"]


def test_a_shipped_fit_names_files_and_never_folders(shipped):
    """This file is published. A fit carries three paths, and on the machine it was made on
    all three are absolute — `C:\\Users\\<name>\\AppData\\...` — which would put somebody's
    user name in a public repository AND resolve to nothing on any other computer.

    The second half is not cosmetic: the runner takes the stored panel face only if it
    resolves, so an absolute path from elsewhere silently falls back to the BAND's face, and
    at 1920x1080 that reads the mining panel at 0.726 where its own face reads 0.905.
    """
    for name, fit in shipped.items():
        for field in ("font_path", "panel_font_path", "hud_template_path"):
            value = fit.get(field)
            if not value:
                continue
            assert value == Path(value).name, f"{name}.{field} names a folder: {value}"
            assert ":" not in value and "\\" not in value, f"{name}.{field}: {value}"


@pytest.mark.parametrize("key", ["1920x1080@ja", "1600x900@ja"])
def test_a_wide_fit_carries_a_hud_it_can_actually_match(shipped, key):
    """The withdrawn 1920x1080 fit stored a photograph of a wall: it matched 13 frames in
    2341, so episodes never closed and four chests were recorded as one. What separates the
    two is measurable and is written down in the fit itself.

    Both wide fits are now measured rather than searched, and 1600x900 is where it shows what
    the measurement is worth: the player's own calibration there settled on map interior
    (y 68-100, the grid itself), which matched 0 frames of a 1,121-frame window and 2 of 441.
    Fitted against four real walking frames with the drop shot as the negative, the band moves
    down onto the button bar (y 160-191) and matches 152 of each, and 286 of 309 across six
    replayed episodes."""
    notes = shipped[key].get("notes") or {}
    assert notes.get("hud_stability", 0) >= 0.9, notes
    assert notes.get("hud_leak", 1) <= 0.15, notes
    assert shipped[key].get("hud_threshold"), "no fitted threshold"


@pytest.mark.parametrize("key", sorted(VERIFIED))
def test_a_shipped_profile_is_complete_enough_to_capture_with(shipped, key):
    p = Profile(**shipped[key])
    size, _, locale = key.partition("@")
    assert tuple(p.frame_size) == tuple(int(v) for v in size.split("x"))
    assert p.locale == locale
    assert p.message_band[1] > p.message_band[0]
    assert p.font_size > 0 and p.calibration_score > 0.7
    # The panel fit too, INCLUDING ITS FACE. Without the size, every session re-derives it by
    # sweeping; without the face, the panel is read in the BAND's typeface, and that is not a
    # cosmetic difference — see test_a_shipped_fit_names_the_panels_own_face.
    assert p.panel_font_size and p.panel_letter_spacing is not None


@pytest.mark.parametrize("key", sorted(VERIFIED))
def test_a_shipped_fit_names_the_panels_own_face(shipped, key):
    """MEASURED TWICE, and the second time it cost a reading that was already confirmed.

    The panel and the message band are rendered from different atlases. When a fit omits
    `panel_font_path` the runner falls back to the band's face, and the damage is not spread
    evenly: at 1600x900 the ore lines still read 0.885 — high enough that a replay looks
    perfectly healthy — while 「北穿の金のつるはしが壊れてしまった」 fell to **0.7548**,
    under the 0.85 gate, and all four of that session's pickaxe breaks vanished. With the
    face named, the same break reads **0.9026**, margin 0.3443, and the session goes from
    20 match / 4 missing to 24 match / 0 differ.

    A break is the reading with the most to lose: a false one spends a pickaxe the player
    still has, and a missed one silently inflates every pickaxe-lifetime figure drawn from
    the session.

    What matters is the face the panel is READ in, not the field: 704x1241 reads its message
    band from `atlas.ja.json` already, so its fallback lands on the right atlas and it names
    no panel face at all. The two wide fits read the band from the scenario atlas, so they
    have to say so.
    """
    fit = shipped[key]
    reads_in = fit.get("panel_font_path") or fit["font_path"]
    assert reads_in == "atlas.ja.json", f"the panel would be read from {reads_in}"


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


def test_a_players_build_is_told_what_it_can_actually_do_about_it(tmp_path, monkeypatch):
    """The same refusal, worded for someone whose client does not offer calibration.

    `wddrop calibrate` is a command in a window with no console, in a build where the button
    is not there either — instructions that cannot be carried out, which is worse than the
    bare fact. What a player CAN do is set the game's own resolution to one the client ships
    a fit for, so that is what it says.
    """
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli
    from wddrop_client import config

    monkeypatch.setattr(config, "in_development", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli._select_profile(SimpleNamespace(data=str(tmp_path), locale="ja"), (1234, 567))
    said = str(exc.value)
    assert "1234x567" in said
    assert "calibrat" not in said.lower(), said
    assert "1920x1080" in said, "the sizes it CAN read, which are game settings"


def test_a_shipped_fit_replaces_the_players_own_for_that_size(monkeypatch, tmp_path):
    """"Invalidate" means IGNORE, not delete: the file stays, and it is still used for any
    size the shipped set does not cover.

    The order used to be the other way round, and a stale local fit silently outranked a
    better one shipped later with nothing to say so — measured on the player this was
    written for, a 24px/+2.0 fit stayed in use after 25px/+1.1 shipped and mining recorded
    nothing for two more sessions. Upgrading the client could not fix it; only deleting the
    file could.
    """
    import json as _json

    from wddrop_client.calibration import Profile, ProfileStore

    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    mine = Profile(frame_size=(1920, 1080), message_band=(1, 2), font_path="atlas.ja.json",
                   font_size=24, offset=(0, 0), calibration_score=0.5, letter_spacing=2.0,
                   locale="ja")
    store = ProfileStore.load(tmp_path)
    store.put(mine)
    store.save(tmp_path)

    args = type("A", (), {"data": str(tmp_path), "locale": "ja"})()
    from wddrop_client.__main__ import _select_profile

    chosen = _select_profile(args, (1920, 1080))
    baked = _json.loads(SHIPPED.read_text(encoding="utf-8"))["1920x1080@ja"]
    assert chosen.font_size == baked["font_size"] != mine.font_size
    assert chosen.letter_spacing == baked["letter_spacing"] != mine.letter_spacing
    # ...and a size nothing ships for is still the player's own.
    store.put(Profile(frame_size=(800, 600), message_band=(1, 2), font_path="atlas.ja.json",
                      font_size=20, offset=(0, 0), calibration_score=0.9, locale="ja"))
    store.save(tmp_path)
    assert _select_profile(args, (800, 600)).font_size == 20


def test_what_the_shipped_fit_cannot_know_is_carried_over(monkeypatch, tmp_path):
    """The mining panel's geometry is LEARNED on the player's machine — no shot of a panel
    goes into a calibration — so dropping it with the rest of their fit means re-fitting the
    panel every session and saving the answer into an entry that is outranked. Gaps only: a
    value the shipped fit carries is never overwritten by theirs."""
    from wddrop_client.calibration import Profile, ProfileStore
    from wddrop_client.__main__ import _select_profile

    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    mine = Profile(frame_size=(704, 1241), message_band=(1, 2), font_path="atlas.ja.json",
                   font_size=24, offset=(0, 0), calibration_score=0.5, locale="ja")
    mine.panel_font_path = "atlas.ja.json"
    mine.panel_font_size = 99
    store = ProfileStore.load(tmp_path)
    store.put(mine)
    store.save(tmp_path)

    args = type("A", (), {"data": str(tmp_path), "locale": "ja"})()
    chosen = _select_profile(args, (704, 1241))
    assert chosen.panel_font_path == "atlas.ja.json", "the learned face was thrown away"
    assert chosen.panel_font_size == 25, "the shipped value was overwritten by a local one"

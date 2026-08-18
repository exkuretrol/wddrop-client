"""
Where the player's data lives, and how it gets there from where it used to live.

State used to sit beside the program. That is the folder a player drags a new version into,
and it holds the install_id — the only handle they have for erasure, and the thing that
makes a returning player the same player. Losing it does not look like a loss: capture keeps
working, a new id is generated, and their earlier records are stranded under a pseudonym
nobody can reach.
"""
from __future__ import annotations

import json
from pathlib import Path


import pytest

from wddrop_client.config import (STATE_FILES, ClientConfig, config_dir,
                                  migrate_state, records_path, spool_path)


@pytest.fixture
def home(tmp_path, monkeypatch):
    new = tmp_path / "appdata"
    monkeypatch.setenv("WDDROP_HOME", str(new))
    return new


def test_everything_lives_under_one_folder(home):
    """So "delete this folder" is the whole answer to "how do I remove my data"."""
    assert spool_path().parent == config_dir()
    assert records_path().parent == config_dir()
    assert (config_dir() / "config.json").parent == config_dir()


def test_the_install_id_survives_the_move(home, tmp_path):
    """The one file whose loss is unrecoverable: a new id means their old records belong to
    a stranger and the id they were told to quote no longer refers to anything."""
    old = tmp_path / "program"
    old.mkdir()
    (old / "config.json").write_text(json.dumps({
        "install_id": "keep-me", "server_url": "http://localhost:8080", "consent": {},
    }), encoding="utf-8")

    assert "config.json" in migrate_state(old)
    assert ClientConfig.load().install_id == "keep-me"
    assert not (old / "config.json").exists(), "left behind, it would be found again later"


def test_the_unsent_spool_and_the_players_copy_move_too(home, tmp_path):
    old = tmp_path / "program"
    old.mkdir()
    (old / "spool.jsonl").write_text('{"event_id": "a"}\n', encoding="utf-8")
    (old / "records.jsonl").write_text('{"event_id": "a"}\n', encoding="utf-8")
    (old / "profile.json").write_text("{}", encoding="utf-8")

    moved = migrate_state(old)
    assert {"spool.jsonl", "records.jsonl", "profile.json"} <= set(moved)
    assert spool_path().read_text(encoding="utf-8").strip() == '{"event_id": "a"}'


def test_a_file_already_in_the_new_folder_is_never_overwritten(home, tmp_path):
    """The new folder is the newer truth. Copying an old spool over a current one would
    resurrect events already sent and lose ones that were not."""
    old = tmp_path / "program"
    old.mkdir()
    (old / "spool.jsonl").write_text('{"event_id": "old"}\n', encoding="utf-8")
    config_dir().mkdir(parents=True, exist_ok=True)
    spool_path().write_text('{"event_id": "current"}\n', encoding="utf-8")

    assert "spool.jsonl" not in migrate_state(old)
    assert "current" in spool_path().read_text(encoding="utf-8")


def test_migrating_from_the_folder_it_already_uses_does_nothing(home):
    """WDDROP_HOME can point anywhere, including at the program itself."""
    assert migrate_state(config_dir()) == []


def test_data_files_are_not_treated_as_state(home, tmp_path):
    """Vocabularies and atlases ship with the program and are rebuilt from the game's own
    files. Dragging them into the player's folder would strand them when it is deleted."""
    for name in ("vocab.zh_tw.json", "atlas.zh_tw.json", "catalog.zh_tw.json",
                 "boosts.json"):
        assert name not in STATE_FILES


def test_the_program_folder_is_still_searched_for_its_own_data(monkeypatch, tmp_path):
    """State moved out; the vocabulary did not. A client launched from a shortcut has some
    other working directory, and must still find the files shipped beside it.

    Skipped without the GUI toolkit rather than failing: `find_data` lives in `ui`, which
    imports PySide6 at module level, and PySide6 is deliberately not installed where the
    capture path is tested — an 80MB toolkit must never become a requirement of reading the
    screen. CI is exactly that environment, and this was the one test that assumed otherwise.
    """
    pytest.importorskip("PySide6.QtWidgets", reason="Qt not installed")

    from wddrop_client import ui

    monkeypatch.setenv("WDDROP_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.chdir(tmp_path)
    shipped = ui.program_dir() / "vocab.__test__.json"
    shipped.write_text("{}", encoding="utf-8")
    try:
        assert ui.find_data("vocab.{locale}.json", "__test__") == shipped
    finally:
        shipped.unlink()


def test_a_profile_path_left_dangling_by_the_move_is_repaired(home, tmp_path):
    """The move took the HUD template with it and left the profile naming where it used to
    be. Nothing breaks — the template is also embedded, and the embedded copy wins — but a
    file that describes itself wrongly is a trap for whoever reads it next."""
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "hud_template.png").write_bytes(b"PNG")
    (config_dir() / "profile.json").write_text(json.dumps({
        "frame_size": [704, 1241], "message_band": [1000, 1022],
        "font_path": "C:/gone/atlas.zh_tw.json", "font_size": 26, "offset": [-1, -1],
        "calibration_score": 0.86,
        "hud_template_b64": "AAAA",
        "hud_template_path": "C:/old/place/hud_template.png",
    }), encoding="utf-8")

    # Nothing is left to MOVE — the profile is already here. The repair still runs, which is
    # what makes this reach an install that migrated before the repair existed.
    assert migrate_state(tmp_path / "nothing-here") == []
    repaired = json.loads((config_dir() / "profile.json").read_text(encoding="utf-8"))
    assert repaired["hud_template_path"] == str(config_dir() / "hud_template.png")
    assert repaired["hud_template_b64"] == "AAAA", "the embedded copy is untouched"


def test_a_path_that_still_resolves_is_left_alone(home, tmp_path):
    """Repair means repair. Rewriting a working path would be a second way to break one."""
    real = tmp_path / "hud_template.png"
    real.write_bytes(b"PNG")
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "profile.json").write_text(json.dumps({
        "frame_size": [1, 1], "message_band": [0, 1], "font_path": "x", "font_size": 1,
        "offset": [0, 0], "calibration_score": 0.0, "hud_template_path": str(real),
    }), encoding="utf-8")

    from wddrop_client.config import repair_profile_paths

    assert repair_profile_paths() == []
    kept = json.loads((config_dir() / "profile.json").read_text(encoding="utf-8"))
    assert kept["hud_template_path"] == str(real)


def test_the_atlas_is_found_again_after_the_program_folder_moves(home, tmp_path):
    """The profile stores an absolute path to wherever the client was when it was fitted.
    That folder is the player's to move or reinstall, and losing a calibration to it would
    be silent."""
    from wddrop_client.calibration import Profile
    from wddrop_client.config import program_dir

    shipped = program_dir() / "atlas.__test__.json"
    shipped.write_text("{}", encoding="utf-8")
    try:
        profile = Profile(frame_size=(1, 1), message_band=(0, 1), font_size=1,
                          offset=(0, 0), calibration_score=0.0,
                          font_path="C:/a/place/that/is/gone/atlas.__test__.json")
        assert Path(profile.resolve_font(near=config_dir())) == shipped
    finally:
        shipped.unlink()


# -- the panel fit, and what invalidates it -----------------------------------------

def test_a_declared_version_beats_a_fingerprint(tmp_path):
    """The data repo stamps a version; a human-readable "1.35.0" beats a hash in a log."""
    from wddrop_client.calibration import data_version

    plain = tmp_path / "vocab.json"
    plain.write_text(json.dumps({"locale": "zh_tw", "items": []}), encoding="utf-8")
    assert len(data_version(plain)) == 16, "no version declared -> fingerprint"

    stamped = tmp_path / "vocab2.json"
    stamped.write_text(json.dumps({"locale": "zh_tw", "version": "1.35.0"}), encoding="utf-8")
    assert data_version(stamped) == "1.35.0"


def test_changing_the_data_changes_the_version(tmp_path):
    """A fit is a claim about how THIS atlas renders THIS vocabulary. Carrying one across a
    data update would read plausible wrong item names rather than fail."""
    from wddrop_client.calibration import data_version

    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps({"items": ["a"]}), encoding="utf-8")
    atlas = tmp_path / "atlas.png"
    atlas.write_bytes(b"PNG-one")

    before = data_version(vocab, atlas)
    atlas.write_bytes(b"PNG-two")                 # the atlas is rebuilt
    assert data_version(vocab, atlas) != before

    vocab.write_text(json.dumps({"items": ["a", "b"]}), encoding="utf-8")   # a new item
    assert data_version(vocab, atlas) != before


def test_the_version_is_stable_for_unchanged_data(tmp_path):
    """Otherwise every session would refit and the persistence would buy nothing."""
    from wddrop_client.calibration import data_version

    f = tmp_path / "vocab.json"
    f.write_text(json.dumps({"items": ["a"]}), encoding="utf-8")
    assert data_version(f) == data_version(f)


def test_a_profile_carries_the_panel_geometry_and_its_version(tmp_path):
    """Stored so a later session builds ONE index instead of searching for the right one."""
    from wddrop_client.calibration import Profile

    p = Profile(frame_size=(1920, 1080), message_band=(870, 889), font_path="x",
                font_size=22, offset=(0, 0), calibration_score=0.87,
                panel_font_size=22, panel_letter_spacing=-0.1, panel_data_version="1.35.0")
    out = tmp_path / "profile.json"
    p.save(out)
    back = Profile.load(out)
    assert (back.panel_font_size, back.panel_letter_spacing) == (22, -0.1)
    assert back.panel_data_version == "1.35.0"


def test_an_old_profile_without_a_panel_fit_still_loads(tmp_path):
    """Every existing profile predates these fields; they must default, not raise."""
    from wddrop_client.calibration import Profile

    out = tmp_path / "profile.json"
    out.write_text(json.dumps({
        "frame_size": [704, 1241], "message_band": [1000, 1022], "font_path": "x",
        "font_size": 26, "offset": [-1, -1], "calibration_score": 0.86,
    }), encoding="utf-8")
    p = Profile.load(out)
    assert p.panel_font_size is None and p.panel_data_version is None


# -- what must survive closing the window ---------------------------------------------

def test_the_chosen_dungeon_is_remembered(home):
    """A player runs one dungeon for a session at a time. Re-picking it every launch is a
    step they will eventually forget, and a dive filed under the wrong dungeon is worse than
    one never recorded."""
    cfg = ClientConfig.load()
    assert cfg.dungeon_id is None, "unset, so it cannot be confused with a real choice"
    cfg.dungeon_id = 7015
    cfg.save()
    assert ClientConfig.load().dungeon_id == 7015


def test_a_config_written_by_another_version_still_opens(home):
    """Settings are lost the moment load() raises: the window cannot open, and the player's
    only route back is deleting the file that also holds their install_id. A key this build
    does not know is not a reason to throw any of that away."""
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "config.json").write_text(json.dumps({
        "install_id": "keep-me", "pickaxes": 7,
        "a_setting_from_a_later_build": True,
    }), encoding="utf-8")

    cfg = ClientConfig.load()
    assert cfg.install_id == "keep-me" and cfg.pickaxes == 7

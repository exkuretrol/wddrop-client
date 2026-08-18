"""
Choosing the right window.

The failure this defends against actually happened: a Discord window whose CHANNEL was named
after the game ("… | Wizardry Variants Daphne | …") matched the title hint before the game
did, so the client tried to read a 2560x1392 chat window as if it were the game.
"""
from __future__ import annotations


# The CLI module imports the schema package, which lives beside the client rather than
# being installed.

from wddrop_client.capture import window as W


def make(title, w, h, process=""):
    return W.WindowInfo(handle=1, title=title, left=0, top=0, width=w, height=h, process=process)


GAME = make("WizardryVariantsDaphne", 1920, 1080, "wizardryvariantsdaphne.exe")
DISCORD = make("#主頻道 | Wizardry Variants Daphne | 討論區 - Discord", 2560, 1392, "discord.exe")


def pick(windows, title=None, expect_size=None, monkeypatch=None):
    W.list_windows = lambda *a, **k: windows          # type: ignore[assignment]
    return W.find_window(title, expect_size=expect_size)


def test_process_name_beats_a_title_that_merely_mentions_the_game():
    assert pick([DISCORD, GAME]).process == "wizardryvariantsdaphne.exe"


def test_order_does_not_decide_it():
    assert pick([GAME, DISCORD]).title == "WizardryVariantsDaphne"


def test_calibrated_size_disambiguates_when_the_process_is_unknown():
    """Some setups cannot read the process name; the size still separates them."""
    game = make("WizardryVariantsDaphne", 1920, 1080)
    assert pick([DISCORD, game], expect_size=(1920, 1080)).width == 1920


def test_exact_title_matches_when_the_process_is_unreadable():
    game = make("WizardryVariantsDaphne", 1920, 1080)
    assert pick([make("Notes", 800, 600), game]).title == "WizardryVariantsDaphne"


def test_no_builtin_substring_search():
    """A window merely MENTIONING the game must never be picked by default. Discord's title
    contains the game's name and would win any substring search."""
    import pytest

    with pytest.raises(SystemExit):
        pick([make("#chat | Wizardry Variants Daphne | zh", 2560, 1392, "discord.exe")])


def test_explicit_substring_is_honoured_when_asked_for():
    w = make("My Game Window - Daphne", 1920, 1080)
    assert pick([w], title="Daphne").title == "My Game Window - Daphne"


def test_nothing_matching_lists_what_was_open():
    import pytest

    with pytest.raises(SystemExit) as exc:
        pick([make("Some Other App", 1000, 800, "other.exe")])
    assert "Some Other App" in str(exc.value)


# -- which calibration live capture uses --------------------------------------------

def _two_calibrations(tmp_path):
    """A player who has calibrated BOTH windowed and fullscreen. profile.json holds
    whichever was saved last, which is why it cannot be the one that decides."""
    from wddrop_client.calibration import Profile, ProfileStore

    store = ProfileStore()
    for size, font_size in (((704, 1241), 25), ((1920, 1080), 22)):
        store.put(Profile(frame_size=size, message_band=(0, 1), font_path="x",
                          font_size=font_size, offset=(0, 0), calibration_score=0.9))
    store.save(tmp_path)
    store.get((704, 1241)).save(tmp_path / "profile.json")     # the last one calibrated
    return tmp_path


def test_live_capture_uses_the_calibration_for_the_window_it_found(tmp_path, monkeypatch):
    """Reported from a real run: the window was found at 1920x1080, both resolutions were
    calibrated, and the client used the 704x1241 fit anyway — then reported the mismatch as
    if nothing were calibrated for the size. Replay had picked by frame size since
    ProfileStore existed; live capture never did, because it read profile.json directly.
    """
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli
    from wddrop_client.capture.window import WindowInfo

    monkeypatch.setattr(cli, "find_window", None, raising=False)
    monkeypatch.setattr("wddrop_client.capture.window.find_window",
                        lambda title=None, expect_size=None: WindowInfo(
                            1, "WizardryVariantsDaphne", 621, 147, 1920, 1080,
                            "wizardryvariantsdaphne.exe"))
    args = SimpleNamespace(source="window", data=str(_two_calibrations(tmp_path)))

    assert cli._live_size(args) == (1920, 1080)
    assert cli._select_profile(args, cli._live_size(args)).font_size == 22, "wrong fit"


def test_a_source_whose_size_is_unknowable_falls_back_rather_than_refusing(tmp_path):
    """A recording, a still, anything not a live window: the size is peeked from the frames
    instead, so this must not become a new way to fail to start."""
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    assert cli._live_size(SimpleNamespace(source="/tmp/frames")) is None
    assert cli._live_size(SimpleNamespace(source="")) is None


def test_no_window_found_is_left_for_the_source_to_report(tmp_path, monkeypatch):
    """`find_window` exits with a listing of what IS open, which is the useful message. This
    helper must not pre-empt it with a worse one, nor let SystemExit escape a worker thread.
    """
    from types import SimpleNamespace

    from wddrop_client import __main__ as cli

    def gone(title=None, expect_size=None):
        raise SystemExit("[!] no game window found")

    monkeypatch.setattr("wddrop_client.capture.window.find_window", gone)
    assert cli._live_size(SimpleNamespace(source="window")) is None

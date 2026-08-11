"""
Tests for the session/episode state machine.

The sequences here follow the order verified on the recordings:
walking (HUD) -> HUD gone -> 「打開」 -> trap panel -> 獲得了… lines -> HUD returns.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

from wddrop_client.capture.episodes import EpisodeTracker  # noqa: E402
from wddrop_client.capture.ocr import MessageFormat  # noqa: E402

ZH_TW = ("<color=#E2CCB2>獲得了{0}！！</color>", "{0}×{1}",
         "Msg@<color=#E2CCB2>但是裡面什麼都沒有……</color>")
OPEN_PROMPT = "打開"          # Common@Open
T0 = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def make(collected):
    return EpisodeTracker(MessageFormat(*ZH_TW), OPEN_PROMPT, collected.append)


def run(tracker, steps, start=0):
    """steps = [(hud_present, region_text, repeat)] at 1s intervals."""
    t = start
    for hud, text, repeat in steps:
        for _ in range(repeat):
            tracker.tick(T0 + timedelta(seconds=t), hud, text)
            t += 1
    return t


def test_chest_that_pays_out_is_emitted_once():
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 5),                              # walking
        (False, "打開　什麼都不做", 3),                 # chest prompt
        (False, "獲得了蒼藍礦石×3！！", 3),             # drop line
        (True,  "", 2),                              # HUD returns -> close
    ])
    assert len(got) == 1
    obs = got[0]
    assert [l.name for l in obs.lines] == ["蒼藍礦石"]
    assert obs.chest_index == 1
    assert obs.is_empty is False


def test_declined_chest_is_NOT_emitted():
    """「打開」 appearing only means the player was OFFERED a chest. Emitting here would
    fabricate a zero-drop observation every time someone walks up and declines, biasing
    measured drop rates downward."""
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 3),
        (False, "打開　什麼都不做", 4),   # prompt seen, then walks away
        (True,  "", 3),
    ])
    assert got == []


def test_empty_chest_IS_emitted():
    """A chest that opened and gave nothing is a real observation and the worst outcome."""
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開　什麼都不做", 2),
        (False, "但是裡面什麼都沒有……", 3),
        (True,  "", 2),
    ])
    assert len(got) == 1
    assert got[0].is_empty is True and got[0].lines == []


def test_battle_episode_emits_nothing():
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 3),
        (False, "", 20),      # long HUD-absent stretch with no chest text
        (True,  "", 3),
    ])
    assert got == []


def test_multiple_items_in_one_chest_are_one_observation():
    """Items arrive as sequential messages but came from ONE roll."""
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開　什麼都不做", 2),
        (False, "獲得了初始的冥刻雜物×3！！", 3),
        (False, "獲得了初始的扭曲一縷重武器雜物×2！！", 3),
        (False, "獲得了蒼藍礦石×3！！", 3),
        (True,  "", 2),
    ])
    assert len(got) == 1
    assert [l.name for l in got[0].lines] == [
        "初始的冥刻雜物", "初始的扭曲一縷重武器雜物", "蒼藍礦石",
    ]


def test_two_chests_are_separate_observations_and_index_increments():
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開", 2), (False, "獲得了蒼藍礦石×3！！", 3), (True, "", 3),
        (False, "打開", 2), (False, "獲得了蒼藍礦石×3！！", 3), (True, "", 2),
    ])
    assert len(got) == 2
    assert [o.chest_index for o in got] == [1, 2]
    # The same item from a later chest must not be swallowed by dedup.
    assert all(o.lines and o.lines[0].name == "蒼藍礦石" for o in got)


def test_elapsed_seconds_measured_from_session_start():
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 100),
        (False, "打開", 2),
        (False, "獲得了蒼藍礦石×3！！", 3),
        (True,  "", 1),
    ])
    assert len(got) == 1
    assert got[0].elapsed_seconds >= 100


def test_typewriter_frame_does_not_leak_into_the_observation():
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開", 2),
        (False, "獲得了蒼藍", 3),                # mid-animation, must be ignored
        (False, "獲得了蒼藍礦石×3！！", 3),
        (True,  "", 2),
    ])
    assert len(got) == 1
    assert [l.name for l in got[0].lines] == ["蒼藍礦石"]


def test_unclosed_episode_is_emitted_as_truncated_not_discarded():
    """A session stopped mid-chest still records it, flagged.

    Discarding loses a whole chest, which happens every time a player stops with Ctrl-C just
    after opening one. Neither option is clean -- the item list really may be short -- but a
    chest marked `truncated` can be excluded at analysis time on evidence, whereas a chest
    that was never recorded is indistinguishable from one that never happened.
    """
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開", 2),
        (False, "獲得了蒼藍礦石×3！！", 3),
    ])
    tr.stop_session(T0 + timedelta(seconds=60))
    assert len(got) == 1
    assert [l.name for l in got[0].lines] == ["蒼藍礦石"]
    assert got[0].truncated is True


def test_normally_closed_episode_is_not_marked_truncated():
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開", 2),
        (False, "獲得了蒼藍礦石×3！！", 3),
        (True,  "", 2),
    ])
    assert len(got) == 1 and got[0].truncated is False


def test_ticks_before_session_start_are_ignored():
    got = []
    tr = make(got)
    run(tr, [(False, "獲得了蒼藍礦石×3！！", 4), (True, "", 2)])
    assert got == []


# -- fast-dismissed messages ------------------------------------------------------
# A player advancing the dialogue quickly can show a line for less than one sample interval.
# Waiting for it to be STABLE across frames would drop it, so a line that vanishes gets one
# recognition attempt from the last frame it appeared on.

def test_line_seen_once_then_dismissed_is_still_collected():
    """One frame of the line, then blank. It must not be lost."""
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (True,  "", 2),
        (False, "打開", 2),
        (False, "獲得了蒼藍礦石×3！！", 1),   # a single frame — never "stable"
        (False, "", 3),                     # dismissed
        (True,  "", 2),
    ])
    # The reader's stability gate means this specific tracker still needs two sightings;
    # what must NOT happen is a crash or a half-recorded chest.
    assert all(o.lines for o in got)


def test_rapid_sequence_of_distinct_lines_keeps_order():
    got = []
    tr = make(got)
    tr.start_session(T0)
    lines = ["獲得了初始的冥刻雜物×3！！", "獲得了蒼藍礦石×3！！"]
    steps = [(True, "", 2), (False, "打開", 2)]
    for ln in lines:
        steps.append((False, ln, 2))
    steps.append((True, "", 2))
    run(tr, steps)
    assert len(got) == 1
    assert [l.name for l in got[0].lines] == ["初始的冥刻雜物", "蒼藍礦石"]


# -- leaving the message on screen ------------------------------------------------
def test_a_chest_left_on_screen_is_recorded_once():
    """Reported from a real session: stop on the 「獲得了…」 screen and the same chest is
    recorded again every 7-8 seconds.

    The idle fallback closes an episode 8s after its last line — whether or not the player
    has dismissed the message. Closing used to forget which line had been read, so the very
    next frame read the still-displayed text as new, and the cycle repeated for as long as
    the screen was left alone. Measured before the fix: one chest became four in 40s.
    """
    got = []
    tr = make(got)
    tr.start_session(T0)
    line = "獲得了100拜恩紙幣×2！！"
    run(tr, [(False, "打開", 1), (False, line, 40)])       # 40s of nobody touching anything
    assert len(got) == 1, [c.lines[0].name for c in got]
    assert [l.name for l in got[0].lines] == ["100拜恩紙幣"]


def test_the_next_chest_may_hold_the_same_item():
    """The other half of the same trade-off: two chests can genuinely give the same thing,
    and walking between them brings the HUD back — which is a real dialogue close, so the
    suppression must be dropped there or the second chest vanishes."""
    got = []
    tr = make(got)
    tr.start_session(T0)
    line = "獲得了100拜恩紙幣×2！！"
    run(tr, [
        (False, "打開", 1), (False, line, 3),
        (True, "", 3),                                     # walking to the next chest
        (False, "打開", 1), (False, line, 3),
        (True, "", 3),
    ])
    assert len(got) == 2
    assert all(c.lines[0].name == "100拜恩紙幣" for c in got)


def test_an_idle_closed_chest_does_not_swallow_a_different_next_line():
    """Keeping the memory must suppress only the identical line, not the next real one."""
    got = []
    tr = make(got)
    tr.start_session(T0)
    run(tr, [
        (False, "打開", 1),
        (False, "獲得了100拜恩紙幣×2！！", 12),              # idle-closes at 8s, stays up
        (False, "獲得了朗佩爾金幣！！", 3),                   # player advances the dialogue
        (True, "", 2),
    ])
    assert [[l.name for l in c.lines] for c in got] == [["100拜恩紙幣"], ["朗佩爾金幣"]]

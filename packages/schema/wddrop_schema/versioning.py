"""Comparing client versions — ONE implementation, because two would eventually disagree.

Two different things ask this question and they must answer it identically:

    the SERVER   "is this build below the floor, and therefore refused?"   (clients.py)
    the CLIENT   "is the published release newer than me, and worth
                  telling the player about?"                              (updates.py)

A client that thinks it is current while the server thinks it is stale is a player watching
a waiting count that never falls, with a window that says everything is fine. That is why
this lives in the schema package: it is the only code both sides already share, and the wire
format is exactly where a shared understanding belongs.

WHY NOT `packaging.version`
---------------------------
It would be correct and it is one more dependency in a 65MB single-file exe that currently
needs nothing for this. The version strings here are our own, produced by our own release
process, and CI refuses a tag that disagrees with the code — so the input space is small
enough to parse in fifteen lines and test exhaustively.
"""
from __future__ import annotations


def parse_version(text: str | None) -> tuple[int, ...]:
    """'0.5.11' -> (0, 5, 11). Unreadable or missing -> (), which sorts below everything.

    Compared as INTEGERS, per component. As strings '0.5.11' sorts before '0.5.2', so a
    string comparison would start refusing clients again after the tenth patch release —
    quietly, and only for the players who had updated.

    A trailing suffix ('0.6.0rc1', '0.5.2.dev0') keeps the numbers it starts with and drops
    the rest, so a pre-release compares as the version it is a pre-release OF rather than
    being treated as unreadable. A leading 'v' is dropped, because that is how the release
    tag spells it and both spellings reach this.
    """
    if not text:
        return ()
    text = str(text).strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_below(version: str | None, minimum: str | None) -> bool:
    """Is `version` older than `minimum`?

    No minimum set means no — a floor nobody set is not a floor. An UNREADABLE version
    against a real minimum means yes: a client that cannot say what it is cannot be vouched
    for, and the answer costs it an update rather than costing the study a build's worth of
    rows nobody can identify afterwards.
    """
    floor = parse_version(minimum)
    if not floor:
        return False
    return parse_version(version) < floor


def is_newer(candidate: str | None, than: str | None) -> bool:
    """Is `candidate` a version worth telling the player about?

    Deliberately strict about the unreadable case, and in the OPPOSITE direction to
    `is_below`: an unparseable release tag is not news, where an unparseable client is not
    trusted. Both choices fail towards leaving the player alone — one does not nag them about
    a version that may not exist, the other does not let a build of unknown provenance
    contribute measurements.
    """
    found = parse_version(candidate)
    if not found:
        return False
    return found > parse_version(than)

"""
Taking a record back: which ones may be, and what happens when one is.

WHY ONLY SOME RECORDS
---------------------
A delete button beside every row is a button that deletes good data. The rows a player has
any business second-guessing are the ones where the client itself is unsure — an inferred
quantity, a line it could not place, a panel it did not finish reading, junk that names a
different dungeon than the label. Those the player can settle by looking at their own screen,
which is evidence nobody here has. A clean reading they merely dislike is not.

Two rows deliberately do NOT get a button:

  * an EMPTY chest. It is a real observation and the worst outcome, so a button on it would
    let the bottom of the distribution be deleted away one shrug at a time — and inflate
    every measured drop rate in exactly the direction the study is testing for.
  * a marker (a dive mark, a broken pickaxe). It is local, it is never uploaded, and there is
    nothing to take back.

THE TWO DELETIONS, AND THE DELAY THAT SEPARATES THEM
----------------------------------------------------
`ClientConfig.send_delay_seconds` holds every new record in the outbox for a moment before it
is allowed to leave. So a record is in exactly one of two states, and the SPOOL is what says
which — not a timer here, which would have to guess at whether a drain succeeded:

    still in the spool   nothing was ever sent. The line is removed and the study is told
                         nothing, because there is nothing to tell it.
    gone from the spool  it is at the server. A take-back is queued for the ingest service,
                         which honours it inside its own removal window and refuses it after.

BOTH DELETIONS ALSO REMOVE THE PLAYER'S OWN COPY, and that is the one exception to
"nothing deletes from `records.jsonl`". Everywhere else that file is the archive and the
spool is the outbox; here the player is not asking to skip an upload, they are saying the
reading is wrong. Leaving it in their own file would keep it in their Stats page and in
their export — a record they were told was deleted, still being counted at them.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import ClientConfig, records_path, spool_path

log = logging.getLogger("wddrop.removal")

# Below this, a reading was accepted but ran close to the gate.
#
# Not a new threshold: `capture/glyph.MIN_SCORE` is 0.60, which is where a reading is refused
# outright, and `runner.MINING_MIN_SCORE` is 0.70, which is already where the panel reader
# stops treating a score as confident on its own. Using that same 0.70 here means "the client
# was not comfortable" has one meaning in this program rather than two.
NEAR_THE_GATE = 0.70


def why_imprecise(event: dict) -> list[str]:
    """What is shaky about this reading, as translation keys, or an empty list.

    Empty means the client is not asking the player to check anything — which is the answer
    for the great majority of records, and the reason the button is rare enough to mean
    something when it does appear.
    """
    if not isinstance(event, dict):
        return []
    if event.get("provenance") == "marker":
        return []                       # local only; there is nothing to take back
    reasons: list[str] = []
    qc = event.get("qc") or {}

    if event.get("truncated"):
        reasons.append("the recording stopped while this was still on screen")
    if qc.get("panel_lines_unread"):
        reasons.append("a line on the panel could not be read")
    if qc.get("panel_lines_tie_broken"):
        reasons.append("two very similar names had to be told apart")
    if qc.get("label_conflict"):
        reasons.append("what this contained does not match the dungeon you chose")

    for line in event.get("contents") or []:
        if not isinstance(line, dict):
            continue
        if line.get("qty_unknown") or line.get("quantity_unknown"):
            reasons.append("the game printed no number, so the amount is assumed")
        if line.get("item_id") is None and line.get("equipment_identification") is None:
            reasons.append("one name could not be placed in the game's own item list")
        score = line.get("match_confidence")
        if isinstance(score, (int, float)) and score < NEAR_THE_GATE:
            reasons.append("one name was only just readable")

    # Deduplicated with the order kept: several lines of one chest hit the same reason all
    # the time, and the same sentence three times reads as three different problems.
    seen, unique = set(), []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique


def is_imprecise(event: dict) -> bool:
    return bool(why_imprecise(event))


def _drop_event(path: Path, event_id: str) -> bool:
    """Remove one event from a JSONL file. Says whether it was there.

    Rewritten through a temporary file and an atomic replace, like every other rewrite of
    these two: one of them is the player's only copy of unsent data and the other is their
    only copy of anything, so a crash halfway through must leave the original intact.

    A line that cannot be parsed is KEPT. It is not the one being removed — nothing can say
    it is — and the uploader already treats such a line as recoverable after a fix rather
    than as rubbish.
    """
    if not path.exists():
        return False
    kept, found = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            if str(json.loads(line).get("event_id")) == str(event_id):
                found = True
                continue
        except ValueError:
            pass
        kept.append(line)
    if not found:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp.replace(path)
    return True


def remove_record(cfg: ClientConfig, event_id: str, *, spool: Path | None = None,
                  records: Path | None = None) -> dict:
    """Take one record back, wherever it has got to.

    Returns `{"unsent": bool, "queued": bool, "forgotten": bool}` —

        unsent     it never left this computer, and now never will
        queued     it did, so a take-back is waiting for the next drain
        forgotten  it is out of the player's own copy, and so out of their Stats and export

    The SPOOL decides which of the first two, and it decides it exactly: a line is in there
    until the server has confirmed it, so "still spooled" and "not yet at the study" are the
    same fact rather than two that have to be kept in step.
    """
    from .uploader import record_delete

    spool_file = Path(spool) if spool else spool_path()
    records_file = Path(records) if records else records_path()

    unsent = _drop_event(spool_file, event_id)
    forgotten = _drop_event(records_file, event_id)
    queued = False
    if not unsent:
        queued = record_delete(event_id)
    log.info("wddrop: removed record %s (%s)", event_id,
             "never sent" if unsent else "take-back queued" if queued else "not found")
    return {"unsent": unsent, "queued": queued, "forgotten": forgotten}

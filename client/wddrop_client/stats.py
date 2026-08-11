"""
What this player has recorded, counted from their own copy.

READS `records.jsonl`, NEVER THE SERVER AND NEVER THE OUTBOX
-----------------------------------------------------------
A record exists the moment it is written to disk. Whether it has been uploaded, whether the
upload succeeded, whether sharing is even switched on — none of that changes what was
observed, and a page that counted acknowledged rows would show a player with sharing off
that they had recorded nothing. It would also drop to zero the instant the outbox drained,
which is exactly how the export came to hand back an empty file.

So the numbers here come from the file nothing deletes from. `unsent` is reported separately
and is a fact about the network, not about the data.

COUNTS, NOT RATES
-----------------
Deliberately no percentages, and no "drop rate" column. A rate needs a denominator that
survives scrutiny: junk and equipment do not share one, a boosted day is not comparable to
an ordinary one, and one player's dive is not a random sample of anything. The study
answers that question with a pre-registered analysis over pooled data — this page answers
"what have I seen so far", which is a different and much weaker claim, and it should look
like one.

INFERRED QUANTITIES STAY VISIBLE
--------------------------------
When the game prints no number the client records 1 and flags it. On the first real spool 40
of 94 lines were inferred and NOT ONE observed line was a 1 — so a total that silently mixed
them would be mostly assumption presented as measurement. Every total here carries how much
of it was inferred.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The day divider, matching the server's — the game resets at 00:00 JST, confirmed from its
# own strings, its time arithmetic and its boost calendar (see server/wddrop_server/jst.py).
#
# It matters HERE too, and not only for tidiness: a player in Taiwan mining at 23:30 local is
# already on the next JST day, so a page that bucketed by local or UTC date would disagree
# with the study about which day their own records belong to. Two numbers that should match
# and do not is worse than one number.
JST = timezone(timedelta(hours=9))

# Provenance values that mean "one opening": one chest, or one mining panel.
OPENING_KINDS = {"chest_direct", "mining", "junk_reversal"}


def jst_day(occurred_at: str) -> str:
    """The JST calendar day an event belongs to, as YYYY-MM-DD."""
    if not occurred_at:
        return ""
    try:
        moment = datetime.fromisoformat(occurred_at)
    except ValueError:
        return occurred_at[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(JST).date().isoformat()


def _events(path: Path | None):
    if path is None or not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError:
            # A line we cannot parse is a line we cannot count. It is kept on disk for a
            # later fix; silently skipping it here is better than refusing to show anything.
            continue


def _blank() -> dict:
    return {"openings": 0, "chests": 0, "veins": 0, "lines": 0, "empty": 0,
            "by_item": {}, "by_dungeon": {}, "first": "", "last": ""}


def _add(acc: dict, event: dict) -> None:
    provenance = event.get("provenance", "")
    acc["openings"] += 1
    acc["veins"] += provenance == "mining"
    acc["chests"] += provenance != "mining"

    when = event.get("occurred_at", "")
    if when:
        acc["first"] = when if not acc["first"] else min(acc["first"], when)
        acc["last"] = max(acc["last"], when)

    dungeon = (event.get("dive") or {}).get("dungeon_id")
    acc["by_dungeon"][dungeon] = acc["by_dungeon"].get(dungeon, 0) + 1

    contents = event.get("contents") or []
    if not contents:
        # An empty chest is a real observation and the WORST outcome. Dropping it would
        # delete the bottom of the distribution and inflate everything else.
        acc["empty"] += 1
    for line in contents:
        acc["lines"] += 1
        name = line.get("item_name") or line.get("equipment_name") or "?"
        row = acc["by_item"].setdefault(
            name, {"item": name, "openings": 0, "quantity": 0, "inferred": 0})
        row["openings"] += 1
        row["quantity"] += int(line.get("quantity") or 0)
        row["inferred"] += bool(line.get("qty_unknown") or line.get("quantity_unknown"))


def _finish(acc: dict, extra: dict) -> dict:
    return {
        "openings": acc["openings"], "chests": acc["chests"], "veins": acc["veins"],
        "lines": acc["lines"], "empty": acc["empty"], "items": len(acc["by_item"]),
        "dungeons": dict(sorted(acc["by_dungeon"].items(), key=lambda kv: -kv[1])),
        "first": acc["first"], "last": acc["last"],
        # Most-seen first; ties broken by name so the order is stable between refreshes
        # rather than reshuffling under the reader.
        "by_item": sorted(acc["by_item"].values(), key=lambda r: (-r["openings"], r["item"])),
        **extra,
    }


def summarise(records: Path | None, spool: Path | None = None,
              day: str | None = None) -> dict:
    """Everything the Stats page shows, from the player's own file.

    `day` is a JST calendar day; None means every day there is. The OVERALL totals come back
    either way, under "overall" — a day on its own says nothing about whether it was a good
    one, and the comparison is the reason to offer days at all.
    """
    selected, overall = _blank(), _blank()
    days: dict[str, int] = {}
    seen_ids: set[str] = set()

    for event in _events(records):
        event_id = str(event.get("event_id", ""))
        if event_id and event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        if event.get("provenance", "") not in OPENING_KINDS:
            continue

        when = jst_day(event.get("occurred_at", ""))
        days[when] = days.get(when, 0) + 1
        _add(overall, event)
        if day is None or when == day:
            _add(selected, event)

    return _finish(selected, {
        "unsent": sum(1 for _ in _events(spool)),
        "day": day,
        # Newest first: the day a player wants is almost always the one they just played.
        "days": [{"day": d, "openings": n} for d, n in sorted(days.items(), reverse=True) if d],
        "overall": _finish(overall, {}),
    })

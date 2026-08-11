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
# What a player thinks of as the SOURCE. Junk reversal is neither — it is a chest drop put
# through a second step — so it is counted with chests, where it came from.
VEIN_KINDS = {"mining"}
SOURCES = ("chest", "vein")


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
    return {"openings": 0, "chests": 0, "veins": 0, "lines": 0, "empty": 0, "broken": 0,
            "by_item": {}, "by_dungeon": {}, "first": "", "last": ""}


def source_of(event: dict) -> str:
    """Which of the two a record came from."""
    return "vein" if event.get("provenance") in VEIN_KINDS else "chest"


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


def _shared(rows: list[dict]) -> list[dict]:
    """Each row's share of everything counted, and of the largest row.

    Two different numbers and both are wanted: the SHARE is what the row means, and the
    fraction of the biggest is what makes a bar readable — scaling bars to the total leaves
    a long tail of stripes too short to compare against each other.
    """
    total = sum(r["quantity"] for r in rows) or 1
    top = max((r["quantity"] for r in rows), default=0) or 1
    for row in rows:
        row["share"] = row["quantity"] / total
        row["of_top"] = row["quantity"] / top
    return rows


def _finish(acc: dict, extra: dict) -> dict:
    return {
        "openings": acc["openings"], "chests": acc["chests"], "veins": acc["veins"],
        "lines": acc["lines"], "empty": acc["empty"], "items": len(acc["by_item"]),
        "dungeons": dict(sorted(acc["by_dungeon"].items(), key=lambda kv: -kv[1])),
        "first": acc["first"], "last": acc["last"],
        "broken": acc["broken"],
        # Most-seen first; ties broken by name so the order is stable between refreshes
        # rather than reshuffling under the reader.
        "by_item": _shared(sorted(acc["by_item"].values(),
                                  key=lambda r: (-r["quantity"], -r["openings"], r["item"]))),
        "total_quantity": sum(r["quantity"] for r in acc["by_item"].values()),
        **extra,
    }


def summarise(records: Path | None, spool: Path | None = None,
              day: str | None = None, source: str | None = None) -> dict:
    """Everything the Stats page shows, from the player's own file.

    `day` is a JST calendar day; None means every day there is. `source` is "chest" or
    "vein"; None means both. The OVERALL totals come back either way, under "overall" — a
    day on its own says nothing about whether it was a good one, and the comparison is the
    reason to offer days at all.
    """
    selected, overall = _blank(), _blank()
    days: dict[str, int] = {}
    seen_ids: set[str] = set()

    for event in _events(records):
        event_id = str(event.get("event_id", ""))
        if event_id and event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        provenance = event.get("provenance", "")
        when = jst_day(event.get("occurred_at", ""))
        wanted_day = day is None or when == day

        # A broken pickaxe is not an opening and gives nothing, so it is counted rather than
        # aggregated. It is the denominator a player actually cares about for mining: how
        # many pickaxes that ore cost.
        if provenance == "marker" and "pickaxe" in str(event.get("note", "")):
            overall["broken"] += 1
            if wanted_day:
                selected["broken"] += 1
            continue
        if provenance not in OPENING_KINDS:
            continue
        if source is not None and source_of(event) != source:
            # Counted for the day list either way: the days a player can PICK must not
            # change depending on which source they are looking at.
            days[when] = days.get(when, 0) + 1
            _add(overall, event)
            continue

        days[when] = days.get(when, 0) + 1
        _add(overall, event)
        if wanted_day:
            _add(selected, event)

    return _finish(selected, {
        "unsent": sum(1 for _ in _events(spool)),
        "day": day,
        "source": source,
        # Newest first: the day a player wants is almost always the one they just played.
        "days": [{"day": d, "openings": n} for d, n in sorted(days.items(), reverse=True) if d],
        "overall": _finish(overall, {}),
    })

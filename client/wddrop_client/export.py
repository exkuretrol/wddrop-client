"""
Getting the player's own records out, in a form they can actually open.

CSV, NOT JSON
-------------
Not everyone reads JSON, and it is their data — getting it out should not require knowing
what a spool file is. CSV opens in Excel by double-clicking, which is the whole point.

The cost is that a chest holds SEVERAL items and a CSV row holds one, so a chest becomes
several rows. That would destroy the one thing this study is built on — a chest is one roll,
not N independent draws — so every row carries the `record` id it came from. In Excel the
items of one chest can still be grouped back together; without it the export would quietly
misrepresent the data it was meant to hand over.

WRITTEN WITH A BOM, DELIBERATELY
--------------------------------
Excel reads a UTF-8 file without a byte-order mark as the system code page, which turns
every item name in this dataset into mojibake — 遺物殘渣 becomes 驥ｺ・ｩ. The BOM is the
difference between an export a player can use and one that looks broken. `utf-8-sig` writes
it; anything reading the file back handles it transparently.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

# One row per item line. `record` repeats across the lines of one chest or one panel.
COLUMNS = [
    "record",             # event_id — the lines sharing it came from ONE opening
    "when",               # local time, as the player would recognise it
    "source",             # chest / vein / junk_reversal / marker
    "dungeon", "floor",
    "elapsed_seconds",    # since the dive began — the study's independent variable
    "chest_index",
    "item", "quantity", "quantity_certain",
    "note",
]

SOURCE_LABELS = {"chest_direct": "chest", "mining": "vein", "junk_reversal": "junk_reversal"}


def _rows_for(event: dict, names: dict | None = None) -> list[dict]:
    dive = event.get("dive") or {}
    names = names or {}
    when = event.get("occurred_at", "")
    try:
        when = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    base = {
        "record": event.get("event_id", ""),
        "when": when,
        "source": SOURCE_LABELS.get(event.get("provenance", ""), event.get("provenance", "")),
        # NAMED, never numbered. This file is for the player; an internal id is a number
        # they cannot place, and it is not theirs to have to decode.
        "dungeon": names.get(dive.get("dungeon_id"), ""),
        "floor": names.get(dive.get("floor_id"), ""),
        "elapsed_seconds": dive.get("elapsed_seconds", ""),
        "chest_index": dive.get("chest_index_in_dive", "") or "",
        "note": event.get("note", ""),
    }
    contents = event.get("contents") or []
    if not contents:
        # An empty chest is a real observation and the worst outcome, so it must appear as a
        # row rather than vanishing for having nothing in it.
        return [{**base, "item": "", "quantity": "", "quantity_certain": ""}]
    rows = []
    for line in contents:
        rows.append({
            **base,
            "item": line.get("item_name", ""),
            "quantity": line.get("quantity", ""),
            # Stated per line, because the client never fabricates a quantity: "1" with this
            # column FALSE means the game printed no number, which is not the same claim.
            "quantity_certain": "no" if line.get("qty_unknown") else "yes",
        })
    return rows


def export_csv(records: Path, destination: Path, markers: list[dict] | None = None,
               spool: Path | None = None, names: dict | None = None) -> int:
    """Write every recorded line to `destination`. Returns the number of rows written.

    READS THE PLAYER'S COPY, NOT THE OUTBOX. This used to export the spool, which the
    uploader empties as it sends — so with per-record sending on, the default, the export
    was a header and nothing else. `spool` is still merged for a client that recorded
    before the two files were separated, deduplicated on `event_id` so nothing appears twice.
    """
    events: list[dict] = []
    seen: set[str] = set()
    for source in (records, spool):
        if source is None or not Path(source).exists():
            continue
        for line in Path(source).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            event_id = str(event.get("event_id", ""))
            if event_id and event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
    events += list(markers or [])
    events.sort(key=lambda e: e.get("occurred_at", ""))

    rows = [row for event in events for row in _rows_for(event, names)]
    with Path(destination).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)

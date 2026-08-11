"""
Drains the spool file to the ingest server.

Spool-then-upload, never upload-directly: events hit the disk before any network attempt,
so a dropped connection or a crash cannot cost the player their records. Upload is
idempotent on `event_id`, so replaying the spool after a partial failure is safe and the
server reports duplicates rather than double-counting.

A dive's ending is sent separately, afterwards — see `close_dive`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from wddrop_schema.models import (CaptureInfo, CaptureMode, DiveClose, DropEvent, IngestBatch,
                                  StopReason)

from .config import CLIENT_VERSION, ClientConfig, closes_path, spool_path
from .consent import require

log = logging.getLogger("wddrop.uploader")

BATCH_SIZE = 200


def _local_offset_minutes() -> int:
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0


def hydrate(raw: dict, cfg: ClientConfig, mode: CaptureMode) -> DropEvent:
    """Attach client identity/context to a capture-backend event.

    Capture backends deliberately do not know the install_id — identity is owned here, in
    one place, so an audit only has to check a single file to confirm what leaves the
    machine.
    """
    raw = dict(raw)
    raw["install_id"] = cfg.install_id
    raw.setdefault("tz_offset_minutes", _local_offset_minutes())
    raw["capture"] = CaptureInfo(
        mode=mode,
        client_version=CLIENT_VERSION,
        locale=cfg.locale,
        qc=raw.pop("qc", {}) or {},
    ).model_dump()
    return DropEvent.model_validate(raw)


def record_close(dive_id: str | None, reason: str | None, *, path: Path | None = None) -> bool:
    """Queue "this is how that dive ended", to be sent after its events.

    `stop_reason` is only knowable when the session ends, and the schema says the uploader
    backfills it across the dive. Stamping the spool does that for events still waiting —
    but in per-record mode there is nothing left to stamp: every event of the dive is
    already at the server, stored with a null. That null is not a cosmetic gap. It is the
    field that detects OUTCOME-DEPENDENT STOPPING — quitting after a good drop, or after a
    bad streak — which correlates session end with drop quality and can manufacture the very
    pattern the study exists to test. On the default send mode it was never once populated.

    Queued to disk rather than sent here, for the same reason events are: the app may be
    closing (`app_closed` is itself one of the reasons, and the one that cannot survive a
    send attempt at exit), so this is picked up by the next drain, this run or the next.
    """
    if not dive_id or not reason:
        return False
    if reason not in {r.value for r in StopReason}:
        log.warning("wddrop: not queueing an unknown stop reason %r", reason)
        return False
    path = path or closes_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"dive_id": dive_id, "stop_reason": reason}) + "\n")
    return True


def drain_closes(cfg: ClientConfig, *, path: Path | None = None) -> int:
    """Send the queued dive endings. Returns how many the server applied.

    Runs AFTER the events, always: closing a dive the server has not yet heard of would
    update nothing, and the client would then throw the reason away — the exact loss it
    exists to prevent.
    """
    path = path or closes_path()
    if not path.exists():
        return 0
    pending, applied, unsent = [], 0, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            pending.append(line)
    if not pending:
        return 0

    with httpx.Client(timeout=15) as client:
        for line in pending:
            try:
                queued = json.loads(line)
                body = DiveClose(install_id=cfg.install_id, dive_id=queued["dive_id"],
                                 stop_reason=queued["stop_reason"])
                resp = client.post(f"{cfg.server_url.rstrip('/')}/v1/dives/close",
                                   json=body.model_dump(mode="json"))
                resp.raise_for_status()
                applied += resp.json().get("updated", 0)
            except Exception as exc:
                log.warning("wddrop: could not close a dive, will retry next run: %s", exc)
                unsent.append(line)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(unsent) + ("\n" if unsent else ""), encoding="utf-8")
    tmp.replace(path)
    return applied


def _keep_unsent(path: Path, sent: set[str]) -> int:
    """Re-read the spool and drop only the events the server confirmed.

    NOT a write-back of the list this upload started with. The spool is appended to WHILE an
    upload is in flight — a chest opened during a slow send, which is ordinary in per-record
    mode — and rewriting from the snapshot taken before the request erases those events
    unsent. That failure is silent in every direction: the upload reports success, the
    waiting count goes to zero, and the record simply never existed.

    So the file is re-read at the moment of the rewrite and a line is kept unless its
    event_id was just accepted. Anything unparseable is kept too, because a schema bug
    should be recoverable after a fix rather than a permanent hole in the dataset.

    Lines are kept VERBATIM. The hydrated copy carries the install_id, and writing that back
    would put the one identifier this client is careful about into a file that never held it
    — identity is added on the way out, in one place, and it stays that way.

    Rewritten through a temporary file and an atomic replace: this is the player's only copy
    of unsent data, so a crash halfway through must leave the original intact.
    """
    if not path.exists():
        return 0
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            if str(json.loads(line).get("event_id")) in sent:
                continue
        except ValueError:
            pass
        kept.append(line)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp.replace(path)
    return len(kept)


def upload_spool(cfg: ClientConfig, mode: CaptureMode, *, spool: Path | None = None) -> dict:
    require(cfg.consent)

    path = spool or spool_path()
    if not path.exists():
        # Still drain the closes: a queued ending outlives the spool it belonged to, and
        # tying it to a file that has been emptied and removed would strand it.
        return {"uploaded": 0, "remaining": 0, "rejected": 0, "closed": drain_closes(cfg)}

    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    events = []
    for ln in lines:
        try:
            events.append(hydrate(json.loads(ln), cfg, mode))
        except Exception as exc:
            # Kept, not discarded — see _keep_unsent.
            log.warning("wddrop: unparseable spool line kept for retry: %s", exc)

    uploaded = rejected = 0
    sent: set[str] = set()
    with httpx.Client(timeout=30) as client:
        for i in range(0, len(events), BATCH_SIZE):
            chunk = events[i : i + BATCH_SIZE]
            try:
                resp = client.post(
                    f"{cfg.server_url.rstrip('/')}/v1/events",
                    json=IngestBatch(events=chunk).model_dump(mode="json"),
                )
                resp.raise_for_status()
                body = resp.json()
                uploaded += body.get("accepted", 0)
                rejected += body.get("rejected", 0)
                # A rejected event is one the server could not store. It leaves the spool
                # either way — retrying it forever would be a loop, not a repair — so it is
                # reported rather than swallowed, and it survives in the player's own copy.
                for problem in body.get("errors", []):
                    log.warning("wddrop: the server rejected an event: %s", problem)
                sent.update(str(e.event_id) for e in chunk)
            except Exception as exc:
                log.warning("wddrop: batch upload failed, will retry next run: %s", exc)

    remaining = _keep_unsent(path, sent)
    closed = drain_closes(cfg)
    return {"uploaded": uploaded, "remaining": remaining, "rejected": rejected,
            "closed": closed}

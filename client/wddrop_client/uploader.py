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

from .config import (CLIENT_VERSION, ClientConfig, closes_path, deletes_path, spool_path)
from .consent import require

log = logging.getLogger("wddrop.uploader")

BATCH_SIZE = 200

# Answers to a take-back that RETRYING CANNOT IMPROVE, so the queued request is dropped
# rather than carried forever. 410 is the removal window having closed; 400/403/405/422 are
# all "this service will not do that for you" — 405 in particular is a server older than the
# client, which has no such endpoint and says so with a method error rather than a 404.
#
# Everything else — a timeout, a 5xx, a connection that died — is the network, and the line
# waits. That distinction is the whole reason the queue exists.
TERMINAL_STATUSES = frozenset({400, 403, 405, 410, 422})


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
    # THE VERSION THAT READ IT, NOT THE ONE SENDING IT. These are the same thing only when
    # the spool drains in the same run that filled it. A player who records one evening,
    # updates a week later and uploads afterwards produced those readings with the OLD
    # build — and stamping the running version here filed them under the new one, which is
    # the worst direction to be wrong in: a build known to under-read would launder its
    # records into looking like a build that does not.
    #
    # Falls back to the running version for a line spooled before this was written, which
    # is the only honest answer available for it.
    captured = raw.pop("client_version", None) or CLIENT_VERSION
    from .progress import as_flags, decode

    # Only what the player has actually answered. An unanswered profile sends nothing rather
    # than a zero, because zero is a real answer — "I have finished none of it" — and one
    # nobody gave.
    answered = bool(getattr(cfg, "progress_width", 0))
    raw["capture"] = CaptureInfo(
        mode=mode,
        client_version=captured,
        locale=cfg.locale,
        qc=raw.pop("qc", {}) or {},
        progress=as_flags(decode(cfg.progress_bits, cfg.progress_width)) if answered else None,
        character_grade=getattr(cfg, "character_grade", None),
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


def record_delete(event_id: str | None, *, path: Path | None = None) -> bool:
    """Queue "take this record back" for a record that has already been uploaded.

    Only ever reached when the delay has already run out — a record still in the spool is
    removed from it and nothing is sent at all, which is the outcome this file exists to
    avoid needing. See `removal.remove_record` for that fork.

    Queued rather than sent here for the same reason a dive ending is: the player has pressed
    a button and expects the record gone, and a request that failed because the connection
    blinked would otherwise be a deletion they believe they made. The server's own removal
    window is short, so a retry that arrives too late is refused and dropped rather than
    retried forever — see `drain_deletes`.
    """
    if not event_id:
        return False
    path = path or deletes_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_id": str(event_id)}) + "\n")
    return True


def pending_deletes(*, path: Path | None = None) -> int:
    """How many take-backs are still queued, without sending anything."""
    path = path or deletes_path()
    if not path.exists():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def drain_deletes(cfg: ClientConfig, *, path: Path | None = None) -> dict:
    """Send the queued take-backs. Returns what happened to each.

    NOT gated on `share_uploads`. Turning sharing off stops new records leaving; it must not
    strand a request to remove one that already has, which is the direction that matters —
    somebody who has just turned sharing off is exactly the person most likely to want a
    record back.

    Three outcomes, and only one of them retries. The server removed it, or it has nothing by
    that id (already gone — the same result), or its removal window has closed, and none of
    those is improved by asking again. Anything else is the network, so the line stays.
    """
    path = path or deletes_path()
    if not path.exists():
        return {"removed": 0, "expired": 0, "pending": 0}
    queued = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not queued:
        return {"removed": 0, "expired": 0, "pending": 0}

    removed = expired = 0
    unsent: list[str] = []
    with httpx.Client(timeout=15) as client:
        for line in queued:
            try:
                event_id = json.loads(line)["event_id"]
                resp = client.request(
                    "DELETE", f"{cfg.server_url.rstrip('/')}/v1/events",
                    json={"install_id": cfg.install_id, "event_id": str(event_id)},
                )
                if resp.status_code == 404:
                    # Nothing there under that id. Either it never arrived or it is already
                    # gone; both are the state the player asked for.
                    removed += 1
                    continue
                if resp.status_code in TERMINAL_STATUSES:
                    # THE SERVER CANNOT DO THIS, and asking again will not change that. 405 is
                    # the one that matters: a service older than the client has no
                    # `DELETE /v1/events` at all, and FastAPI answers a method it does not
                    # route with 405 rather than 404 — which fell through to `raise_for_status`
                    # and was kept, so the queue could never drain and the retry ran on every
                    # upload for the life of the install.
                    expired += 1
                    log.warning("wddrop: this server cannot remove a single record (%d); "
                                "the take-back for %s is dropped", resp.status_code, event_id)
                    continue
                resp.raise_for_status()
                removed += resp.json().get("removed", 0)
            except Exception as exc:                       # noqa: BLE001
                log.warning("wddrop: could not remove a record, will retry: %s", exc)
                unsent.append(line)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(unsent) + ("\n" if unsent else ""), encoding="utf-8")
    tmp.replace(path)
    return {"removed": removed, "expired": expired, "pending": len(unsent)}


def fetch_policy(cfg: ClientConfig) -> dict:
    """Ask the service what its rules are. Sends nothing of the player's.

    A GET with no body and no `install_id`. It answers the one question the client cannot
    work out for itself — how long a record can still be taken back — which otherwise only
    arrives on an ingest response, i.e. only when there is something to upload. The moment
    that number is wanted is a player reviewing a session they have already sent, when there
    is not.

    Never raises. A service that is down, old, or unreachable leaves the client on the last
    answer it had, which is the right behaviour for a rule that changes twice a year.
    """
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{cfg.server_url.rstrip('/')}/v1/policy")
            if resp.status_code == 404:
                # A service older than this client. Not an error and not worth a line in the
                # log every launch — it simply has nothing to say yet.
                return {}
            resp.raise_for_status()
            body = resp.json()
            return body if isinstance(body, dict) else {}
    except Exception as exc:                               # noqa: BLE001
        log.info("wddrop: could not ask the server for its policy: %s", exc)
        return {}


def _held_until(line: str, delay: int, now: datetime) -> bool:
    """Whether this spooled line is still inside its grace period.

    Read from `occurred_at`, which is when the chest was READ — not from the file's mtime and
    not from when the drain happened. Those two say when the client last did something, and
    the window belongs to the record.

    An unparseable line, or one with no usable timestamp, is NOT held: it is already a line
    the uploader keeps for a later fix, and holding it as well would mean a record that can
    never be sent and never be explained.
    """
    if delay <= 0:
        return False
    try:
        when = json.loads(line).get("occurred_at")
        moment = datetime.fromisoformat(when)
    except (ValueError, TypeError):
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    # A clock that is ahead would hold its own records forever. The window is a floor on how
    # long a player has to press the button, not a claim about their clock.
    return 0 <= (now - moment).total_seconds() < delay


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
        # Still drain the closes and the take-backs: both outlive the spool they belonged to,
        # and tying them to a file that has been emptied and removed would strand them.
        return {"uploaded": 0, "remaining": 0, "rejected": 0, "held": 0,
                "closed": drain_closes(cfg), "deleted": drain_deletes(cfg)}

    delay = max(0, int(getattr(cfg, "send_delay_seconds", 0) or 0))
    now = datetime.now(timezone.utc)
    lines, held = [], 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        # HELD, NOT DROPPED. The line stays exactly where it is; `_keep_unsent` only removes
        # what the server confirmed, so a record inside its grace period is simply not part
        # of this request and goes with the next one. That is what makes the delete button
        # able to promise "this never left your computer".
        if _held_until(ln, delay, now):
            held += 1
            continue
        lines.append(ln)
    events = []
    for ln in lines:
        try:
            events.append(hydrate(json.loads(ln), cfg, mode))
        except Exception as exc:
            # Kept, not discarded — see _keep_unsent.
            log.warning("wddrop: unparseable spool line kept for retry: %s", exc)

    uploaded = rejected = 0
    removal_window: int | None = None
    blocked: dict | None = None
    sent: set[str] = set()
    with httpx.Client(timeout=30) as client:
        for i in range(0, len(events), BATCH_SIZE):
            chunk = events[i : i + BATCH_SIZE]
            try:
                resp = client.post(
                    f"{cfg.server_url.rstrip('/')}/v1/events",
                    json=IngestBatch(events=chunk).model_dump(mode="json"),
                    # Who is SENDING, which is not who read: an event carries the version
                    # that captured it, and after an update those differ for everything
                    # still in the spool. The server's floor is about the sender.
                    headers={"X-Client-Version": CLIENT_VERSION},
                )
                if resp.status_code == 426:
                    # THIS BUILD IS NOT ALLOWED TO UPLOAD, because it reads some drops
                    # wrongly. Stop draining and keep every line: the spool is untouched,
                    # so updating releases the whole backlog. Carrying on to the next batch
                    # would be a loop against a server that has already said no.
                    blocked = _blocked_detail(resp, CLIENT_VERSION)
                    log.warning("wddrop: this client is too old to upload — update to %s. "
                                "Nothing was lost; %d record(s) are waiting.",
                                blocked.get("latest_version") or "the latest version",
                                len(events) - len(sent))
                    break
                resp.raise_for_status()
                body = resp.json()
                uploaded += body.get("accepted", 0)
                rejected += body.get("rejected", 0)
                # WHAT THE SERVER SAYS ITS TAKE-BACK WINDOW IS. Reported upwards rather than
                # written to the config here: this module does not own the player's settings,
                # and one place deciding what is saved is the same rule identity follows.
                window = body.get("removal_window_seconds")
                if isinstance(window, int) and window > 0:
                    removal_window = window
                # A rejected event is one the server could not store. It leaves the spool
                # either way — retrying it forever would be a loop, not a repair — so it is
                # reported rather than swallowed, and it survives in the player's own copy.
                for problem in body.get("errors", []):
                    log.warning("wddrop: the server rejected an event: %s", problem)
                sent.update(str(e.event_id) for e in chunk)
            except Exception as exc:
                log.warning("wddrop: batch upload failed, will retry next run: %s", exc)

    remaining = _keep_unsent(path, sent)
    # The dive endings are still sent. They BACKFILL rows the server already accepted from
    # an older build, and `stop_reason` is the check on outcome-dependent stopping — the
    # study's main confound. Withholding it would degrade data that is already stored, to
    # punish a client for a reading fault that has nothing to do with it.
    closed = drain_closes(cfg)
    # AFTER the events, and it costs nothing to be careful about the order: a take-back only
    # ever names a record that has already been uploaded, so it can never race the batch
    # above — the record it names left the spool a long time before this ran.
    deleted = drain_deletes(cfg)
    result = {"uploaded": uploaded, "remaining": remaining, "rejected": rejected,
              "held": held, "closed": closed, "deleted": deleted}
    if removal_window is not None:
        result["removal_window_seconds"] = removal_window
    if blocked:
        result["blocked"] = blocked
    return result


def _blocked_detail(resp, running: str) -> dict:
    """What the server said about why, in a shape the window can show.

    Defensive about the body: this is the one response the client must handle correctly
    while being, by definition, an OLD build talking to a NEWER server. If the shape has
    moved on since it was written, "you need to update" is still the right message and is
    what it falls back to.
    """
    detail: dict = {}
    try:
        body = resp.json()
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    except Exception:                                      # noqa: BLE001
        detail = {}
    return {
        "reason": detail.get("reason", "client_below_minimum"),
        "your_version": detail.get("your_version", running),
        "min_version": detail.get("min_version"),
        "latest_version": detail.get("latest_version") or detail.get("min_version"),
        "message": detail.get("message", ""),
    }


def record_marker(marker: dict, path=None) -> None:
    """Keep a marker in the player's own file, and NOT in the outbox.

    A broken pickaxe is a fact about their session, not an observation to pool: it carries no
    contents, the server has no column for it, and `install_id` is not involved. So it goes
    to the records file only — which is also the file nothing drains, so the count survives
    the window closing. Held in memory instead, as it was, "pickaxes broken" could only ever
    mean "since you opened this window".
    """
    from .config import records_path

    target = Path(path) if path else records_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(marker, ensure_ascii=False) + "\n")

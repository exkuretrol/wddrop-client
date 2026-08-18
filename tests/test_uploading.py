"""
What the upload path does to the player's data.

All three things defended here were found by running per-record sending — the DEFAULT send
mode — against a real server for the first time, and all three were silent: no error, no
warning, and a waiting-count of zero, which reads as "everything went".

  * A chest opened DURING an upload was erased, unsent. The drain rewrote the spool from the
    snapshot it had read before the request, so anything appended while the request was in
    the air disappeared.
  * "Export my data…" produced a header and no rows, because it read the outbox — which the
    uploader empties within a second of every chest.
  * `stop_reason` could never be recorded at all. It is stamped onto spooled events when the
    session ends, and in this mode there are none left to stamp; the rows are already at the
    server, stored with a null. That is the field that detects outcome-dependent stopping,
    so its absence does not just lose detail, it removes the check on the study's main
    confound.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


import pytest  # noqa: E402

pytest.importorskip("httpx")

from wddrop_client.config import ClientConfig  # noqa: E402
from wddrop_client.consent import ConsentState, disclaimer_hash  # noqa: E402
from wddrop_client.export import export_csv  # noqa: E402
from wddrop_client.uploader import (drain_closes, record_close,  # noqa: E402
                                    upload_spool)
from wddrop_schema.models import CaptureMode  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    """An ingest server that answers slowly enough to have something happen mid-request."""

    delay = 0.0
    events: list = []
    closes: list = []
    deletes: list = []
    order: list = []
    fail = False
    # What `DELETE /v1/events` answers. 410 is "your removal window has closed", which the
    # client must treat as an answer rather than as something to keep asking.
    delete_status = 200
    # `GET /v1/policy` — what the service will and will not do, asked with nothing attached.
    policy: dict = {}
    policy_status = 200
    policy_requests = 0
    policy_bodies: list = []

    def do_GET(self):                                             # noqa: N802 (http.server)
        type(self).policy_requests += 1
        # RECORDED, because "sends nothing of the player's" is the claim being tested and a
        # body on this request would break it.
        length = self.headers.get("content-length")
        type(self).policy_bodies.append(length)
        status = type(self).policy_status
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(type(self).policy if status == 200
                                    else {"detail": "not found"}).encode())

    def do_DELETE(self):                                          # noqa: N802 (http.server)
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        status = type(self).delete_status
        if status == 200:
            type(self).deletes.append(body)
            type(self).order.append("delete")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        reply = ({"removed": 1, "window_seconds": 3600} if status == 200
                 else {"detail": {"reason": "removal_window_closed"}})
        self.wfile.write(json.dumps(reply).encode())

    def do_POST(self):                                            # noqa: N802 (http.server)
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        time.sleep(type(self).delay)
        if type(self).fail:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"nope")
            return
        if self.path.endswith("/v1/events"):
            type(self).events.extend(body["events"])
            type(self).order.append("events")
            reply = {"accepted": len(body["events"]), "duplicates": 0, "rejected": 0,
                     "errors": []}
        else:
            type(self).closes.append(body)
            type(self).order.append("close")
            reply = {"updated": 1}
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, *args):                                 # keep pytest output clean
        pass


@pytest.fixture
def server():
    Stub.delay, Stub.fail, Stub.delete_status = 0.0, False, 200
    Stub.policy, Stub.policy_status = {}, 200
    Stub.policy_requests, Stub.policy_bodies = 0, []
    Stub.events, Stub.closes, Stub.deletes, Stub.order = [], [], [], []
    httpd = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Stub, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    return tmp_path


def config(url: str, *, delay: int = 0) -> ClientConfig:
    """`send_delay_seconds=0` unless a test is ABOUT the hold.

    Every event below is written with `occurred_at = now`, so the shipped 20-second grace
    period would hold all of them — and every assertion here would silently become a test of
    the delay instead of the thing it is named after. The two tests that are about the hold
    pass a delay and say so.
    """
    return ClientConfig(server_url=url, share_uploads=True, send_delay_seconds=delay,
                        consent=ConsentState(accepted_hash=disclaimer_hash()))


def an_event(name: str = "莫尼翁銀幣", dive_id: str | None = None,
             age_seconds: float = 0.0) -> dict:
    now = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "occurred_at": now.isoformat(),
        "tz_offset_minutes": 480,
        "provenance": "chest_direct",
        "contents": [{"item_name": name, "quantity": 1}],
        "dive": {"dive_id": dive_id or str(uuid.uuid4()), "started_at": now.isoformat(),
                 "elapsed_seconds": 5, "dungeon_id": 7015},
    }


def spool_write(path: Path, *events: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


# -- the spool is appended to while it is being drained ------------------------------


def test_a_chest_opened_during_an_upload_is_not_erased(server, home):
    """The failure this replaces was total and silent: the event never reached the server,
    left no trace on disk, and the upload reported success."""
    stub, url = server
    stub.delay = 1.0

    from wddrop_client.config import spool_path

    spool = spool_path()
    first, during = an_event("第一個寶箱"), an_event("上傳途中的寶箱")
    spool_write(spool, first)

    result: dict = {}
    drain = threading.Thread(target=lambda: result.update(upload_spool(config(url),
                                                                      CaptureMode.OCR)))
    drain.start()
    time.sleep(0.3)                       # the request is in the air
    spool_write(spool, during)            # ...and the player opens a chest
    drain.join(timeout=30)

    assert result["uploaded"] == 1
    left = [json.loads(ln) for ln in spool.read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    assert [e["event_id"] for e in left] == [during["event_id"]], \
        "the chest opened mid-upload must still be queued"
    assert result["remaining"] == 1

    # And it goes on the next drain, rather than being remembered as sent.
    assert upload_spool(config(url), CaptureMode.OCR)["uploaded"] == 1
    assert {e["event_id"] for e in stub.events} == {first["event_id"], during["event_id"]}


def test_what_is_kept_never_gains_an_install_id(server, home):
    """Failures used to be written back hydrated, putting the one identifier the client is
    careful with into a file that had never held it."""
    stub, url = server
    stub.fail = True

    from wddrop_client.config import spool_path

    spool_write(spool_path(), an_event())
    upload_spool(config(url), CaptureMode.OCR)

    kept = spool_path().read_text(encoding="utf-8")
    assert kept.strip(), "a failed send must stay queued"
    assert "install_id" not in kept


# -- the export is the player's copy, not the outbox ----------------------------------


def test_the_export_survives_the_spool_being_drained(server, home, tmp_path):
    """Per-record sending empties the spool almost immediately, so exporting from it handed
    the player a CSV with nothing in it — their own data, gone from their own machine."""
    _, url = server
    from wddrop_client.config import records_path, spool_path
    from wddrop_client.runner import CaptureRunner

    events = [an_event("莫尼翁銀幣"), an_event("北穿幽靈城的四鱗雜物")]
    for event in events:
        CaptureRunner._spool(event)       # the real record path: outbox AND player's copy

    assert upload_spool(config(url), CaptureMode.OCR)["uploaded"] == 2
    assert spool_path().read_text(encoding="utf-8").strip() == "", "the outbox is drained"

    out = tmp_path / "mine.csv"
    rows = export_csv(records_path(), out, [], spool=spool_path())
    assert rows == 2
    assert "莫尼翁銀幣" in out.read_text(encoding="utf-8-sig")


def test_an_event_in_both_files_is_exported_once(home, tmp_path):
    """A client that recorded before the two files were separated still has events in the
    spool alone; merging them must not double-count anything already in the copy."""
    from wddrop_client.config import records_path, spool_path
    from wddrop_client.runner import CaptureRunner

    older = an_event("舊的寶箱")
    spool_write(spool_path(), older)                  # only ever in the outbox
    CaptureRunner._spool(an_event("新的寶箱"))          # in both

    out = tmp_path / "mine.csv"
    assert export_csv(records_path(), out, [], spool=spool_path()) == 2


# -- how the dive ended reaches rows that are already stored --------------------------


def test_the_close_is_sent_after_the_events_of_that_dive(server, home):
    """Closing a dive the server has not heard of yet would update nothing and then be
    thrown away, which is the loss it exists to prevent."""
    stub, url = server
    from wddrop_client.config import spool_path

    dive = str(uuid.uuid4())
    spool_write(spool_path(), an_event(dive_id=dive))
    record_close(dive, "user_stop")

    result = upload_spool(config(url), CaptureMode.OCR)
    assert stub.order == ["events", "close"], "the reason must not overtake the rows"
    assert result["closed"] == 1
    assert stub.closes[0]["dive_id"] == dive
    assert stub.closes[0]["stop_reason"] == "user_stop"


def test_a_close_that_cannot_be_sent_is_kept_for_the_next_run(server, home):
    """`app_closed` is itself a stop reason, so the send may well be impossible at the time
    — it has to survive to the next launch or that category is the one that goes missing."""
    stub, url = server
    stub.fail = True

    from wddrop_client.config import closes_path

    dive = str(uuid.uuid4())
    record_close(dive, "app_closed")
    assert drain_closes(config(url)) == 0
    assert closes_path().read_text(encoding="utf-8").strip(), "still queued"

    stub.fail = False
    assert drain_closes(config(url)) == 1
    assert closes_path().read_text(encoding="utf-8").strip() == ""
    assert stub.closes[0]["stop_reason"] == "app_closed"


def test_a_reason_the_schema_does_not_know_is_never_queued(home):
    """It would be rejected on arrival and retried forever."""
    from wddrop_client.config import closes_path

    assert record_close(str(uuid.uuid4()), "crashed?") is False
    assert record_close(None, "user_stop") is False
    assert record_close(str(uuid.uuid4()), None) is False
    assert not closes_path().exists() or not closes_path().read_text(encoding="utf-8").strip()


def test_a_queued_close_survives_the_spool_file_being_gone(server, home):
    """The ending outlives the events it describes — they have been sent and the outbox
    deleted — so it must not be stranded by having nothing left to ride along with."""
    stub, url = server
    from wddrop_client.config import spool_path

    dive = str(uuid.uuid4())
    record_close(dive, "game_closed")
    assert not spool_path().exists()

    assert upload_spool(config(url), CaptureMode.OCR)["closed"] == 1
    assert stub.closes[0]["dive_id"] == dive


def test_the_export_names_places_rather_than_numbering_them(home, tmp_path):
    """The CSV is the player's own file. An internal id is a number they cannot place, and
    decoding it is not their job."""
    from wddrop_client.config import records_path
    from wddrop_client.runner import CaptureRunner

    event = an_event("莫尼翁銀幣")
    event["dive"]["dungeon_id"] = 7015
    event["dive"]["floor_id"] = 701501
    CaptureRunner._spool(event)

    out = tmp_path / "mine.csv"
    export_csv(records_path(), out, [], names={7015: "北穿幽靈城", 701501: "B1F"})
    text = out.read_text(encoding="utf-8-sig")

    assert "北穿幽靈城" in text and "B1F" in text
    assert "7015" not in text and "701501" not in text


def test_an_unknown_place_is_blank_rather_than_a_number(tmp_path, home):
    """Falling back to the id would put exactly the thing being hidden into the file."""
    from wddrop_client.config import records_path
    from wddrop_client.runner import CaptureRunner

    event = an_event("莫尼翁銀幣")
    event["dive"]["dungeon_id"] = 9999
    CaptureRunner._spool(event)

    out = tmp_path / "mine.csv"
    export_csv(records_path(), out, [], names={})
    assert "9999" not in out.read_text(encoding="utf-8-sig")


# -- a build that reads the screen wrongly is refused, and loses nothing ------------------
#
# A client that under-reads does not fail loudly: it uploads a chest with two of its three
# items and the row looks exactly like a chest that held two. Up to 0.5.1 that was real —
# the mining panel was rendered at the message band's letter spacing and every item name
# long enough for the drift to matter went unread. Turning such a build away at the door is
# the only thing that stops more of it arriving.
#
# The whole design rests on the refusal costing the player NOTHING, so that is what these
# check: the spool survives intact, and the run says which version to get.


class Old(Stub):
    """An ingest server that has raised its floor above the client asking."""

    seen_versions: list = []

    def do_POST(self):                                            # noqa: N802 (http.server)
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        if self.path.endswith("/v1/events"):
            type(self).seen_versions.append(self.headers.get("X-Client-Version"))
            self.send_response(426)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"detail": {
                "reason": "client_below_minimum", "your_version": "0.5.1",
                "min_version": "0.5.2", "latest_version": "0.5.3",
                "message": "update and your records will send"}}).encode())
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"updated": 1}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def stale_server():
    Old.seen_versions = []
    httpd = HTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Old, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_refused_client_keeps_every_record(stale_server, home):
    """THE POINT OF THE WHOLE MECHANISM. Refusing an upload is only acceptable because it
    delays records rather than losing them — updating has to release the backlog, so the
    outbox must come through untouched."""
    from wddrop_client.config import spool_path

    _stub, url = stale_server
    spool_write(spool_path(), an_event("蒼雫の鉱石"), an_event("透明な小石"))

    result = upload_spool(config(url), CaptureMode.OCR)

    assert result["uploaded"] == 0
    assert result["remaining"] == 2, "records were dropped by a refusal"
    assert spool_path().read_text(encoding="utf-8").count("\n") == 2
    assert result["blocked"]["min_version"] == "0.5.2"
    assert result["blocked"]["latest_version"] == "0.5.3"


def test_the_running_version_is_what_is_asked_about(stale_server, home):
    """The floor is about who is SENDING. The events carry the version that read them, which
    after an update is a different, older number — testing that one would strand the backlog
    permanently, because updating cannot change what already happened."""
    from wddrop_client.config import CLIENT_VERSION, spool_path

    stub, url = stale_server
    # An event captured by a much older build than the one running now.
    old = an_event("蒼雫の鉱石")
    old["client_version"] = "0.4.0"
    spool_write(spool_path(), old)

    upload_spool(config(url), CaptureMode.OCR)

    assert stub.seen_versions == [CLIENT_VERSION]


def test_the_version_that_read_an_event_travels_with_it(server, home):
    """Stamped when the event is captured, not when it is sent. A spool recorded before an
    update and drained after it would otherwise be filed under the newer version — which
    would let a build known to under-read launder its rows into looking like one that does
    not."""
    from wddrop_client.config import spool_path

    stub, url = server
    captured = an_event("蒼雫の鉱石")
    captured["client_version"] = "0.4.0"
    spool_write(spool_path(), captured, an_event("透明な小石"))   # the second has no stamp

    upload_spool(config(url), CaptureMode.OCR)

    versions = [e["capture"]["client_version"] for e in stub.events]
    assert "0.4.0" in versions, "the capturing version was overwritten by the sending one"
    # A line spooled before this existed has no honest answer but the running version.
    from wddrop_client.config import CLIENT_VERSION

    assert CLIENT_VERSION in versions


def test_a_refusal_still_sends_the_dive_endings(stale_server, home):
    """`stop_reason` backfills rows the server ALREADY accepted, and it is the check on
    outcome-dependent stopping — the study's main confound. Withholding it would degrade
    data that is already stored to punish a client for a fault it cannot fix by sulking."""
    from wddrop_client.config import spool_path

    _stub, url = stale_server
    dive = str(uuid.uuid4())
    spool_write(spool_path(), an_event("蒼雫の鉱石", dive_id=dive))
    record_close(dive, "user_stop")

    result = upload_spool(config(url), CaptureMode.OCR)

    assert result["blocked"]
    assert result["closed"] == 1


# -- the send delay, and the delete button it exists for --------------------------------
#
# A record waits `send_delay_seconds` in the outbox before it is allowed to leave. That is
# the whole difference between Delete meaning "this never left your computer" and it meaning
# "please take this back", so the hold is not a nicety — it is what the first of those two
# promises rests on.


def test_a_fresh_record_is_held_and_nothing_is_sent(server, home):
    """Held, not dropped. The line stays in the spool and goes with the next drain."""
    from wddrop_client.config import spool_path

    stub, url = server
    spool_write(spool_path(), an_event("蒼雫の鉱石"))

    result = upload_spool(config(url, delay=20), CaptureMode.OCR)

    assert stub.events == [], "a record inside its grace period was sent"
    assert result["held"] == 1
    assert result["remaining"] == 1, "holding a record must not lose it"
    assert spool_path().read_text(encoding="utf-8").strip(), "the spool was emptied anyway"


def test_the_hold_is_per_record_not_per_drain(server, home):
    """Read from each record's own `occurred_at`. Keying off the file, or off when the last
    drain ran, would hold a whole session because its newest chest is a second old."""
    from wddrop_client.config import spool_path

    stub, url = server
    spool_write(spool_path(),
                an_event("蒼雫の鉱石", age_seconds=300),   # long past its window
                an_event("透明な小石"))                     # just recorded

    result = upload_spool(config(url, delay=20), CaptureMode.OCR)

    assert [c["item_name"] for e in stub.events for c in e["contents"]] == ["蒼雫の鉱石"]
    assert result["held"] == 1 and result["uploaded"] == 1


def test_a_record_deleted_inside_the_delay_is_never_sent(server, home):
    """The whole point of the hold. Nothing goes to the study, now or ever — and the
    player's own copy loses it too, or their Stats page would keep counting a record they
    were told was deleted."""
    from wddrop_client.config import records_path, spool_path
    from wddrop_client.removal import remove_record

    stub, url = server
    doomed, kept = an_event("蒼雫の鉱石"), an_event("透明な小石")
    spool_write(spool_path(), doomed, kept)
    spool_write(records_path(), doomed, kept)

    outcome = remove_record(config(url, delay=20), doomed["event_id"])
    assert outcome == {"unsent": True, "queued": False, "forgotten": True}

    # Drained with no delay at all, so nothing is merely being held back by the clock.
    upload_spool(config(url), CaptureMode.OCR)
    assert [c["item_name"] for e in stub.events for c in e["contents"]] == ["透明な小石"]
    assert doomed["event_id"] not in records_path().read_text(encoding="utf-8")


def test_deleting_an_already_sent_record_queues_a_take_back(server, home):
    """Out of the delay, so the study has it. The spool is what says so: a line is in there
    until the server confirms it, which makes "still spooled" and "not yet sent" one fact
    rather than two that have to be kept in step."""
    from wddrop_client.config import deletes_path, records_path, spool_path
    from wddrop_client.removal import remove_record

    stub, url = server
    sent = an_event("蒼雫の鉱石")
    spool_write(spool_path(), sent)
    spool_write(records_path(), sent)
    upload_spool(config(url), CaptureMode.OCR)
    assert len(stub.events) == 1 and not spool_path().read_text(encoding="utf-8").strip()

    outcome = remove_record(config(url), sent["event_id"])

    assert outcome["unsent"] is False and outcome["queued"] is True
    assert sent["event_id"] in deletes_path().read_text(encoding="utf-8")
    assert sent["event_id"] not in records_path().read_text(encoding="utf-8")


def test_a_take_back_the_server_says_is_too_late_is_not_retried_forever(server, home):
    """410 is an answer, not a failure. Asking again cannot change it, and a queue that
    never drains is a promise the client would go on appearing to keep."""
    from wddrop_client.config import deletes_path
    from wddrop_client.uploader import drain_deletes, record_delete

    stub, url = server
    stub.delete_status = 410
    record_delete(str(uuid.uuid4()))

    result = drain_deletes(config(url))

    assert result == {"removed": 0, "expired": 1, "pending": 0}
    assert not deletes_path().read_text(encoding="utf-8").strip()


def test_a_take_back_that_could_not_be_sent_is_kept(server, home):
    """A deletion that evaporated because the connection blinked is one the player believes
    they made. It waits, like an unsent event does."""
    from wddrop_client.config import deletes_path
    from wddrop_client.uploader import drain_deletes, record_delete

    stub, url = server
    stub.delete_status = 503
    record_delete(str(uuid.uuid4()))

    result = drain_deletes(config(url))

    assert result["pending"] == 1 and result["removed"] == 0
    assert deletes_path().read_text(encoding="utf-8").strip()


def test_take_backs_are_sent_even_with_sharing_off(server, home):
    """Sharing off stops new records LEAVING. It must not strand a request to remove one
    that already has — and someone who has just turned sharing off is the likeliest person
    of anyone to be making that request."""
    from wddrop_client.uploader import drain_deletes, record_delete

    stub, url = server
    stub.delete_status = 200
    cfg = config(url)
    cfg.share_uploads = False
    record_delete(str(uuid.uuid4()))

    assert drain_deletes(cfg)["removed"] == 1


def test_a_server_without_the_endpoint_does_not_queue_a_take_back_forever(server, home):
    """A service older than the client has no `DELETE /v1/events`, and FastAPI answers a
    method it does not route with 405 rather than 404 — which fell through to
    `raise_for_status` and was kept. The queue could then never drain, and the request was
    retried on every upload for the life of the install."""
    from wddrop_client.config import deletes_path
    from wddrop_client.uploader import drain_deletes, record_delete

    stub, url = server
    stub.delete_status = 405
    record_delete(str(uuid.uuid4()))

    result = drain_deletes(config(url))

    assert result["pending"] == 0, "a request no server will ever accept was kept"
    assert not deletes_path().read_text(encoding="utf-8").strip()


def test_a_server_that_is_merely_down_keeps_the_take_back(server, home):
    """The distinction the queue exists for: 5xx and a dead connection are the network, and
    the line waits. Only an answer that retrying cannot improve is dropped."""
    from wddrop_client.uploader import drain_deletes, record_delete

    stub, url = server
    stub.delete_status = 503
    record_delete(str(uuid.uuid4()))

    assert drain_deletes(config(url))["pending"] == 1


# -- asking the service what its rules are -----------------------------------------------


def test_the_policy_is_asked_for_without_sending_anything(server, home):
    """A GET with no body and no install_id. It answers the one question the client cannot
    work out for itself — how long a record can still be taken back — which otherwise only
    arrives on an ingest response, i.e. only when there is something to upload."""
    from wddrop_client.uploader import fetch_policy

    stub, url = server
    stub.policy = {"removal_window_seconds": 86400, "min_client_version": "0.5.2",
                   "latest_client_version": "0.8.0"}

    got = fetch_policy(config(url))

    assert got["removal_window_seconds"] == 86400
    assert stub.policy_requests == 1
    assert stub.policy_bodies == [None], "the client sent something with the question"


def test_an_older_service_without_the_endpoint_is_not_an_error(server, home):
    """404 there means a service that predates this client. It has nothing to say yet, and
    the client keeps the last answer it had rather than resetting to a default."""
    from wddrop_client.uploader import fetch_policy

    stub, url = server
    stub.policy_status = 404
    assert fetch_policy(config(url)) == {}


def test_an_unreachable_service_leaves_the_client_on_what_it_knew(server, home):
    """A rule that changes twice a year must not follow the network up and down."""
    from wddrop_client.uploader import fetch_policy

    cfg = config("http://127.0.0.1:9")            # discard port; nothing listens
    assert fetch_policy(cfg) == {}

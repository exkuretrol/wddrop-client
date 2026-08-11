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
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

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
    order: list = []
    fail = False

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
    Stub.delay, Stub.fail = 0.0, False
    Stub.events, Stub.closes, Stub.order = [], [], []
    httpd = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Stub, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    return tmp_path


def config(url: str) -> ClientConfig:
    return ClientConfig(server_url=url, share_uploads=True,
                        consent=ConsentState(accepted_hash=disclaimer_hash()))


def an_event(name: str = "莫尼翁銀幣", dive_id: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
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

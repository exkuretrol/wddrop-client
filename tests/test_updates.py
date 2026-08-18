"""Telling a player their client is out of date.

The server's floor is the ENFORCEMENT and it only reaches someone who uploads — sharing is
off until asked, and asked once. This is what reaches everyone else, and the whole of its job
is to be right about the version and silent about everything else.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("httpx")

import httpx  # noqa: E402

from wddrop_client import updates  # noqa: E402
from wddrop_client.config import ClientConfig  # noqa: E402
from wddrop_schema.versioning import is_below, is_newer, parse_version  # noqa: E402

# What GitHub actually returned for v0.5.2, trimmed to the fields this reads. Copied from a
# real response rather than invented, because the shape is the contract and a made-up one
# agrees with whatever the code already believes.
REAL = {
    "tag_name": "v0.5.2",
    "html_url": "https://github.com/exkuretrol/wddrop-client/releases/tag/v0.5.2",
    "draft": False,
    "prerelease": False,
    "assets": [{
        "name": "wddrop.exe",
        "size": 68689810,
        "digest": "sha256:d2c436df9d8b235dbb38fb10fc5030d6ab1123585981390dbed0581534152343",
        "browser_download_url":
            "https://github.com/exkuretrol/wddrop-client/releases/download/v0.5.2/wddrop.exe",
    }],
}


def _answer(body, status=200, seen=None):
    """A transport that replies with `body`, recording what was asked."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _patch(monkeypatch, transport):
    real_get = httpx.get

    def get(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(httpx, "get", get)
    return real_get


# -- the comparator both sides share -----------------------------------------------------
#
# That the SERVER uses this same one is asserted in test_client_policy.py, which is not
# exported: this file ships to the public client repository, where the server package does
# not exist and an import of it is a collection error rather than a test.

def test_a_tag_and_a_version_are_the_same_thing():
    """The release says `v0.5.2`, the code says `0.5.2`, and both reach this."""
    assert parse_version("v0.5.2") == parse_version("0.5.2") == (0, 5, 2)
    assert not is_newer("v0.5.2", "0.5.2")


def test_an_unreadable_answer_is_not_news():
    """Deliberately the opposite direction to the server's floor: an unparseable RELEASE is
    left alone, an unparseable CLIENT is not trusted. Both fail towards not bothering
    anyone."""
    assert not is_newer("", "0.5.1")
    assert not is_newer(None, "0.5.1")
    assert is_below("nonsense", "0.5.2")


# -- reading the release -----------------------------------------------------------------

def test_it_reads_the_shape_github_actually_returns(monkeypatch):
    _patch(monkeypatch, _answer(REAL))
    found = updates.latest()
    assert found.version == "0.5.2"
    assert found.page.endswith("/releases/tag/v0.5.2")
    # Carried but unused: nothing downloads the file today, and whoever adds that should not
    # have to change this layer to get the digest.
    assert found.sha256 == "d2c436df9d8b235dbb38fb10fc5030d6ab1123585981390dbed0581534152343"
    assert found.size_bytes == 68689810


def test_a_draft_or_a_prerelease_is_not_offered(monkeypatch):
    """Either would send players after a build we did not mean them to have."""
    for field in ("draft", "prerelease"):
        _patch(monkeypatch, _answer({**REAL, field: True}))
        assert updates.latest() is None


@pytest.mark.parametrize("failure", [
    _answer({}, status=500),
    _answer({"tag_name": None}),
    _answer("not json at all"),
])
def test_every_kind_of_no_answer_is_silence(monkeypatch, failure):
    """Offline, rate-limited, down, or a shape that moved on since this was written. None of
    those is worth a word to the player, and none may raise into the window."""
    _patch(monkeypatch, failure)
    assert updates.latest() is None


def test_it_identifies_itself(monkeypatch):
    """GitHub answers 403 without a User-Agent. Naming the version also means their logs can
    say which builds are still out there — the same question the server's floor asks."""
    seen = []
    _patch(monkeypatch, _answer(REAL, seen=seen))
    updates.latest()
    assert seen and seen[0].headers["user-agent"].startswith("wddrop/")


# -- the decision ------------------------------------------------------------------------

def test_only_a_newer_release_is_reported(monkeypatch):
    _patch(monkeypatch, _answer(REAL))
    cfg = ClientConfig()
    assert updates.check(cfg, running="0.5.1").version == "0.5.2"
    assert updates.check(cfg, running="0.5.2") is None
    assert updates.check(cfg, running="0.6.0") is None, "told to downgrade"


def test_turning_it_off_stops_the_REQUEST(monkeypatch):
    """Not merely the message about it. A switch that still calls out is not the switch the
    player was offered, and this one is in the disclaimer."""
    seen = []
    _patch(monkeypatch, _answer(REAL, seen=seen))
    cfg = ClientConfig(check_updates=False)
    assert updates.check(cfg, running="0.1.0") is None
    assert seen == [], "it asked GitHub anyway"


def test_it_carries_nothing_of_the_players(monkeypatch):
    """The one request in this client that does not go to the study's own server. What it
    may carry is what any web request carries, and nothing else — no install_id above all,
    which is the erasure handle."""
    seen = []
    _patch(monkeypatch, _answer(REAL, seen=seen))
    cfg = ClientConfig()
    updates.check(cfg, running="0.5.1")

    request = seen[0]
    assert request.url.host == "api.github.com"
    assert not request.content
    assert cfg.install_id not in str(request.url)
    for name, value in request.headers.items():
        assert cfg.install_id not in value, f"the install_id rode in {name}"


def test_the_disclaimer_says_this_happens():
    """It is a third party this client otherwise never speaks to. The consent gate is a hash
    of this file, so saying it here is also what re-asks anyone who agreed to terms that did
    not mention it."""
    text = (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
    assert "GitHub" in text
    for promise in ("newer version", "Settings"):
        assert promise in text

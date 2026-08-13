"""
Capture backend interface.

Every capture backend produces the same DropEvent stream, which is what lets a second one be
compared against the first on the same machine — how a new reader would be shown to be right
(docs/PLAN.md §3). Any new backend must satisfy this protocol.
"""
from __future__ import annotations

from typing import Protocol

from wddrop_schema.models import DropEvent


class EventSink(Protocol):
    def __call__(self, event: DropEvent) -> None: ...


class CaptureBackend(Protocol):
    """Runs until stopped, pushing completed events into a sink."""

    def start(self, sink: EventSink) -> None: ...

    def stop(self) -> None: ...

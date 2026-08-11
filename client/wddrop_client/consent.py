"""
Consent gate. Nothing in this client may touch the network before this passes.

ONE opt-in, for one thing: reading the screen. There used to be a second, for a mode that did
not read the screen at all; it is gone, along with the higher ban risk it carried and the
paragraph of the disclaimer that described it.

Acceptance is stored as a hash OF THE DISCLAIMER TEXT, so editing the terms re-prompts
instead of silently inheriting agreement to something the player never read.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

DISCLAIMER_PATH = Path(__file__).resolve().parents[2] / "DISCLAIMER.md"


def disclaimer_path() -> Path:
    """Where the disclaimer actually is.

    Not simply "two folders up from this file": frozen into a one-file exe that resolves to a
    temporary directory that does not contain it, and the first thing the window does is show
    this text. It failed there as a FileNotFoundError with a path under Temp — found by the
    build's own self-check, which is the only reason it was not found by a player instead.
    """
    from .config import bundled_dir, program_dir

    for root in (program_dir(), bundled_dir()):
        if root and (root / "DISCLAIMER.md").exists():
            return root / "DISCLAIMER.md"
    return DISCLAIMER_PATH


def disclaimer_text() -> str:
    return disclaimer_path().read_text(encoding="utf-8")


def disclaimer_hash() -> str:
    """Identity of the exact text the user agreed to, so a material edit re-prompts."""
    return hashlib.sha256(disclaimer_text().encode("utf-8")).hexdigest()[:16]


@dataclass
class ConsentState:
    accepted_hash: str | None = None

    @property
    def general_ok(self) -> bool:
        return self.accepted_hash == disclaimer_hash()



class ConsentRequired(RuntimeError):
    """Raised instead of silently collecting. Callers must surface the disclaimer."""


def require(state: ConsentState) -> None:
    if not state.general_ok:
        raise ConsentRequired(
            "使用者尚未同意免責聲明 / disclaimer not accepted — no data may be collected"
        )

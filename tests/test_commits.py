"""Every commit subject written from here on is Conventional Commits.

This repository spelled its subjects as releases — "0.9.1: Windows 10 can record again" —
because it received one squashed commit per version from an exporter. It is developed here
now, one commit per change, and the release-shaped subject stopped describing what a commit
is. The old ones are left alone: they were accurate when written.

It is not only style. `cliff.toml` sets `filter_unconventional = true`, so git-cliff DROPS a
subject it cannot parse — an unconventional commit does not merely read badly, it vanishes
from the changelog draft and its change goes unmentioned in the release notes players read.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The types git-cliff is configured to group, plus the ones that are conventional anywhere.
PATTERN = re.compile(r"^(feat|fix|docs|test|chore|build|ci|perf|refactor|revert|style)"
                     r"(\([a-z0-9-]+\))?!?: .+")

# Before this, this repository spelled its subjects as releases ("0.9.1: Windows 10 can
# record again"). Those are left as they are: they were the convention at the time, and
ADOPTED_AT = "c92f43e4ff025ef4cc3b168477aa69206b41abf7"  # docs(windows): the first commit written to the convention


def _subjects(rev_range: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%s", rev_range],
                         capture_output=True, text=True)
    return [s for s in out.stdout.splitlines() if s.strip()]


def test_every_commit_since_the_convention_was_adopted_is_conventional():
    known = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", ADOPTED_AT],
                           capture_output=True)
    if known.returncode:
        pytest.skip("the boundary commit is not in this checkout (shallow clone?)")
    bad = [s for s in _subjects(f"{ADOPTED_AT}~1..HEAD") if not PATTERN.match(s)]
    assert not bad, (
        "not Conventional Commits — reword with `git commit --amend`, or rebase:\n  "
        + "\n  ".join(bad))


def test_the_pattern_rejects_what_it_should():
    """A guard nobody has seen fail is not one. The middle case is the one that matters: a
    release-style subject is exactly what arrived here by cherry-pick from the client."""
    for subject in ("added a thing", "0.9.1: Windows 10 can record again", "feat no colon"):
        assert not PATTERN.match(subject), f"{subject!r} should not have passed"
    for subject in ("feat(ingest): a record can be taken back", "fix: one thing", "docs!: a break"):
        assert PATTERN.match(subject), f"{subject!r} should have passed"

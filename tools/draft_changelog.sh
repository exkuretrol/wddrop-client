#!/usr/bin/env bash
# Draft the changelog's [Unreleased] section from the commits since the last tag.
#
# It is a DRAFT. Paste what is useful under [Unreleased] in CHANGELOG.md and rewrite it for
# somebody who plays the game: what changed for them, and whether their existing recordings
# are worth re-verifying. A commit subject cannot say that, which is why this does not write
# the file itself.
#
#     tools/draft_changelog.sh              since the last tag
#     tools/draft_changelog.sh v0.5.2..     since a tag you name
#
# Needs git-cliff:  uv tool install git-cliff
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v git-cliff >/dev/null; then
    echo "[!] git-cliff is not installed.  uv tool install git-cliff" >&2
    exit 1
fi

if [ $# -gt 0 ]; then
    git-cliff "$@"
else
    git-cliff --unreleased
fi

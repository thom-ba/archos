#!/usr/bin/env bash
# AGENTS.md: a change a customer can notice ships with its note
# (`task ci:release-note-required`, pull requests only).
#
# The escape hatch is the `no-release-note` label. On GitHub Actions it is read
# from the PR as it is NOW rather than from the event payload: a re-run replays
# the payload, so a label added after the push used to need another commit.
# Another CI passes the labels it has in PR_LABELS (comma-separated, e.g.
# Woodpecker's CI_COMMIT_PULL_REQUEST_LABELS), and a re-run there needs a push.
#
# Env: BASE_SHA, HEAD_SHA (required); PR_LABELS, or GH_TOKEN + PR_NUMBER +
#      GITHUB_REPOSITORY for the live lookup.
set -euo pipefail
: "${BASE_SHA:?ci/release_note_required.sh needs BASE_SHA}"
: "${HEAD_SHA:?ci/release_note_required.sh needs HEAD_SHA}"

labels="${PR_LABELS:-}"
if [ -z "$labels" ] && [ -n "${PR_NUMBER:-}" ]; then
  # A failed lookup reads as "no label", so the note is then required.
  labels="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" --jq '.labels[].name' || true)"
fi
if printf '%s\n' "$labels" | tr ',' '\n' | grep -qx 'no-release-note'; then
  echo "Labelled no-release-note: no note required."
  exit 0
fi
python3 ci/require_release_note.py "$BASE_SHA" "$HEAD_SHA"

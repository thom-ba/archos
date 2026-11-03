#!/usr/bin/env bash
# The release-note lint of Repo checks, for any CI (`task ci:release-notes`).
#
# The notes are published verbatim to piloti.at, so their prose is linted on
# every run. reno's own lint reads the notes through the whole git history, and
# its verdict changes only with the notes, reno's config or its pin, so a pull
# request that touches none of them skips that part rather than fetch 1.1 GB.
#
# Env: BASE_SHA, the pull request's base (empty on a push: full lint).
#      GH_TOKEN, optional: authenticates the unshallow fetch on GitHub Actions.
#      Another CI's clone carries its own credentials.
set -euo pipefail

if [ -n "${BASE_SHA:-}" ] && git diff --quiet "$BASE_SHA...HEAD" -- releasenotes/ Taskfile.yml taskfiles/repo.yml; then
  echo "No change to the notes, reno's config or its pin: house rules only."
  task release:lint:house
else
  if [ -n "${GH_TOKEN:-}" ]; then . ci/git_auth.sh; fi
  task release:lint
fi

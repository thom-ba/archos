#!/usr/bin/env bash
# Secret scan of Repo checks (`task ci:secrets`), gitleaks at a pinned version.
#
# Every commit the change adds, not only its final diff, so a secret is caught
# in the commit that added it even when a later one deletes it. On a pull
# request that is base..HEAD: the commits under the base were scanned when they
# landed, and the weekly Security run reads the whole history of every branch.
# A push reads its own full history. Not `--all`, gitleaks' default: a
# full-depth checkout fetches every branch, and an open PR's fixture then
# failed every other PR's run. Exceptions: CONTRIBUTING.md "Secret scanning".
#
# Env: BASE_SHA, the pull request's base (empty on a push).
# Runs the gitleaks binary when it is the pinned version (a CI image that ships
# it, or the gitleaks image itself), else the pinned container.
set -euo pipefail
GITLEAKS_VERSION=8.30.1

range="--full-history HEAD"
if [ -n "${BASE_SHA:-}" ]; then range="$BASE_SHA..HEAD"; fi

if command -v gitleaks >/dev/null 2>&1 && [ "$(gitleaks version)" = "$GITLEAKS_VERSION" ]; then
  gitleaks detect --source . --redact -v --log-opts="$range"
else
  docker run --rm -v "$PWD:/repo" \
    "zricethezav/gitleaks:v$GITLEAKS_VERSION" detect --source /repo --redact -v \
    --log-opts="$range"
fi

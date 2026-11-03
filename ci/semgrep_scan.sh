#!/bin/sh
# SAST of a pull request (`task ci:sast`): `semgrep ci` reports only the
# findings the change introduces, so a new finding blocks and the backlog does
# not. POSIX sh: ci.yml runs it in the semgrep image, which has no bash.
#
# On GitHub Actions semgrep finds the merge base itself, asking the API first
# (GH_TOKEN) and deepening a shallow clone only if that fails. Elsewhere it
# diffs against BASE_SHA, so the clone needs the history down to the merge
# base; without BASE_SHA it scans the whole tree, backlog included.
#
# Env: BASE_SHA, the pull request's base (off GitHub Actions; locally try
# BASE_SHA=origin/develop). Writes semgrep.json for ci/semgrep_summary.py.
# Runs the semgrep binary when it is the pinned version (the image ci.yml and
# .woodpecker/sast.yaml run it in), else the pinned container.
set -eu
SEMGREP_VERSION=1.177.0

# p/github-actions includes the mutable-action-tag rule, so an action
# referenced by tag instead of commit SHA is a new finding here.
SEMGREP_RULES="p/default p/javascript p/typescript p/python p/secrets p/github-actions"
export SEMGREP_RULES
if [ "${GITHUB_ACTIONS:-}" != true ] && [ -n "${BASE_SHA:-}" ]; then
  SEMGREP_BASELINE_REF="$BASE_SHA"
  export SEMGREP_BASELINE_REF
fi
set -- ci --json-output=semgrep.json \
  --exclude-rule javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstring

if command -v semgrep >/dev/null 2>&1 && [ "$(semgrep --version)" = "$SEMGREP_VERSION" ]; then
  exec semgrep "$@"
fi
exec docker run --rm -v "$PWD:/src" -w /src -e SEMGREP_RULES -e SEMGREP_BASELINE_REF \
  "semgrep/semgrep:$SEMGREP_VERSION" semgrep "$@"

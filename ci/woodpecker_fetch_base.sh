#!/bin/sh
# The `base` step of a pull request's repo pipeline (.woodpecker/repo.yaml):
# fetch the target branch's tip to refs/ci/base, which `. ci/woodpecker_env.sh`
# reads as BASE_SHA in the steps after it. Alone in its step, so the token a
# private repository needs (GH_TOKEN, as ci/git_auth.sh reads it, in this
# process's env and never in .git/config) is set for this fetch only, not for
# the steps that run the repository's hooks and tests.
set -eu

if [ -n "${GH_TOKEN:-}" ]; then . ci/git_auth.sh; fi
git fetch --no-tags --quiet origin "+refs/heads/$CI_COMMIT_TARGET_BRANCH:refs/ci/base"

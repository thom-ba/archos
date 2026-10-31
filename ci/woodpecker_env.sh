# The CI-neutral env the `task ci:*` targets read, from Woodpecker's own
# variables (https://woodpecker-ci.org/docs/usage/environment). Sourced by a
# step, after ci/woodpecker_setup.sh: `. ci/woodpecker_env.sh`.
#
# BASE_SHA is the target branch's tip on a pull request, where GitHub Actions
# reads the event's base.sha, and empty on a push. Woodpecker names only the
# branch, so it is fetched here: anonymously, or with GH_TOKEN (a read-only
# token from the `github_token` secret) when the repository is private.
# PR_LABELS is CI_COMMIT_PULL_REQUEST_LABELS as the pipeline started, so a
# label added later needs a new push (ci/release_note_required.sh).

export HEAD_SHA="${CI_COMMIT_SHA:-}"
export PR_LABELS="${CI_COMMIT_PULL_REQUEST_LABELS:-}"
BASE_SHA=
if [ "${CI_PIPELINE_EVENT:-}" = pull_request ]; then
  if [ -n "${GH_TOKEN:-}" ]; then . ci/git_auth.sh; fi
  git fetch --no-tags --quiet origin "$CI_COMMIT_TARGET_BRANCH"
  BASE_SHA=$(git rev-parse FETCH_HEAD)
fi
export BASE_SHA

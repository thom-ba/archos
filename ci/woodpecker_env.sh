# The CI-neutral env the `task ci:*` targets read, from Woodpecker's own
# variables (https://woodpecker-ci.org/docs/usage/environment). Sourced by a
# step, after ci/woodpecker_setup.sh: `. ci/woodpecker_env.sh`.
#
# BASE_SHA is the target branch's tip on a pull request, where GitHub Actions
# reads the event's base.sha, and empty on a push. Woodpecker names only the
# branch: the pipeline's `base` step fetched it to refs/ci/base
# (ci/woodpecker_fetch_base.sh), so this step needs no token.
# PR_LABELS is CI_COMMIT_PULL_REQUEST_LABELS as the pipeline started, so a
# label added later needs a new push (ci/release_note_required.sh).

export HEAD_SHA="${CI_COMMIT_SHA:-}"
export PR_LABELS="${CI_COMMIT_PULL_REQUEST_LABELS:-}"
BASE_SHA=
if [ "${CI_PIPELINE_EVENT:-}" = pull_request ]; then
  BASE_SHA=$(git rev-parse --verify --quiet refs/ci/base) || {
    echo "ci/woodpecker_env.sh: no refs/ci/base; the pipeline's base step fetches it" >&2
    return 1
  }
fi
export BASE_SHA

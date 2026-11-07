#!/usr/bin/env bash
# Start one ci-shadow run and follow it. Needs only kubectl, so it lives on the cluster:
#   kubectl -n ci-shadow get configmap ci-shadow-scripts -o jsonpath='{.data.run\.sh}' | bash -s light
# A second argument tests another branch of the test copy, one with an open pull request.
set -euo pipefail

main() {
  local depth="${1:-light}" branch="${2:-}" ns=ci-shadow job slot0
  job="run-$(date +%Y%m%d-%H%M%S)"
  if [ -n "$branch" ]; then
    kubectl -n "$ns" create job "$job" --from="cronjob/$depth" --dry-run=client -o yaml \
      | kubectl set env --local -f - -c feeder BRANCH="$branch" -o yaml \
      | kubectl -n "$ns" create -f -
  else
    kubectl -n "$ns" create job "$job" --from="cronjob/$depth"
  fi
  slot0="batch.kubernetes.io/job-name=$job,batch.kubernetes.io/job-completion-index=0"
  until kubectl -n "$ns" get pod -l "$slot0" -o name | grep -q .; do sleep 2; done
  kubectl -n "$ns" wait --for=condition=Ready pod -l "$slot0" --timeout=600s
  # A dropped stream is not the end of the run: reattach until the slot ends.
  local tail=(--tail=-1)
  until kubectl -n "$ns" logs -f -c feeder -l "$slot0" "${tail[@]}"; do
    sleep 5
    tail=(--since=70s)
  done
}

# Whole script read first, stdin closed: when piped to bash, stdin is this script.
main "$@" </dev/null

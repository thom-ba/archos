#!/usr/bin/env bash
# One slot's feeder: holds the GitHub token and hands the runner beside it a
# single-use registration at a time until the CI run ends. Slot 0 also starts the
# run. Protocol on /shared: `jit` is a registration waiting, `busy` means the
# runner took one, `done` means stop.
set -uo pipefail

: "${REPO:?}" "${BRANCH:?}" "${DEPTH:?}" "${JOB_NAME:?}" "${DEPLOY_REPO:?}"
index="${JOB_COMPLETION_INDEX:-0}"
marker="ci-shadow: $JOB_NAME"
last_runner=""

log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }

api() { # api METHOD PATH [JSON]
  local args=(-fsS -X "$1" -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28')
  [ $# -ge 3 ] && args+=(-H 'Content-Type: application/json' -d "$3")
  curl "${args[@]}" --config <(printf 'header = "Authorization: Bearer %s"\n' "$(cat /secret/token)") \
    "https://api.github.com$2"
}

finish() {
  touch /shared/done
  [ -n "$last_runner" ] && api DELETE "/repos/$REPO/actions/runners/$last_runner" >/dev/null 2>&1
  exit 0
}

head_commit() { api GET "/repos/$REPO/git/ref/heads/$BRANCH" | jq -r .object.sha; }

deploy_running() { # also true when it cannot tell: not knowing must not start a run
  local status count
  for status in in_progress queued; do
    count="$(curl -fsS -H 'Accept: application/vnd.github+json' \
      "https://api.github.com/repos/$DEPLOY_REPO/actions/workflows/deploy.yml/runs?status=$status&per_page=1" \
      | jq -r .total_count)" || return 0
    [ "$count" = 0 ] || return 0
  done
  return 1
}

start_run() {
  local head tree i
  head="$(head_commit)" || { log "cannot read $REPO"; return 1; }
  if [ "$(api GET "/repos/$REPO/git/commits/$head" | jq -r .message)" = "$marker" ]; then
    log "run already started (this slot restarted)"
    return 0
  fi
  for i in $(seq 1 60); do
    deploy_running || break
    [ "$i" = 60 ] && { log "RESULT: not started, a staging deploy ran for over 30 minutes"; return 1; }
    log "a staging deploy is running (or GitHub cannot say), waiting"
    sleep 30
  done
  api PATCH "/repos/$REPO/actions/variables/CI_LIGHT_RUNS" \
    "$(jq -nc --arg v "$([ "$DEPTH" = light ] && echo true || echo false)" '{name:"CI_LIGHT_RUNS",value:$v}')" >/dev/null \
    || { log "cannot set CI_LIGHT_RUNS"; return 1; }
  tree="$(api GET "/repos/$REPO/git/commits/$head" | jq -r .tree.sha)"
  head="$(api POST "/repos/$REPO/git/commits" \
    "$(jq -nc --arg m "$marker" --arg t "$tree" --arg p "$head" '{message:$m,tree:$t,parents:[$p]}')" | jq -r .sha)" \
    || { log "cannot create the commit"; return 1; }
  api PATCH "/repos/$REPO/git/refs/heads/$BRANCH" "$(jq -nc --arg s "$head" '{sha:$s}')" >/dev/null \
    || { log "cannot move $BRANCH"; return 1; }
  log "started the $DEPTH run on $BRANCH (${head:0:9})"
}

wait_for_marker() { # prints the sha of the commit slot 0 made
  local i sha
  for i in $(seq 1 240); do
    sha="$(head_commit)"
    if [ -n "$sha" ] && [ "$(api GET "/repos/$REPO/git/commits/$sha" | jq -r .message)" = "$marker" ]; then
      echo "$sha"
      return 0
    fi
    sleep 10
  done
  return 1
}

find_run() { # prints the CI run of a commit as JSON, or nothing yet
  api GET "/repos/$REPO/actions/runs?head_sha=$1&event=pull_request&per_page=20" \
    | jq -c '[.workflow_runs[] | select(.path == ".github/workflows/ci.yml")] | first // empty'
}

mint() {
  local response
  response="$(api POST "/repos/$REPO/actions/runners/generate-jitconfig" \
    "$(jq -nc --arg n "shadow-$JOB_NAME-$index-$(date +%s)" \
      '{name:$n,runner_group_id:1,labels:["self-hosted","Linux","X64","ci"],work_folder:"_work"}')")" || return 1
  last_runner="$(jq -r .runner.id <<<"$response")"
  jq -r .encoded_jit_config <<<"$response" >/shared/jit.tmp && mv /shared/jit.tmp /shared/jit
}

report() { # the whole run, for whoever reads this slot's log
  local run_id="$1" run="$2"
  api GET "/repos/$REPO/actions/runs/$run_id/jobs?per_page=100" | jq -r '
    def secs: (.completed_at | fromdateiso8601) - (.started_at | fromdateiso8601);
    .jobs[] | select(.conclusion != "skipped")
    | "  \(.conclusion)\t\(if .started_at and .completed_at then "\(secs)s" else "-" end)\t\(.name)"'
  jq -r '"RESULT: \(.conclusion), '"$DEPTH"' run, \(.html_url)"' <<<"$run"
  jq -r '"took \(((.updated_at | fromdateiso8601) - (.run_started_at | fromdateiso8601)) / 60 | floor) minutes"' <<<"$run"
}

if [ "$index" = 0 ]; then
  start_run || finish
fi
sha="$(wait_for_marker)" || { log "the run never started"; finish; }
log "slot $index following commit ${sha:0:9}"

deadline=$((SECONDS + 300))
while :; do
  run="$(find_run "$sha")"
  if [ -z "$run" ]; then
    if [ "$SECONDS" -gt "$deadline" ]; then
      log "GitHub started no CI run within 5 minutes"
      finish
    fi
  elif [ "$(jq -r .status <<<"$run")" = completed ]; then
    if [ "$index" = 0 ]; then
      report "$(jq -r .id <<<"$run")" "$run"
    fi
    finish
  fi
  if [ ! -e /shared/jit ] && [ ! -e /shared/busy ]; then
    mint || log "could not register a runner, retrying"
  fi
  sleep 10
done

#!/usr/bin/env bash
# One slot's runner: takes a registration from the feeder, runs one CI job, clears
# up, and takes the next, until the feeder says the run is over. It never sees the
# GitHub token, only single-use registrations.
set -u

cd /home/runner || exit 1
# What the image ships in $HOME. A pod runs several jobs and each must start as on a
# fresh runner: uv's Python left here ends up in a cached .venv that no other pod can use.
shipped="$(ls -A /home/runner)"
while :; do
  [ -e /shared/done ] && exit 0
  if mv /shared/jit /shared/busy 2>/dev/null; then
    config="$(cat /shared/busy)"
    : >/shared/busy
    find /home/runner/_work /home/runner/.npm-global /dev/shm /tmp -mindepth 1 -delete 2>/dev/null
    ls -A /home/runner | grep -vxF "$shipped" | while read -r leftover; do
      rm -rf "/home/runner/${leftover:?}"
    done
    ./run.sh --jitconfig "$config" &
    pid=$!
    unset config
    while kill -0 "$pid" 2>/dev/null; do
      if [ -e /shared/done ]; then
        kill -TERM "$pid" 2>/dev/null
        for _ in $(seq 1 20); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 1
        done
        # The pod must end, or it holds its quota until the job's deadline.
        pkill -KILL -f Runner.Listener 2>/dev/null
        kill -KILL "$pid" 2>/dev/null
        break
      fi
      sleep 3
    done
    wait "$pid" 2>/dev/null
    rm -f /shared/busy
  fi
  sleep 2
done

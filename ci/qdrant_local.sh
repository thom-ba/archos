#!/usr/bin/env bash
# Qdrant without a Docker daemon, for a runner that has none (vars.CI_NO_DOCKER).
# The binary of the service container's version, from the runner image, on the
# port the job's QDRANT_TEST_URL names. On tmpfs with two segments and a small
# WAL: on disk, tests that create many collections time out waiting on syncs.
set -euo pipefail

binary="$(ls /opt/qdrant/*/qdrant | head -1)"
dir="$(mktemp -d /dev/shm/qdrant.XXXXXX)"
log="${RUNNER_TEMP:-/tmp}/qdrant.log"
cd "$dir"
QDRANT__STORAGE__STORAGE_PATH="$dir/storage" \
QDRANT__STORAGE__SNAPSHOTS_PATH="$dir/snapshots" \
QDRANT__STORAGE__TEMP_PATH="$dir/tmp" \
QDRANT__STORAGE__OPTIMIZERS__DEFAULT_SEGMENT_NUMBER=2 \
QDRANT__STORAGE__WAL__WAL_CAPACITY_MB=4 \
QDRANT__TELEMETRY_DISABLED=true \
  nohup "$binary" >"$log" 2>&1 &

for _ in $(seq 1 60); do
  if curl -fsS http://localhost:6333/readyz >/dev/null 2>&1; then
    echo "Qdrant ready ($binary)"
    exit 0
  fi
  sleep 1
done
echo "::error::Qdrant did not start"
tail -50 "$log"
exit 1

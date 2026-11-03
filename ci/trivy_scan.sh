#!/bin/sh
# Scan pinned third-party images with trivy; fail on a fixable HIGH or CRITICAL.
#
# Usage: trivy_scan.sh <file with one image@sha256:... per line>
# Reads .trivyignore.yaml from the working directory, and caches in .trivy-cache.
# Runs the trivy binary when it is the pinned version (the aquasec/trivy image a
# Woodpecker step runs in), else the pinned container. POSIX sh, because that
# image has no bash. The Actions cache keys hash this file, so a version bump
# starts a fresh layer cache.
#
# Used twice: CI scans the pins a change adds (`task ci:trivy`; on Actions, the
# Plan job's list), the weekly Security run scans all of them
# (ci/pinned_images.py list).
#
# The vulnerability DB is downloaded ONCE and every scan reuses it with
# --skip-db-update: one `docker run --rm` per image each pulling trivy-db from
# GCR returns 429 and fails the job. The layer analysis under .trivy-cache/fanal
# is safe to cache across runs (a digest cannot change); the DB is not, because
# the advisories about a digest do.
#
# Vulnerabilities only (--scanners vuln). trivy's default also runs its secret
# scanner, which this script never gated on by intent: a key inside a vendor's
# image is theirs to rotate, not ours. On the Langfuse web image that scanner
# walks 14 MB source maps and hit trivy's 5-minute deadline (`semaphore
# acquire: context deadline exceeded`) on every promotion PR, which scans all
# pins (#1102, #1104), so the check failed without reporting anything.
set -eu
TRIVY_VERSION=0.61.1

pins="${1:?usage: trivy_scan.sh <pins file>}"
[ -s "$pins" ] || { echo "no images to scan"; exit 0; }

cache="$PWD/.trivy-cache"
mkdir -p "$cache"
if command -v trivy >/dev/null 2>&1 \
  && [ "$(trivy --version | sed -n 's/^Version: //p')" = "$TRIVY_VERSION" ]; then
  ignorefile="$PWD/.trivyignore.yaml"
  scan() { command trivy --cache-dir "$cache" "$@"; }
else
  ignorefile=/config/.trivyignore.yaml
  scan() {
    docker run --rm \
      --user "$(id -u):$(id -g)" \
      --env HOME=/tmp \
      -v "$cache:/cache" \
      -v "$PWD/.trivyignore.yaml:$ignorefile:ro" \
      "aquasec/trivy:${TRIVY_VERSION}" \
      --cache-dir /cache \
      "$@"
  }
fi

downloaded=0
for attempt in 1 2 3 4 5; do
  if scan image --download-db-only --db-repository 'mirror.gcr.io/aquasec/trivy-db:2' \
    || scan image --download-db-only --db-repository 'ghcr.io/aquasecurity/trivy-db:2'; then
    downloaded=1
    break
  fi
  echo "trivy DB download failed (attempt ${attempt}/5); retrying"
  sleep $((attempt * 15))
done
[ "$downloaded" -eq 1 ] || { echo "::error::trivy DB download failed after retries"; exit 1; }

failed=0
while read -r image; do
  [ -n "$image" ] || continue
  echo "=== trivy: $image"
  scan image \
    --scanners vuln \
    --skip-db-update \
    --ignorefile "$ignorefile" \
    --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$image" || failed=1
done < "$pins"
exit "$failed"

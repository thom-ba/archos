#!/usr/bin/env bash
# Install the otelcol-contrib build the collector image pins, for the infra
# tier's deploy/pulumi/src/platform/collector-validate.spec.ts, which runs every
# rendered collector config through its `validate` and reads the binary from
# OTELCOL_BIN. Without it that spec is skipped, so CI installs it first.
#
# Usage: install_otelcol.sh <directory>
# Leaves <directory>/otelcol-contrib, and prints that path (absolute).
#
# The version moves with `collectorImage` in deploy/pulumi/src/config/observability.ts:
# bump it and every checksum below together with that digest. The checksums
# are the release's own `<archive>.sha256` files.
set -euo pipefail

dir="${1:?usage: install_otelcol.sh <directory>}"
version=0.163.2

case "$(uname -s)_$(uname -m)" in
  Linux_x86_64) platform=linux_amd64 sha256=cc19dd6a1b85ca694ef16655688e597f693d0e24d2baecdd50e2db597819eeff ;;
  Linux_aarch64 | Linux_arm64) platform=linux_arm64 sha256=d239e219cf81993213fda2fd6de7dc1f9229d32698c45d2dd7bec31b69c1c004 ;;
  Darwin_arm64) platform=darwin_arm64 sha256=ae0dff18926bc3f1e3ef4d1633c4ad4326781f302da2ba5be2ce215ba86e4b9e ;;
  *) echo "install_otelcol.sh: no pinned otelcol-contrib build for $(uname -sm)" >&2; exit 1 ;;
esac

mkdir -p "$dir"
dir="$(cd "$dir" && pwd)"
bin="$dir/otelcol-contrib"

# A second local run keeps the binary it already verified.
if [ -x "$bin" ] && "$bin" --version 2>/dev/null | grep -qx "otelcol-contrib version ${version}"; then
  echo "$bin"
  exit 0
fi

archive="$dir/otelcol-contrib_${version}_${platform}.tar.gz"
curl -sSfL --retry 3 -o "$archive" \
  "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${version}/otelcol-contrib_${version}_${platform}.tar.gz"
if command -v sha256sum >/dev/null; then
  actual="$(sha256sum "$archive" | cut -d' ' -f1)"
else
  actual="$(shasum -a 256 "$archive" | cut -d' ' -f1)"
fi
if [ "$actual" != "$sha256" ]; then
  echo "install_otelcol.sh: $archive has sha256 $actual, expected $sha256" >&2
  rm -f "$archive"
  exit 1
fi
tar -xzf "$archive" -C "$dir" otelcol-contrib
rm -f "$archive"
echo "$bin"

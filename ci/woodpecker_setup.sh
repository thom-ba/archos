#!/bin/sh
# The tool setup of one Woodpecker step (.woodpecker/*.yaml): what the setup-*
# composite actions under .github/actions give a GitHub Actions job, at their
# pins, so a step's commands are this script and then `task` targets only.
#
#   sh ci/woodpecker_setup.sh [--prefix DIR] [uv] [python] [bun] [gitleaks] [jq]
#
# Always go-task (setup-task). `uv`: uv alone, for a project that syncs its
# own lockfile (setup-uv). `python`: uv, Python 3.14 as `python3` and the
# synced dev venv .venv (setup-python-env). `bun`: setup-bun. `gitleaks`: the
# binary ci/secret_scan.sh pins, since a step has no docker for its fallback.
# `jq`: resolve-image-refs.sh needs it, and the GitHub runner image has it.
#
# POSIX sh, run as root in the step's container: node:22-bookworm, or
# docker:cli (Alpine, no bash or npm) for the image builds. It installs into
# /usr/local/bin, already on PATH, so nothing outlives the step except .venv
# in the shared workspace. `--prefix .tools` installs into .tools/bin in the
# workspace instead, for a later step of the same workflow to put on PATH
# (`. ci/woodpecker_tools.sh`): how a step that holds a secret skips running
# installers. Not with `python`, whose interpreter stays in the step.
# tests/test_woodpecker_pipelines.py holds the pins to the actions' defaults.
set -eu

TASK_VERSION=3.52.0
BUN_VERSION=1.4.3
PYTHON_VERSION=3.14
# The uv of the repo's uv images (deploy/pulumi/src/constants.ts).
UV_VERSION=0.11.32
GITLEAKS_VERSION=$(sed -n 's/^GITLEAKS_VERSION=//p' ci/secret_scan.sh)
JQ_VERSION=1.8.2

prefix=/usr/local
if [ "${1:-}" = --prefix ]; then
  mkdir -p "$2/bin"
  prefix=$(cd "$2" && pwd)
  shift 2
  export PATH="$prefix/bin:$PATH"
fi
bin="$prefix/bin"

npm_global() {
  npm install --global --prefix "$prefix" --no-fund --no-audit --loglevel=error "$@"
}

linux_arch() {
  case "$(uname -m)" in
    x86_64) echo "$1" ;;
    aarch64 | arm64) echo arm64 ;;
    *) echo "ci/woodpecker_setup.sh: no $2 build for $(uname -m)" >&2 && exit 1 ;;
  esac
}

# Download $1 to $2 and check it against the release's checksum file $3.
fetch_checked() {
  curl -fsSL "$1" -o "$2"
  curl -fsSL "$3" | grep " $(basename "$1")\$" | sed "s| .*|  $2|" | sha256sum -c -
}

install_uv() {
  installer=$(mktemp)
  curl -fsSL "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$installer"
  UV_INSTALL_DIR="$bin" UV_NO_MODIFY_PATH=1 sh "$installer" >/dev/null
}

if ! command -v npm >/dev/null 2>&1 && command -v apk >/dev/null 2>&1; then
  # The task targets call bash, git and python3.
  apk add --no-cache bash git nodejs npm python3
fi
npm_global "@go-task/cli@$TASK_VERSION"

for tool in "$@"; do
  case "$tool" in
    uv)
      install_uv
      ;;
    python)
      install_uv
      uv python install "$PYTHON_VERSION"
      # Hooks and ci/ scripts call `python3`, which setup-python makes 3.14.
      ln -sf "$(uv python find --system "$PYTHON_VERSION")" "$bin/python3"
      [ -x .venv/bin/python ] || uv venv --python "$PYTHON_VERSION" .venv
      uv sync --group dev
      ;;
    bun)
      npm_global "bun@$BUN_VERSION"
      ;;
    gitleaks)
      arch=$(linux_arch x64 gitleaks)
      release="https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION"
      archive=$(mktemp)
      fetch_checked "$release/gitleaks_${GITLEAKS_VERSION}_linux_$arch.tar.gz" "$archive" \
        "$release/gitleaks_${GITLEAKS_VERSION}_checksums.txt"
      tar -xzf "$archive" -C "$bin" gitleaks
      ;;
    jq)
      # The static release binary, not apt or apk: it lands in $bin like the rest.
      arch=$(linux_arch amd64 jq)
      release="https://github.com/jqlang/jq/releases/download/jq-$JQ_VERSION"
      fetch_checked "$release/jq-linux-$arch" "$bin/jq" "$release/sha256sum.txt"
      chmod +x "$bin/jq"
      ;;
    *)
      echo "ci/woodpecker_setup.sh: unknown tool '$tool' (uv, python, bun, gitleaks, jq)" >&2
      exit 2
      ;;
  esac
done

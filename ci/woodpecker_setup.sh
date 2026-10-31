#!/bin/sh
# The tool setup of one Woodpecker step (.woodpecker/*.yaml): what the setup-*
# composite actions under .github/actions give a GitHub Actions job, at their
# pins, so a step's commands are this script and then `task` targets only.
#
#   sh ci/woodpecker_setup.sh [python] [bun] [gitleaks]
#
# Always go-task (setup-task). `python`: uv, Python 3.14 as `python3` and the
# synced dev venv .venv (setup-python-env). `bun`: setup-bun. `gitleaks`: the
# binary ci/secret_scan.sh pins, since a step has no docker for its fallback.
#
# POSIX sh, run as root in the step's container: node:22-bookworm, or
# docker:cli (Alpine, no bash or npm) for the image builds. It installs into
# /usr/local/bin, already on PATH, so nothing outlives the step except .venv
# in the shared workspace. tests/test_woodpecker_pipelines.py holds the pins
# to the actions' defaults.
set -eu

TASK_VERSION=3.52.0
BUN_VERSION=1.4.3
PYTHON_VERSION=3.14
# The uv of the repo's uv images (deploy/pulumi/src/constants.ts).
UV_VERSION=0.11.32
GITLEAKS_VERSION=$(sed -n 's/^GITLEAKS_VERSION=//p' ci/secret_scan.sh)

npm_global() {
  npm install --global --no-fund --no-audit --loglevel=error "$@"
}

# Download $1 to $2 and check it against the release's checksum file $3.
fetch_checked() {
  curl -fsSL "$1" -o "$2"
  curl -fsSL "$3" | grep " $(basename "$1")\$" | sed "s| .*|  $2|" | sha256sum -c -
}

if ! command -v npm >/dev/null 2>&1 && command -v apk >/dev/null 2>&1; then
  # The task targets call bash, git and python3.
  apk add --no-cache bash git nodejs npm python3
fi
npm_global "@go-task/cli@$TASK_VERSION"

for tool in "$@"; do
  case "$tool" in
    python)
      installer=$(mktemp)
      curl -fsSL "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$installer"
      UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh "$installer" >/dev/null
      uv python install "$PYTHON_VERSION"
      # Hooks and ci/ scripts call `python3`, which setup-python makes 3.14.
      ln -sf "$(uv python find --system "$PYTHON_VERSION")" /usr/local/bin/python3
      [ -x .venv/bin/python ] || uv venv --python "$PYTHON_VERSION" .venv
      uv sync --group dev
      ;;
    bun)
      npm_global "bun@$BUN_VERSION"
      ;;
    gitleaks)
      case "$(uname -m)" in
        x86_64) arch=x64 ;;
        aarch64 | arm64) arch=arm64 ;;
        *) echo "ci/woodpecker_setup.sh: no gitleaks build for $(uname -m)" >&2 && exit 1 ;;
      esac
      release="https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION"
      archive=$(mktemp)
      fetch_checked "$release/gitleaks_${GITLEAKS_VERSION}_linux_$arch.tar.gz" "$archive" \
        "$release/gitleaks_${GITLEAKS_VERSION}_checksums.txt"
      tar -xzf "$archive" -C /usr/local/bin gitleaks
      ;;
    *)
      echo "ci/woodpecker_setup.sh: unknown tool '$tool' (python, bun, gitleaks)" >&2
      exit 2
      ;;
  esac
done

# Put the tools an earlier step installed with
# `sh ci/woodpecker_setup.sh --prefix .tools` on PATH: only the workspace
# outlives a Woodpecker step. Sourced from the workspace root:
# `. ci/woodpecker_tools.sh`. PULUMI_INSTALL_ROOT points deploy-dev.sh's
# Pulumi CLI install there too.
export PATH="$PWD/.tools/bin:$PATH"
export PULUMI_INSTALL_ROOT="$PWD/.tools"

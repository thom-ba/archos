#!/usr/bin/env bash
# Deploy the dev (staging) stack from this checkout: the `deploy` job of
# .github/workflows/deploy.yml as one CI-neutral script, so a CI other than
# GitHub Actions (the Woodpecker pilot, .woodpecker/deploy-dev.yaml) runs the
# same gates through `task infra:deploy:dev`. Dev only: the stack is fixed
# below, selected first, and named in every pulumi command that writes.
#
#   deploy-dev.sh --prepare   install the Pulumi CLI only; reads no secret
#   deploy-dev.sh             deploy (installs the CLI first if missing)
#
# The task targets install both lockfiles and typecheck first. Reads:
#   PULUMI_ACCESS_TOKEN      Pulumi Cloud
#   GH_TOKEN                 GHCR tag lookups (packages: read)
#   GITHUB_REPOSITORY_OWNER  the registry owner
#   GITHUB_ACTOR             the token's user; defaults to the owner
#   DEPLOY_SHA               the checked-out commit; defaults to HEAD
#   RUN_ID                   set: CI published sha-<DEPLOY_SHA> for all three images
#   EXPLICIT_TAG             an operator's rollback tag, pinned for all three
#   PULUMI_INSTALL_ROOT      where the CLI goes; defaults to ~/.pulumi
# and needs the full history and `jq` (resolve-image-refs.sh). The image tag
# is EXPLICIT_TAG, else sha-<DEPLOY_SHA>, never an IMAGE_TAG from outside:
# only EXPLICIT_TAG pins all three images (resolve-image-refs.sh).
#
# Not ported: the PostHog release and annotation and the ADR-0147 workspace
# stack preview, all best effort in deploy.yml.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

# The project from Pulumi.yaml; it also namespaces the config keys below.
PROJECT="$(sed -n 's/^name:[[:space:]]*//p' Pulumi.yaml)"
: "${PROJECT:?deploy-dev.sh found no name: in Pulumi.yaml}"
STACK="matthiasbigl/${PROJECT}/dev"

# The CLI release of the SDK the lockfile pins (deploy.yml, "Install Pulumi"),
# its installer downloaded to a file rather than piped into a shell.
install_root="${PULUMI_INSTALL_ROOT:-$HOME/.pulumi}"
export PATH="$install_root/bin:$PATH"
install_cli() {
  local version installer
  version="$(node -p "require('@pulumi/pulumi/package.json').version")"
  if [ "$(pulumi version 2>/dev/null || true)" != "v${version}" ]; then
    installer="$(mktemp)"
    curl -fsSL https://get.pulumi.com -o "$installer"
    sh "$installer" --version "$version" --install-root "$install_root" --no-edit-path
    rm -f "$installer"
  fi
}
if [ "${1:-}" = --prepare ]; then
  install_cli
  exit 0
fi

: "${PULUMI_ACCESS_TOKEN:?deploy-dev.sh needs PULUMI_ACCESS_TOKEN}"
: "${GH_TOKEN:?deploy-dev.sh needs GH_TOKEN}"
: "${GITHUB_REPOSITORY_OWNER:?deploy-dev.sh needs GITHUB_REPOSITORY_OWNER}"
export GITHUB_ACTOR="${GITHUB_ACTOR:-$GITHUB_REPOSITORY_OWNER}"

head_sha="$(git rev-parse HEAD)"
export DEPLOY_SHA="${DEPLOY_SHA:-$head_sha}"
if [ "$DEPLOY_SHA" != "$head_sha" ]; then
  echo "error: DEPLOY_SHA ${DEPLOY_SHA} is not the checked-out commit ${head_sha}" >&2
  exit 1
fi
export RUN_ID="${RUN_ID:-}"
export EXPLICIT_TAG="${EXPLICIT_TAG:-}"
if [ -n "${IMAGE_TAG:-}" ] && [ "$IMAGE_TAG" != "${EXPLICIT_TAG:-sha-$DEPLOY_SHA}" ]; then
  echo "error: IMAGE_TAG is derived here; set EXPLICIT_TAG=${IMAGE_TAG} to deploy that tag for all three images" >&2
  exit 1
fi
IMAGE_TAG="${EXPLICIT_TAG:-sha-$DEPLOY_SHA}"

# IMAGE_TAG reaches `pulumi config set`: only the shapes CI publishes.
case "$IMAGE_TAG" in
  sha-[0-9a-f]*) [ "${#IMAGE_TAG}" -eq 44 ] || { echo "error: sha- tag must be the full 40-hex commit sha" >&2; exit 1; } ;;
  latest | v[0-9]*) ;;
  *) echo "error: refusing image tag '${IMAGE_TAG}': expected sha-<40-hex>, v<version>, or latest." >&2; exit 1 ;;
esac
echo "deploying image tag: ${IMAGE_TAG}"

if ! awk -v esc_env="${PROJECT}/dev" -f scripts/stack-file-preflight.awk Pulumi.dev.yaml; then
  echo "error: deploy/pulumi/Pulumi.dev.yaml is still the placeholder template (one-time setup: deploy.yml's header)." >&2
  exit 1
fi
echo "stack file preflight OK"

install_cli

# Best effort, as in deploy.yml: a missing ESC grant (ADR-0147) only warns.
scripts/esc-write-preflight.sh matthiasbigl warn || echo "warning: the ESC write preflight failed; deploying anyway"

pulumi stack select "$STACK"
if [ -n "$RUN_ID" ] || [ -n "$EXPLICIT_TAG" ]; then
  scripts/verify-image-tag.sh "$GITHUB_REPOSITORY_OWNER" "$IMAGE_TAG"
fi
refs="$(scripts/resolve-image-refs.sh)"
while IFS='=' read -r key value; do
  case "$key" in
    BACKEND_IMAGE_REF | FRONTEND_IMAGE_REF | WEB_IMAGE_REF) printf -v "$key" '%s' "$value" ;;
    "") ;;
    *) echo "error: resolve-image-refs.sh printed an unknown key '${key}'" >&2; exit 1 ;;
  esac
done <<<"$refs"
: "${BACKEND_IMAGE_REF:?}" "${FRONTEND_IMAGE_REF:?}" "${WEB_IMAGE_REF:?}"

# The plan `pulumi up` applies, gated by the policy pack and the CRD schemas.
pulumi config set --stack "$STACK" "${PROJECT}:imageTag" "$IMAGE_TAG"
pulumi config set --stack "$STACK" "${PROJECT}:backendImage" "$BACKEND_IMAGE_REF"
pulumi config set --stack "$STACK" "${PROJECT}:frontendImage" "$FRONTEND_IMAGE_REF"
pulumi config set --stack "$STACK" "${PROJECT}:webImage" "$WEB_IMAGE_REF"
if ! pulumi preview --stack "$STACK" --policy-pack ./policy --json >.plan.json; then
  echo "error: pulumi preview failed; re-running in text mode to surface the reason" >&2
  pulumi preview --stack "$STACK" --policy-pack ./policy || true
  cat .plan.json
  exit 1
fi
node scripts/validate-crs.mjs .plan.json

pulumi up --stack "$STACK" --yes

echo "deployed: $(pulumi stack output appUrl --stack "$STACK" || echo 'appUrl unavailable')"

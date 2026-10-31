#!/usr/bin/env bash
# Deploy the dev (staging) stack from this checkout: the `deploy` job of
# .github/workflows/deploy.yml as one CI-neutral script, so a CI other than
# GitHub Actions (the Woodpecker pilot, .woodpecker/deploy-dev.yaml) runs the
# same gates through `task infra:deploy:dev`. Dev only: the stack is fixed
# below, every pulumi command names it, and nothing here takes another.
#
# The task target installs both lockfiles and typechecks first. Reads:
#   PULUMI_ACCESS_TOKEN      Pulumi Cloud
#   GH_TOKEN                 GHCR tag lookups (packages: read)
#   GITHUB_REPOSITORY_OWNER  the registry owner
#   GITHUB_ACTOR             the token's user; defaults to the owner
#   DEPLOY_SHA               the checked-out commit; defaults to HEAD
#   RUN_ID                   set: CI published sha-<DEPLOY_SHA> for all three images
#   EXPLICIT_TAG             an operator's rollback tag, pinned for all three
#   IMAGE_TAG                defaults to EXPLICIT_TAG, else sha-<DEPLOY_SHA>
# and needs the full history and `jq` (resolve-image-refs.sh).
#
# Not ported: the PostHog release and annotation and the ADR-0147 workspace
# stack preview, all best effort in deploy.yml.
set -euo pipefail

STACK=matthiasbigl/grid-oib/dev

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

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
IMAGE_TAG="${IMAGE_TAG:-${EXPLICIT_TAG:-sha-$DEPLOY_SHA}}"

# IMAGE_TAG reaches `pulumi config set`: only the shapes CI publishes.
case "$IMAGE_TAG" in
  sha-[0-9a-f]*) [ "${#IMAGE_TAG}" -eq 44 ] || { echo "error: sha- tag must be the full 40-hex commit sha" >&2; exit 1; } ;;
  latest | v[0-9]*) ;;
  *) echo "error: refusing image tag '${IMAGE_TAG}': expected sha-<40-hex>, v<version>, or latest." >&2; exit 1 ;;
esac
echo "deploying image tag: ${IMAGE_TAG}"

if ! awk -f scripts/stack-file-preflight.awk Pulumi.dev.yaml; then
  echo "error: deploy/pulumi/Pulumi.dev.yaml is still the placeholder template (one-time setup: deploy.yml's header)." >&2
  exit 1
fi
echo "stack file preflight OK"

# The CLI release of the SDK the lockfile pins (deploy.yml, "Install Pulumi"),
# its installer downloaded to a file rather than piped into a shell.
version="$(node -p "require('@pulumi/pulumi/package.json').version")"
export PATH="$HOME/.pulumi/bin:$PATH"
if [ "$(pulumi version 2>/dev/null || true)" != "v${version}" ]; then
  installer="$(mktemp)"
  curl -fsSL https://get.pulumi.com -o "$installer"
  sh "$installer" --version "$version"
  rm -f "$installer"
fi

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
pulumi config set --stack "$STACK" grid-oib:imageTag "$IMAGE_TAG"
pulumi config set --stack "$STACK" grid-oib:backendImage "$BACKEND_IMAGE_REF"
pulumi config set --stack "$STACK" grid-oib:frontendImage "$FRONTEND_IMAGE_REF"
pulumi config set --stack "$STACK" grid-oib:webImage "$WEB_IMAGE_REF"
if ! pulumi preview --stack "$STACK" --policy-pack ./policy --json >.plan.json; then
  echo "error: pulumi preview failed; re-running in text mode to surface the reason" >&2
  pulumi preview --stack "$STACK" --policy-pack ./policy || true
  cat .plan.json
  exit 1
fi
node scripts/validate-crs.mjs .plan.json

pulumi up --stack "$STACK" --yes

echo "deployed: $(pulumi stack output appUrl --stack "$STACK" || echo 'appUrl unavailable')"

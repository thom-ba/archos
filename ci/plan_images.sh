#!/usr/bin/env bash
# The images a CI run must build (ci/image_inputs.py plan), for the Plan job.
#
# A pull request builds an image when it changes what goes into it (BASE, its
# base commit). A push builds one when the registry has no image with these
# inputs yet, whatever the diff (REGISTRY_OWNER, the GHCR namespace), so a
# dropped run, a failed build or a new week costs a rebuild and never a
# missing image. EVENT is pull_request or push.
#
# Sources ci/git_auth.sh when GH_TOKEN is set: a blobless checkout fetches the
# files the hash reads at the base on demand, with the job's token.
set -euo pipefail
: "${EVENT:?ci/plan_images.sh needs EVENT (pull_request or push)}"
if [ -n "${GH_TOKEN:-}" ]; then
  # shellcheck source=ci/git_auth.sh
  . ci/git_auth.sh
fi
if [ "$EVENT" = pull_request ]; then
  exec python3 ci/image_inputs.py plan --base "${BASE:?a pull request needs BASE}"
fi
exec python3 ci/image_inputs.py plan --registry "${REGISTRY_OWNER:?a push needs REGISTRY_OWNER}"

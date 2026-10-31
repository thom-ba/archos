"""Build one of the three images the way CI's build-image action does, on any CI.

    task image:build NAME=backend

`.github/actions/build-image` (docker/build-push-action, still what GitHub
Actions runs) and this script agree on what an image is: the context,
Dockerfile and target from `ci/image_inputs.py`, its content tag
`inputs-<hash>`, the build args (`GIT_SHA` plus `image_inputs.py build-args`),
the frontend's PostHog key as the BuildKit secret `posthog_cli_api_key`
(never a build arg, which the image history keeps), and the registry layer
cache `<repository>:buildcache`, read only. The commit's own tags
(`sha-<commit>`, `latest`) are never set here: publishing adds them once every
check passed. tests/test_build_image.py holds the two to the same shape.

Build only: it pushes nothing and writes no layer cache, since no pipeline
publishes from here yet (docs/contributing/woodpecker.md). The OCI labels
docker/metadata-action adds are reduced to the revision.

Env: REGISTRY (default ghcr.io/archos-at), GIT_SHA (default: HEAD's sha),
POSTHOG_API_KEY and POSTHOG_PROJECT_ID (both, frontend only: source maps).
"""

from __future__ import annotations

import argparse
import datetime
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import image_inputs  # noqa: E402 - the sibling module, by path

POSTHOG_SECRET = "posthog_cli_api_key"
DEFAULT_REGISTRY = "ghcr.io/archos-at"


def command(image: image_inputs.Image, digest: str, build_args: Mapping[str, str], env: Mapping[str, str]) -> list[str]:
    """The `docker buildx build` argv; secret values stay in the environment."""
    registry = env.get("REGISTRY", DEFAULT_REGISTRY).lower()
    ref = f"{registry}/{image.repository}"
    argv = ["docker", "buildx", "build", "--file", image.dockerfile]
    if image.target:
        argv += ["--target", image.target]
    argv += ["--tag", f"{ref}:inputs-{digest}"]
    argv += ["--label", f"org.opencontainers.image.revision={build_args['GIT_SHA']}"]
    args = dict(build_args)
    posthog = image.name == "frontend" and env.get("POSTHOG_API_KEY") and env.get("POSTHOG_PROJECT_ID")
    if posthog:
        args["POSTHOG_PROJECT_ID"] = env["POSTHOG_PROJECT_ID"]
        # `env=` names the variable buildx reads it from: the key never
        # reaches a command line or a log.
        argv += ["--secret", f"id={POSTHOG_SECRET},env=POSTHOG_API_KEY"]
    for key, value in args.items():
        argv += ["--build-arg", f"{key}={value}"]
    argv += ["--cache-from", f"type=registry,ref={ref}:buildcache"]
    argv.append(image.context)
    return argv


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", choices=[image.name for image in image_inputs.IMAGES])
    parser.add_argument("--dry-run", action="store_true", help="print the command, build nothing")
    args = parser.parse_args(argv)

    image = next(image for image in image_inputs.IMAGES if image.name == args.image)
    week = image_inputs.iso_week(datetime.datetime.now(datetime.UTC).date())
    digest = image_inputs.input_hash(image, "HEAD", week=week)
    sha = os.environ.get("GIT_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    build_args = {"GIT_SHA": sha, **image_inputs.build_args(image)}

    cmd = command(image, digest, build_args, os.environ)
    print(shlex.join(cmd), flush=True)
    if args.dry_run:
        return 0
    return subprocess.call(cmd, cwd=image_inputs.ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

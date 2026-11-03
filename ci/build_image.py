"""Build one of the three images, and push it when asked: CI's one image build.

    task image:build NAME=backend    # build only (a pull request)
    task image:push NAME=backend     # build, push, write the layer cache

GitHub Actions runs it from `.github/actions/build-image`, Woodpecker from
`.woodpecker/images.yaml`. An image is the context, Dockerfile and target from
`ci/image_inputs.py`, its content tag `inputs-<hash>`, the build args
(`GIT_SHA` plus `image_inputs.py build-args`) and, for the frontend, the
PostHog key as the BuildKit secret `posthog_cli_api_key` (never a build arg,
which the image history keeps). The commit's own tags (`sha-<commit>`,
`latest`) are never set here: publishing adds them once every check passed.

The layer cache is `<repository>:buildcache` in the registry. Every build reads
it; only a push writes it, so a pull request can never put a layer into the
cache the next publish imports without it appearing in any diff. `mode=max`
because the layer worth reusing is the dependency install in an intermediate
stage; `image-manifest`/`oci-mediatypes` because GHCR rejects the legacy cache
manifest.

Env:
- REGISTRY: lowercased, default ghcr.io/archos-at
- IMAGE_HASH: the content hash from CI's plan; default: computed for HEAD
- GIT_SHA: the commit that builds the image; default: HEAD's sha
- IMAGE_LABELS: extra `key=value` labels, one per line (docker/metadata-action's
  `labels` output on Actions); the revision and version are set either way
- PROVENANCE: buildx's `--provenance`; unset leaves buildx's default
- POSTHOG_API_KEY and POSTHOG_PROJECT_ID: both, frontend only: source maps
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


def labels(digest: str, sha: str, extra: str) -> dict[str, str]:
    """The OCI labels: revision and version, then IMAGE_LABELS over them."""
    result = {
        "org.opencontainers.image.revision": sha,
        "org.opencontainers.image.version": f"inputs-{digest}",
    }
    for line in extra.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            result[key.strip()] = value
    return result


def command(
    image: image_inputs.Image,
    digest: str,
    build_args: Mapping[str, str],
    env: Mapping[str, str],
    *,
    push: bool = False,
) -> list[str]:
    """The `docker buildx build` argv; secret values stay in the environment."""
    registry = env.get("REGISTRY", DEFAULT_REGISTRY).lower()
    ref = f"{registry}/{image.repository}"
    argv = ["docker", "buildx", "build", "--file", image.dockerfile]
    if image.target:
        argv += ["--target", image.target]
    argv += ["--tag", f"{ref}:inputs-{digest}"]
    for key, value in labels(digest, build_args["GIT_SHA"], env.get("IMAGE_LABELS", "")).items():
        argv += ["--label", f"{key}={value}"]
    args = dict(build_args)
    posthog = image.name == "frontend" and env.get("POSTHOG_API_KEY") and env.get("POSTHOG_PROJECT_ID")
    if posthog:
        args["POSTHOG_PROJECT_ID"] = env["POSTHOG_PROJECT_ID"]
        # `env=` names the variable buildx reads it from: the key never
        # reaches a command line or a log.
        argv += ["--secret", f"id={POSTHOG_SECRET},env=POSTHOG_API_KEY"]
    for key, value in args.items():
        argv += ["--build-arg", f"{key}={value}"]
    if env.get("PROVENANCE"):
        argv += ["--provenance", env["PROVENANCE"]]
    argv += ["--cache-from", f"type=registry,ref={ref}:buildcache"]
    if push:
        argv += ["--push"]
        argv += ["--cache-to", f"type=registry,ref={ref}:buildcache,mode=max,image-manifest=true,oci-mediatypes=true"]
    argv.append(image.context)
    return argv


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", choices=[image.name for image in image_inputs.IMAGES])
    parser.add_argument("--push", action="store_true", help="push the content tag and write the layer cache")
    parser.add_argument("--dry-run", action="store_true", help="print the command, build nothing")
    args = parser.parse_args(argv)

    image = next(image for image in image_inputs.IMAGES if image.name == args.image)
    digest = os.environ.get("IMAGE_HASH")
    if not digest:
        week = image_inputs.iso_week(datetime.datetime.now(datetime.UTC).date())
        digest = image_inputs.input_hash(image, "HEAD", week=week)
    sha = os.environ.get("GIT_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    build_args = {"GIT_SHA": sha, **image_inputs.build_args(image)}

    cmd = command(image, digest, build_args, os.environ, push=args.push)
    print(shlex.join(cmd), flush=True)
    if args.dry_run:
        return 0
    # An argv list without a shell: the env values (registry, labels, the build
    # args) reach buildx as single arguments, so nothing in them is a command.
    # nosemgrep: dangerous-subprocess-use-tainted-env-args
    return subprocess.call(cmd, cwd=image_inputs.ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

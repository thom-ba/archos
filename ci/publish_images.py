"""Tag the commit's images once every check passed: CI's publish step.

    task image:publish    # a registry login first

Each image's content tag `inputs-<hash>` gets the commit's tag `sha-<commit>`,
and `latest` when LATEST is `true` (develop). `docker buildx imagetools create`
copies the manifest inside the registry: seconds, and no layer moves. GitHub
Actions runs it from the `publish` job of ci.yml, after every check on the push
passed; that is the contract deploy.yml and a prod promotion rely on.

Env:
- IMAGES: the Plan job's `images` output, JSON `[{name, repository, hash}]`;
  default: every image's hash for HEAD (ci/image_inputs.py)
- REGISTRY: lowercased, default ghcr.io/archos-at
- GIT_SHA: the commit to tag; default: HEAD's sha
- LATEST: `true` also moves `latest`
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import image_inputs  # noqa: E402 - the sibling module, by path

DEFAULT_REGISTRY = "ghcr.io/archos-at"


def commands(images: list[dict], sha: str, env: Mapping[str, str]) -> list[list[str]]:
    """One `imagetools create` argv per image, the content tag as its source."""
    registry = env.get("REGISTRY", DEFAULT_REGISTRY).lower()
    result = []
    for image in images:
        ref = f"{registry}/{image['repository']}"
        tags = ["--tag", f"{ref}:sha-{sha}"]
        if env.get("LATEST") == "true":
            tags += ["--tag", f"{ref}:latest"]
        result.append(["docker", "buildx", "imagetools", "create", *tags, f"{ref}:inputs-{image['hash']}"])
    return result


def head_images() -> list[dict]:
    week = image_inputs.iso_week(datetime.datetime.now(datetime.UTC).date())
    digests = image_inputs.hashes("HEAD", week=week)
    return [
        {"name": image.name, "repository": image.repository, "hash": digests[image.name]}
        for image in image_inputs.IMAGES
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the commands, tag nothing")
    args = parser.parse_args(argv)

    images = json.loads(os.environ["IMAGES"]) if os.environ.get("IMAGES") else head_images()
    sha = os.environ.get("GIT_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    for cmd in commands(images, sha, os.environ):
        print(shlex.join(cmd), flush=True)
        if not args.dry_run:
            # An argv list without a shell: the env values (registry, the
            # images' hashes) reach buildx as single arguments, not a command.
            # nosemgrep: dangerous-subprocess-use-tainted-env-args
            subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

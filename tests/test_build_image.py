"""ci/build_image.py builds an image the way .github/actions/build-image does."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("build_image", ROOT / "ci" / "build_image.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["build_image"] = module
    spec.loader.exec_module(module)
    return module


build_image = _load()
IMAGES = {image.name: image for image in build_image.image_inputs.IMAGES}
ARGS = {"GIT_SHA": "a" * 40, "PYTHON_IMAGE": "python:3.14"}


def test_a_build_tags_the_content_hash_and_reads_the_layer_cache_without_pushing():
    cmd = build_image.command(IMAGES["backend"], "abc123", ARGS, {}, push=False)

    assert cmd[:3] == ["docker", "buildx", "build"]
    assert cmd[cmd.index("--tag") + 1] == "ghcr.io/archos-at/grid-oib-backend:inputs-abc123"
    assert cmd[cmd.index("--target") + 1] == "release"
    assert cmd[cmd.index("--file") + 1] == "apps/agent-host/Dockerfile"
    assert cmd[cmd.index("--cache-from") + 1] == "type=registry,ref=ghcr.io/archos-at/grid-oib-backend:buildcache"
    assert "--push" not in cmd and "--cache-to" not in cmd
    assert "GIT_SHA=" + "a" * 40 in cmd and "PYTHON_IMAGE=python:3.14" in cmd
    assert cmd[-1] == "."
    # Never the commit's own tags: publishing adds those after a green run.
    assert not any(":sha-" in arg or arg.endswith(":latest") for arg in cmd)


def test_a_push_writes_the_tag_and_the_layer_cache():
    cmd = build_image.command(IMAGES["web"], "def456", ARGS, {"REGISTRY": "GHCR.io/Archos-At"}, push=True)

    assert "--push" in cmd
    assert cmd[cmd.index("--cache-to") + 1].startswith("type=registry,ref=ghcr.io/archos-at/grid-oib-web:buildcache,")
    assert "--target" not in cmd
    assert cmd[-1] == "apps/website"


def test_the_posthog_key_reaches_the_frontend_build_only_as_a_secret_from_the_environment():
    env = {"POSTHOG_API_KEY": "phx_secret_value", "POSTHOG_PROJECT_ID": "4711"}

    frontend = build_image.command(IMAGES["frontend"], "h", ARGS, env, push=False)
    assert "phx_secret_value" not in " ".join(frontend)
    assert frontend[frontend.index("--secret") + 1] == "id=posthog_cli_api_key,env=POSTHOG_API_KEY"
    assert "POSTHOG_PROJECT_ID=4711" in frontend

    backend = build_image.command(IMAGES["backend"], "h", ARGS, env, push=False)
    assert "--secret" not in backend and "POSTHOG_PROJECT_ID=4711" not in backend
    # Half the pair builds as before, without source maps.
    half = build_image.command(IMAGES["frontend"], "h", ARGS, {"POSTHOG_API_KEY": "k"}, push=False)
    assert "--secret" not in half

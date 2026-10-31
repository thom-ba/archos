"""ci/build_image.py builds an image the way .github/actions/build-image does."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

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


def _ref(name: str, registry: str = build_image.DEFAULT_REGISTRY) -> str:
    return f"{registry}/{IMAGES[name].repository}"


def test_a_build_tags_the_content_hash_and_reads_the_layer_cache_without_pushing():
    cmd = build_image.command(IMAGES["backend"], "abc123", ARGS, {})

    assert cmd[:3] == ["docker", "buildx", "build"]
    assert cmd[cmd.index("--tag") + 1] == f"{_ref('backend')}:inputs-abc123"
    assert cmd[cmd.index("--target") + 1] == IMAGES["backend"].target
    assert cmd[cmd.index("--file") + 1] == IMAGES["backend"].dockerfile
    assert cmd[cmd.index("--cache-from") + 1] == f"type=registry,ref={_ref('backend')}:buildcache"
    assert "--push" not in cmd and "--cache-to" not in cmd
    assert "GIT_SHA=" + "a" * 40 in cmd and "PYTHON_IMAGE=python:3.14" in cmd
    assert cmd[-1] == IMAGES["backend"].context
    # Never the commit's own tags: publishing adds those after a green run.
    assert not any(":sha-" in arg or arg.endswith(":latest") for arg in cmd)


def test_the_registry_is_lowercased_and_an_image_without_a_target_builds_its_last_stage():
    cmd = build_image.command(IMAGES["web"], "def456", ARGS, {"REGISTRY": "GHCR.io/Owner"})

    assert cmd[cmd.index("--tag") + 1] == f"{_ref('web', 'ghcr.io/owner')}:inputs-def456"
    assert "--target" not in cmd
    assert cmd[-1] == IMAGES["web"].context


def test_the_posthog_key_reaches_the_frontend_build_only_as_a_secret_from_the_environment():
    env = {"POSTHOG_API_KEY": "phx_secret_value", "POSTHOG_PROJECT_ID": "4711"}

    frontend = build_image.command(IMAGES["frontend"], "h", ARGS, env)
    assert "phx_secret_value" not in " ".join(frontend)
    assert frontend[frontend.index("--secret") + 1] == "id=posthog_cli_api_key,env=POSTHOG_API_KEY"
    assert "POSTHOG_PROJECT_ID=4711" in frontend

    backend = build_image.command(IMAGES["backend"], "h", ARGS, env)
    assert "--secret" not in backend and "POSTHOG_PROJECT_ID=4711" not in backend
    # Half the pair builds as before, without source maps.
    half = build_image.command(IMAGES["frontend"], "h", ARGS, {"POSTHOG_API_KEY": "k"})
    assert "--secret" not in half


def test_the_action_builds_the_same_shape():
    """Actions still builds through the action: the two must not drift apart."""
    action = yaml.safe_load((ROOT / ".github" / "actions" / "build-image" / "action.yml").read_text(encoding="utf-8"))
    steps = {step.get("name", ""): step for step in action["runs"]["steps"]}
    labels = steps["Labels"]["with"]
    build = next(step["with"] for name, step in steps.items() if name.startswith("Build$"))
    args = steps["Build arguments"]["run"]

    assert labels["images"] == "${{ inputs.registry }}/${{ inputs.repository }}"
    assert labels["tags"] == "type=raw,value=inputs-${{ inputs.hash }}"
    assert build["cache-from"] == "type=registry,ref=${{ inputs.registry }}/${{ inputs.repository }}:buildcache"
    assert f"format('{build_image.POSTHOG_SECRET}={{0}}'" in build["secrets"]
    assert 'echo "GIT_SHA=$GITHUB_SHA"' in args
    assert 'python3 ci/image_inputs.py build-args "$IMAGE_NAME"' in args
    assert 'echo "POSTHOG_PROJECT_ID=$POSTHOG_PROJECT_ID"' in args

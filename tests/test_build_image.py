"""ci/build_image.py is CI's one image build: Actions and Woodpecker both run it."""

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


def test_a_push_writes_the_image_and_the_layer_cache():
    cmd = build_image.command(IMAGES["frontend"], "abc123", ARGS, {}, push=True)

    assert "--push" in cmd
    cache = f"type=registry,ref={_ref('frontend')}:buildcache"
    assert cmd[cmd.index("--cache-from") + 1] == cache
    assert cmd[cmd.index("--cache-to") + 1] == f"{cache},mode=max,image-manifest=true,oci-mediatypes=true"
    assert cmd[cmd.index("--tag") + 1] == f"{_ref('frontend')}:inputs-abc123"


def test_the_labels_name_the_revision_and_version_and_take_the_ones_ci_passes():
    def labels(env):
        cmd = build_image.command(IMAGES["backend"], "abc123", ARGS, env)
        return dict(cmd[i + 1].split("=", 1) for i, arg in enumerate(cmd) if arg == "--label")

    assert labels({}) == {
        "org.opencontainers.image.revision": "a" * 40,
        "org.opencontainers.image.version": "inputs-abc123",
    }
    # docker/metadata-action's output on Actions: one per line, values may be
    # empty or hold '=' and commas.
    passed = labels(
        {
            "IMAGE_LABELS": "org.opencontainers.image.title=repo-title\n"
            "org.opencontainers.image.description=a, b = c\n"
            "org.opencontainers.image.licenses=\n"
            "org.opencontainers.image.version=inputs-abc123\n"
        }
    )
    assert passed["org.opencontainers.image.title"] == "repo-title"
    assert passed["org.opencontainers.image.description"] == "a, b = c"
    assert passed["org.opencontainers.image.licenses"] == ""
    assert passed["org.opencontainers.image.revision"] == "a" * 40


def test_provenance_is_passed_only_when_set():
    assert "--provenance" not in build_image.command(IMAGES["web"], "h", ARGS, {})
    cmd = build_image.command(IMAGES["web"], "h", ARGS, {"PROVENANCE": "mode=max,builder-id=x"})
    assert cmd[cmd.index("--provenance") + 1] == "mode=max,builder-id=x"


def test_the_plan_hash_and_commit_win_over_the_computed_ones(monkeypatch, capsys):
    monkeypatch.setenv("IMAGE_HASH", "fromtheplan")
    monkeypatch.setenv("GIT_SHA", "b" * 40)
    monkeypatch.delenv("REGISTRY", raising=False)

    assert build_image.main(["web", "--push", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert f"{_ref('web')}:inputs-fromtheplan" in printed
    assert "GIT_SHA=" + "b" * 40 in printed
    assert "--push" in printed


def test_both_cis_build_through_the_task_targets():
    """No second build: the action and the pipeline run the same targets."""
    action = yaml.safe_load((ROOT / ".github" / "actions" / "build-image" / "action.yml").read_text(encoding="utf-8"))
    steps = action["runs"]["steps"]
    assert not [step for step in steps if "build-push-action" in step.get("uses", "")]
    build = next(step for step in steps if step.get("name", "").startswith("Build$"))
    assert build["run"] == 'task "$TARGET" NAME="$IMAGE_NAME"'
    env = build["env"]
    assert env["TARGET"] == "${{ inputs.push == 'true' && 'image:push' || 'image:build' }}"
    assert env["IMAGE_HASH"] == "${{ inputs.hash }}"
    assert env["IMAGE_LABELS"] == "${{ steps.meta.outputs.labels }}"
    # The key reaches the environment buildx reads it from, never a command line.
    assert env["POSTHOG_API_KEY"] == "${{ inputs.posthog-api-key }}"
    assert "posthog-api-key" not in build["run"]

    tasks = yaml.safe_load((ROOT / "taskfiles" / "repo.yml").read_text(encoding="utf-8"))["tasks"]
    assert tasks["image:build"]["cmds"] == ["python3 ci/build_image.py {{.NAME}} {{.CLI_ARGS}}"]
    assert tasks["image:push"]["cmds"] == ["python3 ci/build_image.py {{.NAME}} --push {{.CLI_ARGS}}"]

    pipeline = yaml.safe_load((ROOT / ".woodpecker" / "images.yaml").read_text(encoding="utf-8"))
    assert pipeline["steps"][0]["commands"][-1] == "task image:build NAME=${NAME}"

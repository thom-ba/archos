"""ci/publish_images.py tags a green commit's images, ci/ci_ok.py is the gate before it."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


publish_images = _load("publish_images")
ci_ok = _load("ci_ok")
IMAGES = [
    {"name": "backend", "repository": "app-backend", "hash": "abc"},
    {"name": "web", "repository": "app-web", "hash": "def"},
]
SHA = "a" * 40


def test_each_content_tag_gets_the_commit_tag_and_nothing_is_built():
    cmds = publish_images.commands(IMAGES, SHA, {"REGISTRY": "GHCR.io/Owner"})

    assert cmds == [
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"ghcr.io/owner/app-backend:sha-{SHA}",
            "ghcr.io/owner/app-backend:inputs-abc",
        ],
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"ghcr.io/owner/app-web:sha-{SHA}",
            "ghcr.io/owner/app-web:inputs-def",
        ],
    ]


@pytest.mark.parametrize(("latest", "moves"), [("true", True), ("false", False), ("", False)])
def test_only_latest_true_moves_latest(latest, moves):
    (cmd,) = publish_images.commands(IMAGES[:1], SHA, {"LATEST": latest})

    ref = f"{publish_images.DEFAULT_REGISTRY}/app-backend"
    assert (f"{ref}:latest" in cmd) is moves
    assert cmd[-1] == f"{ref}:inputs-abc"


def test_without_a_plan_it_tags_every_image_at_its_hash_for_head():
    images = publish_images.head_images()

    assert [image["name"] for image in images] == [image.name for image in publish_images.image_inputs.IMAGES]
    assert all(image["hash"] and image["repository"] for image in images)


def test_a_dry_run_prints_the_plans_tags():
    env = {
        "IMAGES": json.dumps(IMAGES),
        "REGISTRY": "ghcr.io/owner",
        "GIT_SHA": SHA,
        "LATEST": "true",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "ci" / "publish_images.py"), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("--tag ghcr.io/owner/app-backend:latest ghcr.io/owner/app-backend:inputs-abc")


@pytest.mark.parametrize(
    ("results", "failed"),
    [
        ({"repo": "success", "image": "skipped"}, {}),
        ({"repo": "success", "backend": "failure"}, {"backend": "failure"}),
        ({"site": "cancelled", "infra": "skipped"}, {"site": "cancelled"}),
    ],
)
def test_the_gate_passes_only_success_and_skipped(results, failed):
    needs = {job: {"result": result, "outputs": {}} for job, result in results.items()}

    assert ci_ok.failing(needs) == failed


def test_the_gate_fails_the_step_and_names_the_jobs():
    needs = json.dumps({"repo": {"result": "success"}, "trivy": {"result": "failure"}})
    result = subprocess.run(
        [sys.executable, str(ROOT / "ci" / "ci_ok.py")],
        env={"NEEDS": needs, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "trivy" in result.stderr and "repo" not in result.stderr

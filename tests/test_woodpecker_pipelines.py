"""The Woodpecker pilot's pipelines (.woodpecker/) stay a thin layer over `task`.

Every command a step runs is the tool setup or one `task` target that exists,
and the setup installs the same pins as the setup-* actions GitHub Actions
uses, so both CIs run the same commands with the same tools.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = sorted((ROOT / ".woodpecker").glob("*.yaml"))
SETUP = (ROOT / "ci" / "woodpecker_setup.sh").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "ci"))
import image_inputs  # noqa: E402 - ci/ is not a package

SETUP_COMMAND = re.compile(r"sh ci/woodpecker_setup\.sh( (python|bun|gitleaks))*")
ENV_COMMAND = ". ci/woodpecker_env.sh"
TASK_COMMAND = re.compile(r"task (?P<name>[a-z][a-z0-9:-]*)( [A-Z_]+=\S+)*")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task_names() -> set[str]:
    names: set[str] = set()
    for taskfile in [ROOT / "Taskfile.yml", *sorted((ROOT / "taskfiles").glob("*.yml"))]:
        names |= set((_load(taskfile).get("tasks") or {}).keys())
    return names


def _action_default(action: str, input_name: str) -> str:
    action_yml = _load(ROOT / ".github" / "actions" / action / "action.yml")
    return str(action_yml["inputs"][input_name]["default"])


def _pin(name: str) -> str:
    match = re.search(rf"^{name}=(\S+)$", SETUP, re.MULTILINE)
    assert match, f"ci/woodpecker_setup.sh no longer sets {name}"
    return match.group(1)


def test_the_pilot_has_its_pipelines():
    assert {path.stem for path in PIPELINES} >= {"repo", "backend", "workspace", "workspace-test", "images"}


@pytest.mark.parametrize("path", PIPELINES, ids=lambda path: path.name)
def test_every_step_runs_only_the_setup_and_task_targets(path: Path):
    tasks = _task_names()
    for step in _load(path)["steps"]:
        commands = step["commands"]
        assert SETUP_COMMAND.fullmatch(commands[0]), f"{path.name}:{step['name']} starts without the setup"
        for command in commands[1:]:
            if command == ENV_COMMAND:
                continue
            match = TASK_COMMAND.fullmatch(command)
            assert match, f"{path.name}:{step['name']} runs {command!r}, not a task target"
            assert match["name"] in tasks, f"{path.name}:{step['name']} calls an unknown task {match['name']}"


def test_the_setup_installs_the_actions_pins():
    assert _pin("TASK_VERSION") == _action_default("setup-task", "version")
    assert _pin("BUN_VERSION") == _action_default("setup-bun", "version")
    assert _pin("PYTHON_VERSION") == _action_default("setup-python-env", "python-version")


def test_the_image_matrix_is_every_image_ci_builds():
    names = _load(ROOT / ".woodpecker" / "images.yaml")["matrix"]["NAME"]
    assert sorted(names) == sorted(image.name for image in image_inputs.IMAGES)


def test_the_backend_suite_gets_the_qdrant_actions_runs():
    ci = _load(ROOT / ".github" / "workflows" / "ci.yml")
    actions_image = ci["jobs"]["backend"]["services"]["qdrant"]["image"]
    services = _load(ROOT / ".woodpecker" / "backend.yaml")["services"]
    assert [service["image"] for service in services] == [actions_image]

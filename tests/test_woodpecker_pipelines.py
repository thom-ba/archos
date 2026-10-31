"""The Woodpecker pilot's pipelines (.woodpecker/) stay a thin layer over `task`.

Every command a step runs is a ci/woodpecker_* helper or one `task` target
that exists, the aggregate targets are what ci.yml's jobs run, and the setup
installs the same pins as the setup-* actions GitHub Actions uses, so both CIs
run the same commands with the same tools.
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

SETUP_COMMAND = re.compile(r"sh ci/woodpecker_setup\.sh( --prefix \.tools)?( (python|bun|gitleaks|jq))*")
# The other helpers a step may run besides `task`: the CI-neutral env, the
# tools an earlier step installed, and a pull request's base fetch.
HELPERS = {". ci/woodpecker_env.sh", ". ci/woodpecker_tools.sh", "sh ci/woodpecker_fetch_base.sh"}
TASK_COMMAND = re.compile(r"task (?P<name>[a-z][a-z0-9:-]*)( [A-Z_]+=\S+)*")
CLONE_IMAGE = "woodpeckerci/plugin-git:2.11.0"


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
    assert {path.stem for path in PIPELINES} >= {
        "repo",
        "backend",
        "workspace",
        "workspace-test",
        "images",
        "deploy-dev",
    }


def _tasks_of(name: str) -> list[str]:
    """A task's `task:` calls, in order."""
    tasks: dict = {}
    for taskfile in [ROOT / "Taskfile.yml", *sorted((ROOT / "taskfiles").glob("*.yml"))]:
        tasks |= _load(taskfile).get("tasks") or {}
    return [cmd["task"] for cmd in tasks[name]["cmds"] if isinstance(cmd, dict) and "task" in cmd]


def _actions_tasks(job: str) -> list[str]:
    """The `task` targets a ci.yml job's steps run, in order."""
    steps = _load(ROOT / ".github" / "workflows" / "ci.yml")["jobs"][job]["steps"]
    runs = [step["run"] for step in steps if "run" in step]
    return [m["name"] for run in runs for m in re.finditer(r"^task (?P<name>[a-z][a-z0-9:-]*)", run, re.MULTILINE)]


@pytest.mark.parametrize("path", PIPELINES, ids=lambda path: path.name)
def test_every_step_runs_only_the_helpers_and_task_targets(path: Path):
    tasks = _task_names()
    for step in _load(path)["steps"]:
        for command in step["commands"]:
            if SETUP_COMMAND.fullmatch(command) or command in HELPERS:
                continue
            match = TASK_COMMAND.fullmatch(command)
            assert match, f"{path.name}:{step['name']} runs {command!r}, not a task target"
            assert match["name"] in tasks, f"{path.name}:{step['name']} calls an unknown task {match['name']}"


@pytest.mark.parametrize("path", PIPELINES, ids=lambda path: path.name)
def test_a_custom_clone_uses_the_pinned_plugin(path: Path):
    # A tagged plugin-git is what WOODPECKER_PLUGINS_TRUSTED_CLONE trusts.
    for clone in _load(path).get("clone") or []:
        assert clone["image"] == CLONE_IMAGE, f"{path.name}:{clone['name']}"


def test_only_the_dev_deploy_step_gets_a_secret():
    holders = [
        (path.stem, step["name"])
        for path in PIPELINES
        for step in _load(path)["steps"]
        if "from_secret" in str(step.get("environment", {}))
    ]
    assert holders == [("deploy-dev", "deploy")]


def test_the_repo_pipeline_runs_what_the_actions_repo_job_runs():
    steps = {step["name"]: step for step in _load(ROOT / ".woodpecker" / "repo.yaml")["steps"]}
    assert steps["checks"]["commands"][-1] == "task ci:repo"
    assert steps["release-note-required"]["commands"][-1] == "task ci:release-note-required"
    # ci:repo is the job's steps in order: lint:repo is pre-commit plus
    # agents:audit, and the pull-request-only release-note rule runs apart.
    expanded = [*_tasks_of("lint:repo"), *_tasks_of("ci:repo")[1:]]
    assert _tasks_of("ci:repo")[0] == "lint:repo"
    actions = [name for name in _actions_tasks("repo") if name != "ci:release-note-required"]
    assert expanded == actions
    ci_steps = _load(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]["repo"]["steps"]
    assert any("pre-commit run --all-files" in step.get("run", "") for step in ci_steps)


def test_the_workspace_pipeline_runs_what_the_actions_workspace_job_runs():
    [step] = _load(ROOT / ".woodpecker" / "workspace.yaml")["steps"]
    assert step["commands"][-1] == "task ci:workspace"
    # Not yet here: the row-level-security suite (the docs' "What is missing").
    actions = [name for name in _actions_tasks("workspace") if name != "db:test:rls"]
    assert _tasks_of("ci:workspace") == actions


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


def test_the_deploy_reaches_only_the_dev_stack():
    pipeline = _load(ROOT / ".woodpecker" / "deploy-dev.yaml")
    # A manual run from develop: deploy.yml still deploys dev on every push.
    assert pipeline["when"] == [{"event": "manual", "branch": "develop"}]
    prepare, deploy = pipeline["steps"]
    # The installers run in a step without secrets; the token step installs nothing.
    assert "environment" not in prepare
    assert prepare["commands"][0].startswith("sh ci/woodpecker_setup.sh --prefix .tools ")
    assert prepare["commands"][-1] == "task infra:deploy:dev:prepare"
    assert deploy["commands"] == [". ci/woodpecker_tools.sh", "task infra:deploy:dev:apply"]
    assert deploy["environment"]["PULUMI_ACCESS_TOKEN"] == {"from_secret": "pulumi_dev_access_token"}
    # The registry Actions publishes to, also on a fork or a mirror.
    assert deploy["environment"]["GITHUB_REPOSITORY_OWNER"] == build_image_owner()
    # No other pipeline mentions Pulumi.
    for path in PIPELINES:
        if path.stem != "deploy-dev":
            assert "pulumi" not in path.read_text(encoding="utf-8").lower(), path.name
    script = (ROOT / "deploy" / "pulumi" / "scripts" / "deploy-dev.sh").read_text(encoding="utf-8")
    assert re.findall(r"^STACK=(\S+)$", script, re.MULTILINE) == ['"matthiasbigl/${PROJECT}/dev"']
    assert "prod" not in script


def build_image_owner() -> str:
    source = (ROOT / "ci" / "build_image.py").read_text(encoding="utf-8")
    match = re.search(r'^DEFAULT_REGISTRY = "ghcr\.io/([^"]+)"$', source, re.MULTILINE)
    assert match
    return match.group(1)

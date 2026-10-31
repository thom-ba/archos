"""deploy/pulumi/scripts/deploy-dev.sh, the dev deploy `task infra:deploy:dev` runs.

The script runs in a throwaway copy of deploy/pulumi with `pulumi`, `node` and
the registry scripts stubbed, so these check what it would do to the stack
without credentials: every pulumi command names the dev stack, the gates run
before `pulumi up`, and a bad tag or an unconfigured stack file stops it
before pulumi is called at all.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deploy" / "pulumi" / "scripts"
DEV_STACK = "matthiasbigl/grid-oib/dev"
SHA = "a" * 40
REFS = {
    "BACKEND_IMAGE_REF": f"ghcr.io/org/grid-oib-backend:sha-{SHA}",
    "FRONTEND_IMAGE_REF": f"ghcr.io/org/grid-oib-frontend:sha-{SHA}",
    "WEB_IMAGE_REF": f"ghcr.io/org/grid-oib-web:sha-{SHA}",
}


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    pulumi_dir = tmp_path / "repo" / "deploy" / "pulumi"
    (pulumi_dir / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "deploy" / "pulumi" / "Pulumi.dev.yaml", pulumi_dir)
    for name in ("deploy-dev.sh", "stack-file-preflight.awk"):
        shutil.copy(SCRIPTS / name, pulumi_dir / "scripts")
    log = tmp_path / "calls.log"
    refs = "".join(f"{key}={value}\\n" for key, value in REFS.items())
    _stub(pulumi_dir / "scripts" / "esc-write-preflight.sh", f'echo "esc-write-preflight $*" >>{log}\n')
    _stub(pulumi_dir / "scripts" / "verify-image-tag.sh", f'echo "verify-image-tag $*" >>{log}\n')
    _stub(pulumi_dir / "scripts" / "resolve-image-refs.sh", f'printf "{refs}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(
        bin_dir / "pulumi",
        f"""\
        [ "$1" = version ] && {{ echo v3.0.0; exit 0; }}
        echo "pulumi $*" >>{log}
        [ "$1" = preview ] && echo '{{}}'
        [ "$1 $2" = "stack output" ] && echo https://dev.example
        exit 0
        """,
    )
    _stub(bin_dir / "node", f'[ "$1" = -p ] && {{ echo 3.0.0; exit 0; }}\necho "node $*" >>{log}\n')
    _stub(bin_dir / "curl", "exit 99\n")
    repo = tmp_path / "repo"
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "c"], check=True)
    return tmp_path


def _run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "repo" / "deploy" / "pulumi" / "scripts" / "deploy-dev.sh"
    environ = {
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PULUMI_ACCESS_TOKEN": "pul-test",
        "GH_TOKEN": "gh-test",
        "GITHUB_REPOSITORY_OWNER": "Org",
        **env,
    }
    return subprocess.run(["bash", str(script)], env=environ, capture_output=True, text=True, check=False)


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_a_deploy_names_the_dev_stack_in_every_pulumi_command(checkout: Path):
    result = _run(checkout)
    assert result.returncode == 0, result.stderr
    pulumi = [call for call in _calls(checkout) if call.startswith("pulumi ")]
    assert pulumi[0] == f"pulumi stack select {DEV_STACK}"
    for call in pulumi:
        assert f" {DEV_STACK}" in call, call
    assert f"pulumi config set --stack {DEV_STACK} grid-oib:backendImage {REFS['BACKEND_IMAGE_REF']}" in pulumi
    verbs = [call.split()[1] for call in pulumi]
    assert verbs.index("preview") < verbs.index("up")
    assert f"pulumi up --stack {DEV_STACK} --yes" in pulumi
    assert "node scripts/validate-crs.mjs .plan.json" in _calls(checkout)
    assert "pul-test" not in result.stdout + result.stderr


def test_a_ci_triggered_deploy_checks_the_commit_tag_is_published(checkout: Path):
    head = subprocess.run(
        ["git", "-C", str(checkout / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert _run(checkout, RUN_ID="7").returncode == 0
    assert f"verify-image-tag Org sha-{head}" in _calls(checkout)


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"EXPLICIT_TAG": "main"}, "refusing image tag 'main'"),
        ({"EXPLICIT_TAG": "sha-abc"}, "full 40-hex"),
        ({"DEPLOY_SHA": SHA}, "is not the checked-out commit"),
        ({"PULUMI_ACCESS_TOKEN": ""}, "needs PULUMI_ACCESS_TOKEN"),
    ],
)
def test_a_bad_input_stops_before_pulumi(checkout: Path, env: dict[str, str], reason: str):
    result = _run(checkout, **env)
    assert result.returncode != 0
    assert reason in result.stderr
    assert _calls(checkout) == []


def test_the_template_stack_file_stops_before_pulumi(checkout: Path):
    stack_file = checkout / "repo" / "deploy" / "pulumi" / "Pulumi.dev.yaml"
    stack_file.write_text("config:\n  grid-oib:baseDomain: REPLACE_ME\n", encoding="utf-8")
    result = _run(checkout)
    assert result.returncode != 0
    assert "placeholder" in result.stdout + result.stderr
    assert _calls(checkout) == []


def test_the_preflight_is_the_one_deploy_yml_runs():
    """Until deploy.yml calls the task, the two copies of the program must not drift."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))
    [run] = [step["run"] for step in workflow["jobs"]["deploy"]["steps"] if "<<'AWK'" in step.get("run", "")]
    inline = re.search(r"<<'AWK'[^\n]*\n(.*?)\nAWK\n", run, re.DOTALL)
    assert inline, "deploy.yml's preflight no longer has its AWK heredoc"
    assert inline[1] in (SCRIPTS / "stack-file-preflight.awk").read_text(encoding="utf-8")

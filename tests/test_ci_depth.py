"""How much of CI a run owes (ci/ci_depth.py) and the status that holds a merge (ci/full_suite_status.py)."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "ci"


def _load(name: str):
    sys.path.insert(0, str(CI_DIR))
    try:
        spec = importlib.util.spec_from_file_location(name, CI_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(CI_DIR))


depth = _load("ci_depth")
status = _load("full_suite_status")
selection = _load("pr_selection")


def _decide(event="pull_request", light="true", base="develop", head_repo="o/r", auto_merge=False):
    def read() -> bool:
        if isinstance(auto_merge, Exception):
            raise auto_merge
        return auto_merge

    return depth.decide(event, light, base, "develop", head_repo, "o/r", read)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "light"),
        ({"auto_merge": True}, "full"),
        ({"event": "push"}, "full"),
        ({"light": ""}, "full"),  # Off until the variable says otherwise.
        ({"light": "false"}, "full"),
        ({"head_repo": "fork/r"}, "full"),
        ({"base": "release/1"}, "full"),  # No required check holds that merge.
        ({"base": "claude/stack"}, "full"),
        ({"auto_merge": RuntimeError("no")}, "full"),  # A failed lookup costs minutes, never a check.
    ],
)
def test_depth(kwargs, expected):
    assert _decide(**kwargs)[0] == expected


def test_depth_reads_auto_merge_only_when_it_decides(monkeypatch):
    def boom() -> bool:
        raise AssertionError("no lookup needed")

    assert depth.decide("push", "true", "develop", "develop", "o/r", "o/r", boom)[0] == "full"


def test_depth_reads_auto_merge_now_and_writes_its_outputs(tmp_path, monkeypatch):
    output = tmp_path / "output"
    for key, value in {
        "GITHUB_OUTPUT": str(output),
        "EVENT": "pull_request",
        "LIGHT_RUNS": "true",
        "BASE_REF": "develop",
        "DEFAULT_BRANCH": "develop",
        "HEAD_REPO": "o/r",
        "GITHUB_REPOSITORY": "o/r",
        "PR_NUMBER": "7",
        "GITHUB_TOKEN": "t",
    }.items():
        monkeypatch.setenv(key, value)
    paths: list[str] = []

    def get(path: str) -> dict:
        paths.append(path)
        return {"auto_merge": {"merge_method": "squash"}}

    monkeypatch.setattr(depth, "github_get", lambda _url, _token: get)

    assert depth.main() == 0
    assert paths == ["/repos/o/r/pulls/7"]
    assert output.read_text() == "depth=full\nreason=auto-merge is on\n"


def test_a_light_run_says_how_to_start_the_full_one():
    body = status.payload("pending", "light", "https://example.test/run")

    assert body["state"] == "pending"
    assert body["context"] == "Full suite"
    assert "auto-merge" in body["description"]


@pytest.mark.parametrize("text", [status.LIGHT, status.STALE, *status.DESCRIPTIONS.values()])
def test_a_description_fits_what_github_accepts(text):
    assert len(text) <= 140


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("HEAD_SHA", "a" * 40)
    monkeypatch.setenv("HEAD_REPO", "o/r")
    monkeypatch.setenv("GITHUB_SHA", "m" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setenv("DEPTH", "full")
    return monkeypatch


def _capture(monkeypatch, second_parent: str = "a" * 40) -> list[dict]:
    sent: list[dict] = []
    commit = {"parents": [{"sha": "b" * 40}, {"sha": second_parent}]}
    monkeypatch.setattr(status, "github_get", lambda _url, _token: lambda _path: commit)
    monkeypatch.setattr(status, "post", lambda _url, _token, _repo, _sha, body: sent.append(body))
    return sent


def test_a_light_run_reports_pending_even_on_a_commit_that_passed(env):
    # A reopened or new pull request on an old commit owes a run against today's base.
    env.setenv("DEPTH", "light")
    sent = _capture(env)

    assert status.main(["x", "pending"]) == 0
    assert [(body["state"], body["description"]) for body in sent] == [("pending", status.LIGHT)]


def test_the_result_links_the_run(env):
    sent = _capture(env)

    assert status.main(["x", "success"]) == 0
    assert sent[0]["state"] == "success"
    assert sent[0]["target_url"].endswith("/o/r/actions/runs/7")


def test_a_run_that_tested_another_head_fails_instead_of_proving_this_one(env, capsys):
    sent = _capture(env, second_parent="c" * 40)

    assert status.main(["x", "success"]) == 1
    assert [(body["state"], body["description"]) for body in sent] == [("failure", status.STALE)]
    assert "::error::" in capsys.readouterr().out


def test_a_failure_is_reported_without_asking_what_was_tested(env):
    sent: list[dict] = []
    env.setattr(status, "github_get", lambda _url, _token: pytest.fail)
    env.setattr(status, "post", lambda _url, _token, _repo, _sha, body: sent.append(body))

    assert status.main(["x", "failure"]) == 0
    assert sent[0]["state"] == "failure"


@pytest.mark.parametrize("commit", [{}, {"parents": [{"sha": "a" * 40}]}])
def test_a_commit_that_is_no_merge_tested_no_head(commit):
    assert status.tested_head(commit) == ""


def _refuse(code: int):
    def post(*_args):
        raise urllib.error.HTTPError("url", code, "no", None, None)

    return post


def test_a_forks_read_only_token_is_a_notice_not_a_failure(env, capsys):
    env.setenv("HEAD_REPO", "fork/r")
    _capture(env)
    env.setattr(status, "post", _refuse(403))

    assert status.main(["x", "success"]) == 0
    assert "::notice::" in capsys.readouterr().out


@pytest.mark.parametrize(("code", "head_repo"), [(403, "o/r"), (500, "fork/r"), (500, "o/r")])
def test_any_other_refusal_fails_the_step(env, code, head_repo):
    # A 403 in this repository is a rate limit or a missing permission, and the merge is waiting.
    env.setenv("HEAD_REPO", head_repo)
    _capture(env)
    env.setattr(status, "post", _refuse(code))

    with pytest.raises(urllib.error.HTTPError):
        status.main(["x", "success"])


def test_only_an_https_api_gets_the_token():
    with pytest.raises(ValueError, match="https"):
        status.post("file:///etc/passwd", "t", "o/r", "a" * 40, {})


def test_an_unknown_state_is_a_usage_error(capsys):
    assert status.main(["x", "error"]) == 2
    capsys.readouterr()


def _api(*runs: dict, artifacts: dict | None = None):
    artifacts = artifacts or {}

    def get(path: str) -> dict:
        if "/artifacts" in path:
            return {"artifacts": artifacts.get(path.split("/runs/")[1].split("/", maxsplit=1)[0], [])}
        return {"workflow_runs": list(runs)}

    return get


LIGHT_RUN = {"id": 2, "head_sha": "b" * 40}
FULL_RUN = {"id": 1, "head_sha": "c" * 40}
MARKER = [{"name": "ci-green-" + "d" * 40, "expired": False}]


def test_a_full_run_does_not_chain_from_a_green_light_run():
    get = _api(LIGHT_RUN, FULL_RUN, artifacts={"1": MARKER})

    found = selection.previous_green(get, "o/r", "branch", "h" * 40, lambda _sha: True, full_only=True)

    assert found is FULL_RUN


def test_a_light_run_chains_from_any_green_run():
    get = _api(LIGHT_RUN, FULL_RUN, artifacts={"1": MARKER})

    found = selection.previous_green(get, "o/r", "branch", "h" * 40, lambda _sha: True)

    assert found is LIGHT_RUN


def test_an_expired_marker_reads_as_light():
    expired = [{"name": "ci-green-" + "d" * 40, "expired": True}]
    get = _api(FULL_RUN, artifacts={"1": expired})

    assert selection.previous_green(get, "o/r", "branch", "h" * 40, lambda _sha: True, full_only=True) is None


def test_the_full_status_name_is_what_the_docs_tell_owners_to_require():
    root = CI_DIR.parent
    ci_doc = (root / "docs/contributing/ci.md").read_text(encoding="utf-8")

    assert f"**`{status.CONTEXT}`**" in ci_doc

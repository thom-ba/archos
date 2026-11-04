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

GREEN = {"state": "success", "context": status.CONTEXT}
OTHER = {"state": "success", "context": "Something else"}
PENDING = {"state": "pending", "context": status.CONTEXT}


@pytest.mark.parametrize(
    ("event", "auto_merge", "head_repo", "expected"),
    [
        ("push", False, "", "full"),
        ("pull_request", False, "o/r", "light"),
        ("pull_request", True, "o/r", "full"),
        ("pull_request", False, "fork/r", "full"),
    ],
)
def test_depth(event, auto_merge, head_repo, expected):
    assert depth.decide(event, auto_merge, head_repo, "o/r")[0] == expected


def test_depth_writes_its_outputs(tmp_path, monkeypatch):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("EVENT", "pull_request")
    monkeypatch.setenv("AUTO_MERGE", "false")
    monkeypatch.setenv("HEAD_REPO", "o/r")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

    assert depth.main() == 0
    assert output.read_text() == "depth=light\nreason=auto-merge is off\n"


def test_a_light_run_says_how_to_start_the_full_one():
    body = status.payload("pending", "light", "https://example.test/run")

    assert body["state"] == "pending"
    assert body["context"] == "Full suite"
    assert "auto-merge" in body["description"]
    assert len(body["description"]) <= 140  # GitHub refuses a longer one.


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], False),
        ([OTHER], False),
        ([GREEN], True),
        ([OTHER, GREEN], True),
        ([PENDING, GREEN], False),  # The newest report of the context decides.
    ],
)
def test_already_proven(statuses, expected):
    assert status.already_proven(statuses) is expected


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("HEAD_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    return monkeypatch


def _capture(monkeypatch, listed: list[dict]) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(status, "github_get", lambda _url, _token: lambda _path: listed)
    monkeypatch.setattr(status, "post", lambda _url, _token, _repo, _sha, body: sent.append(body))
    return sent


def test_a_light_run_does_not_take_back_a_passed_full_suite(env):
    env.setenv("DEPTH", "light")
    sent = _capture(env, [GREEN])

    assert status.main(["x", "pending"]) == 0
    assert sent == []


def test_a_light_run_reports_pending_on_a_new_commit(env):
    env.setenv("DEPTH", "light")
    sent = _capture(env, [])

    assert status.main(["x", "pending"]) == 0
    assert [body["state"] for body in sent] == ["pending"]


def test_a_full_run_resets_the_status_even_after_a_pass(env):
    env.setenv("DEPTH", "full")
    sent = _capture(env, [GREEN])

    assert status.main(["x", "pending"]) == 0
    assert [body["state"] for body in sent] == ["pending"]


def test_the_result_links_the_run(env):
    env.setenv("DEPTH", "full")
    sent = _capture(env, [])

    assert status.main(["x", "success"]) == 0
    assert sent[0]["target_url"].endswith("/o/r/actions/runs/7")


def _refuse(code: int):
    def post(*_args):
        raise urllib.error.HTTPError("url", code, "no", None, None)

    return post


def test_a_forks_read_only_token_is_a_notice_not_a_failure(env, capsys):
    env.setenv("DEPTH", "full")
    env.setattr(status, "post", _refuse(403))

    assert status.main(["x", "success"]) == 0
    assert "::notice::" in capsys.readouterr().out


def test_any_other_refusal_fails_the_step(env):
    env.setenv("DEPTH", "full")
    env.setattr(status, "post", _refuse(500))

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

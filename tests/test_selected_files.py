"""ci/selected_files.py turns a pull request's selection into one `task` argument.

The backend-affected and workspace-test jobs of ci.yml pass the Plan job's JSON
lists through it; a file it drops is a test the selection named and nobody ran.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ci"))
import selected_files  # noqa: E402 - ci/ is not a package

NAMED = [
    "tests/test_wire.py",
    "evals/oib-retrieval/tests/test_harness.py",
    "apps/agent-host/tests/test_plugin.py",
    "packages/knowledge-layer/tests/test_store.py",
    "packages/tavily-web-search/tests/test_client.py",
]


def test_without_a_suite_every_file_passes_in_order():
    assert selected_files.select(["b.ts", "app/app/(shell)/a.ts"]) == ["b.ts", "app/app/(shell)/a.ts"]


@pytest.mark.parametrize(
    ("suite", "expected"),
    [
        ("core", ["tests/test_wire.py", "evals/oib-retrieval/tests/test_harness.py"]),
        ("api", ["apps/agent-host/tests/test_plugin.py"]),
        (
            "sources",
            ["packages/knowledge-layer/tests/test_store.py", "packages/tavily-web-search/tests/test_client.py"],
        ),
    ],
)
def test_a_suite_gets_only_its_own_files(suite, expected):
    assert selected_files.select(NAMED, suite) == expected


def test_only_the_first_shard_runs_the_named_files():
    assert selected_files.select(NAMED, "core", "1/4") == selected_files.select(NAMED, "core")
    for shard in ("2/4", "3/4", "4/4"):
        assert selected_files.select(NAMED, "core", shard) == []


def test_the_suites_split_the_named_files_without_overlap():
    picked = [path for suite in selected_files.SUITE_ROOTS for path in selected_files.select(NAMED, suite)]
    assert sorted(picked) == sorted(NAMED)


def test_main_reads_the_list_from_the_env(monkeypatch, capsys):
    monkeypatch.setenv("FILES_JSON", '["tests/test_wire.py", "apps/agent-host/tests/test_plugin.py"]')
    selected_files.main(["--suite", "api", "--shard", ""])
    assert capsys.readouterr().out == "apps/agent-host/tests/test_plugin.py\n"
    monkeypatch.delenv("FILES_JSON")
    selected_files.main([])
    assert capsys.readouterr().out == "\n"

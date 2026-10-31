"""ci/paths_filter.py reads .github/filters.yml as YAML does and matches as picomatch does.

It replaced dorny/paths-filter in the Plan job, so a tier it misreads is a job
that silently stops running. The parser is held to yaml.safe_load, the matcher
to wcmatch on every tracked path (wcmatch is held to picomatch in
tests/test_ci_filter_coverage.py) and to picomatch itself where node has it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from wcmatch import glob

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ROOT / ".github" / "filters.yml"
SCRIPT = ROOT / "ci" / "paths_filter.py"
FLAGS = glob.GLOBSTAR | glob.DOTGLOB

spec = importlib.util.spec_from_file_location("paths_filter", SCRIPT)
paths_filter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paths_filter)


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [path for path in out.split("\0") if path]


def test_the_parser_reads_filters_yml_as_yaml_does():
    text = FILTERS.read_text(encoding="utf-8")
    assert paths_filter.parse_filters(text) == yaml.safe_load(text)


@pytest.mark.parametrize(
    "text",
    [
        "backend: &shared\n  - 'a/**'\nsite: *shared\n",
        "backend: ['a/**']\n",
        "backend:\n  - a/**\n",
        'backend:\n  - "a/**"\n',
        "backend:\n  - 'a/**' # why\n",
        "backend:\n  - 'docs/{a,b}/**'\n",
        "backend:\n  - '!docs/**'\n",
        "backend:\n  - 'a'\nbackend:\n  - 'b'\n",
        "backend:\n",
        "  - 'a/**'\n",
    ],
)
def test_the_parser_refuses_what_it_would_not_read_as_yaml_and_picomatch_do(text: str):
    with pytest.raises(paths_filter.FilterError):
        paths_filter.parse_filters(text)


def test_each_tier_matches_the_tracked_paths_wcmatch_matches():
    tiers = paths_filter.parse_filters(FILTERS.read_text(encoding="utf-8"))
    regexes = paths_filter.compile_tiers(tiers)
    paths = _tracked() + ["apps/moved-ui/src/page.tsx", ".github/x/y.yml", "a/.hidden/b"]
    for tier, patterns in tiers.items():
        matcher = glob.compile(patterns, flags=FLAGS)
        disagree = [path for path in paths if bool(regexes[tier].fullmatch(path)) != matcher.match(path)]
        assert disagree == [], f"{tier}: ci/paths_filter.py and wcmatch disagree on {disagree[:20]}"


EDGES = [
    ("a/**", ["a", "a/b", "a/.x/y", "ab", "b/a"]),
    ("**/b", ["b", "x/b", ".x/y/b", "xb"]),
    ("a/**/b", ["a/b", "a/x/y/b", "a/xb"]),
    ("*.md", [".md", "a.md", "d/a.md"]),
    ("**", ["a", "a/b"]),
    ("a/*/c", ["a/b/c", "a/.b/c", "a/b/x/c"]),
    ("a*b.yml", ["ab.yml", "axyb.yml", "a/b.yml"]),
]


def _picomatch2() -> Path | None:
    for candidate in sorted(ROOT.glob("apps/*/node_modules/**/picomatch/package.json")):
        if json.loads(candidate.read_text(encoding="utf-8")).get("version", "").startswith("2."):
            return candidate.parent
    return None


def test_the_matcher_agrees_with_picomatch_where_wcmatch_does_not_decide():
    # `a/**` matching `a` is where wcmatch and picomatch part ways; dorny used picomatch.
    picomatch = _picomatch2()
    if shutil.which("node") is None or picomatch is None:
        pytest.skip("needs node and a picomatch 2.x under apps/*/node_modules (task setup)")
    script = (
        "const pm = require(process.argv[1]);"
        "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(cases.map(([p, xs]) => xs.map(x => pm(p, {dot: true})(x)))));"
    )
    result = subprocess.run(
        ["node", "-e", script, str(picomatch)], input=json.dumps(EDGES), capture_output=True, text=True, check=True
    )
    expected = json.loads(result.stdout)
    for (pattern, paths), want in zip(EDGES, expected, strict=True):
        regex = paths_filter.compile_tiers({"t": [pattern]})["t"]
        assert [bool(regex.fullmatch(path)) for path in paths] == want, pattern


def test_the_known_edges():
    regex = paths_filter.compile_tiers({"t": ["a/**"]})["t"]
    assert regex.fullmatch("a") and regex.fullmatch("a/.x/y") and not regex.fullmatch("ab")
    regex = paths_filter.compile_tiers({"t": ["*.md"]})["t"]
    assert regex.fullmatch(".md") and not regex.fullmatch("d/a.md")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    _git(tmp_path, "config", "user.name", "ci")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "filters.yml").write_text(
        "# tiers\nbackend:\n  - 'packages/agent/**'\n  - 'pyproject.toml'\n\nsite:\n  - 'apps/website/**'\n",
        encoding="utf-8",
    )
    (tmp_path / "packages" / "agent").mkdir(parents=True)
    (tmp_path / "packages" / "agent" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _run(repo: Path, base: str, tmp_path: Path) -> dict[str, str]:
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    subprocess.run(
        ["python3", str(SCRIPT), "--base", base],
        cwd=repo,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def test_a_deleted_file_counts_and_an_untouched_tier_reads_false(repo: Path, tmp_path: Path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "packages" / "agent" / "a.py").unlink()
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    assert _run(repo, base, tmp_path) == {
        "backend": "true",
        "backend_count": "1",
        "site": "false",
        "site_count": "0",
        "changes": '["backend"]',
    }


def test_no_base_runs_every_tier(repo: Path, tmp_path: Path):
    out = _run(repo, "", tmp_path)
    assert (out["backend"], out["site"], out["changes"]) == ("true", "true", '["backend", "site"]')


def test_the_plan_job_feeds_the_script_the_base_dorny_had():
    steps = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]["changes"][
        "steps"
    ]
    [step] = [step for step in steps if step.get("id") == "filter"]
    assert step["run"] == 'python3 ci/paths_filter.py --base "$BASE"'
    assert step["env"]["BASE"] == "${{ github.event.pull_request.base.sha || steps.green.outputs.sha }}"
    assert not [step for step in steps if "paths-filter" in step.get("uses", "")]

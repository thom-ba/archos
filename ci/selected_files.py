"""The files a pull request's test selection names, as one `task` argument.

ci/pr_selection.py writes them as JSON lists: `ui_files`, the changed files the
UI specs are selected from, and `py_extra`, the backend test files that name a
changed non-Python file, which coverage cannot connect to the change. A job
passes the list in FILES_JSON and gets the files space-separated on stdout,
for `task workspace:test:related FILES=…` or `task be:test:affected EXTRA=…`
(the `ci:workspace:related` and `ci:backend:affected` targets call this).

With --suite, only the backend suite's own test files, and only in its first
shard (--shard 1/4, or none), so a sharded suite runs each file once.

Usage: FILES_JSON='[...]' python3 ci/selected_files.py [--suite core|api|sources [--shard N/M]]
"""

from __future__ import annotations

import argparse
import json
import os
import re

# Where each suite's tests live: TESTMON_PATHS in taskfiles/backend.yml.
SUITE_ROOTS = {
    "core": re.compile(r"^(tests|evals)/"),
    "api": re.compile(r"^apps/agent-host/tests/"),
    "sources": re.compile(r"^packages/(knowledge-layer|ris-adapter|tavily-web-search)/"),
}


def select(files: list[str], suite: str = "", shard: str = "") -> list[str]:
    if not suite:
        return files
    if shard and shard.split("/", maxsplit=1)[0] != "1":
        return []
    return [path for path in files if SUITE_ROOTS[suite].search(path)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=sorted(SUITE_ROOTS), default="")
    parser.add_argument("--shard", default="")
    args = parser.parse_args(argv)
    files = json.loads(os.environ.get("FILES_JSON") or "[]")
    print(" ".join(select(files, args.suite, args.shard)))


if __name__ == "__main__":
    main()

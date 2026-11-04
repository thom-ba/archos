#!/usr/bin/env python3
"""Decide how much of CI this run owes: a pull request's light check, or the full gate.

A pull request that is still changing gets the checks that fail most and cost
least on every push. The rest runs once, when someone asks to merge it: turning
on auto-merge (`gh pr merge --auto --squash`) starts the full run, and the
commit status `Full suite`, which only a full run can turn green, holds the
merge until then (ci/full_suite_status.py).

light  a pull request into the default branch from this repository, with
       auto-merge off, once the repository variable CI_LIGHT_RUNS is 'true'.
full   everything else. The variable is the rollout and the off switch: unset,
       every run does what it did before light runs existed. Another base has no
       required check to hold a merge, so auto-merge could never start its full run.

Auto-merge is read from the API, not the event: runs of one pull request can
start in either order and a re-run replays its old event, so a payload could make
a light run cancel the full run auto-merge is waiting for.

A failure here, or in reading auto-merge, is full: it may cost minutes, never a check.

Environment: EVENT, LIGHT_RUNS, BASE_REF, DEFAULT_BRANCH, HEAD_REPO, PR_NUMBER,
GITHUB_REPOSITORY, GITHUB_TOKEN, GITHUB_API_URL. Writes `depth` and `reason` to
$GITHUB_OUTPUT.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from reuse_green_run import github_get


def decide(
    event: str,
    light_runs: str,
    base: str,
    default_branch: str,
    head_repo: str,
    repo: str,
    auto_merge: Callable[[], bool],
) -> tuple[str, str]:
    if event != "pull_request":
        return "full", f"a {event} run is always full"
    if light_runs != "true":
        return "full", "light runs are off (CI_LIGHT_RUNS)"
    if head_repo != repo:
        return "full", "a fork cannot turn auto-merge on"
    if base != default_branch:
        return "full", f"only a pull request into {default_branch} has a required check to hold its merge"
    try:
        return ("full", "auto-merge is on") if auto_merge() else ("light", "auto-merge is off")
    except Exception as error:  # noqa: BLE001 - a lookup failure means a full run, never a red Plan
        return "full", f"could not read auto-merge ({error})"


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    def auto_merge() -> bool:
        get = github_get(os.environ.get("GITHUB_API_URL", "https://api.github.com"), os.environ["GITHUB_TOKEN"])
        return bool(get(f"/repos/{repo}/pulls/{int(os.environ['PR_NUMBER'])}").get("auto_merge"))

    depth, reason = decide(
        os.environ.get("EVENT", ""),
        os.environ.get("LIGHT_RUNS", ""),
        os.environ.get("BASE_REF", ""),
        os.environ.get("DEFAULT_BRANCH", ""),
        os.environ.get("HEAD_REPO", ""),
        repo,
        auto_merge,
    )
    print(f"depth: {depth} ({reason})")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"depth={depth}\nreason={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Decide how much of CI this run owes: a pull request's light check, or the full gate.

A pull request that is still changing gets the checks that fail most and cost
least on every push. The rest runs once, when someone asks to merge it: turning
on auto-merge (`gh pr merge --auto --squash`) starts the full run, and the
commit status `Full suite`, which only a full run can turn green, holds the
merge until then (ci/full_suite_status.py).

full   anything but a pull request; a pull request with auto-merge on; a pull
       request from a fork, whose contributor cannot turn auto-merge on.
light  every other pull request.

ci.yml reads a failure of this script as full: it may cost minutes, never a check.

Environment: EVENT, AUTO_MERGE ('true' when on), HEAD_REPO, GITHUB_REPOSITORY.
Writes `depth` and `reason` to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import os
import sys


def decide(event: str, auto_merge: bool, head_repo: str, repo: str) -> tuple[str, str]:
    if event != "pull_request":
        return "full", f"a {event} run is always full"
    if head_repo != repo:
        return "full", "a fork cannot turn auto-merge on"
    if auto_merge:
        return "full", "auto-merge is on"
    return "light", "auto-merge is off"


def main() -> int:
    depth, reason = decide(
        os.environ.get("EVENT", ""),
        os.environ.get("AUTO_MERGE") == "true",
        os.environ.get("HEAD_REPO", ""),
        os.environ.get("GITHUB_REPOSITORY", ""),
    )
    print(f"depth: {depth} ({reason})")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"depth={depth}\nreason={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

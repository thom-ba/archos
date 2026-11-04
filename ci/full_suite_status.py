#!/usr/bin/env python3
"""Report the commit status `Full suite` on a pull request's head commit.

This status is what holds a merge until the full run has passed. `CI OK` cannot
do it: a light run's `CI OK` is green, and auto-merge merges at once when every
required check is already green, so requiring `CI OK` alone would merge a pull
request the moment auto-merge starts the run that was meant to prove it.

A status, not a job: a skipped job reports success to a required check, so a
job a light run skips would pass the gate it exists to hold.

- pending: Plan, on every pull request run. A light run says how to start the
  full one. It leaves a `success` alone, so re-running a light run on a commit
  the full run already proved does not take the proof back.
- success, failure: `CI OK`, on a full run only.

A fork's read-only token cannot post a status, so that is a notice and not a
failure: a maintainer pushes the branch to this repository to merge it.

Usage: full_suite_status.py pending|success|failure
Environment: GITHUB_TOKEN (statuses: write), GITHUB_REPOSITORY, GITHUB_API_URL,
GITHUB_SERVER_URL, GITHUB_RUN_ID, HEAD_SHA, DEPTH.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from reuse_green_run import github_get

CONTEXT = "Full suite"
LIGHT = "Light checks only. Turn on auto-merge to run the full suite."
DESCRIPTIONS = {
    "pending": "Full suite running.",
    "success": "Full suite passed.",
    "failure": "Full suite failed: see this run.",
}


def payload(state: str, depth: str, target_url: str) -> dict[str, str]:
    description = LIGHT if state == "pending" and depth == "light" else DESCRIPTIONS[state]
    return {"state": state, "context": CONTEXT, "description": description, "target_url": target_url}


def already_proven(statuses: list[dict]) -> bool:
    """Whether the newest report of this context says the full suite passed (the API lists newest first)."""
    return next((s.get("state") == "success" for s in statuses if s.get("context") == CONTEXT), False)


def post(api_url: str, token: str, repo: str, sha: str, body: dict[str, str]) -> None:
    base = urllib.parse.urlsplit(api_url)
    if base.scheme != "https" or not base.netloc:
        raise ValueError(f"GITHUB_API_URL must be an https URL, got {api_url!r}")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/repos/{repo}/statuses/{sha}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    # The base is the https GitHub API (checked above); the path is built here.
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=30):  # noqa: S310 - fixed https API base
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in DESCRIPTIONS:
        print(__doc__, file=sys.stderr)
        return 2
    state = argv[1]
    depth = os.environ.get("DEPTH", "full")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["HEAD_SHA"]
    if state == "pending" and depth == "light":
        statuses = github_get(api_url, token)(f"/repos/{repo}/commits/{sha}/statuses?per_page=100")
        if already_proven(statuses):
            print(f"{CONTEXT} already passed on {sha[:12]}; a light run does not take that back")
            return 0
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    target_url = f"{server}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    try:
        post(api_url, token, repo, sha, payload(state, depth, target_url))
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
        print(f"::notice::{CONTEXT} not reported: this run's token cannot write statuses (a fork?)")
        return 0
    print(f"{CONTEXT}: {state} on {sha[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

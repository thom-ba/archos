#!/usr/bin/env python3
"""Report the commit status `Full suite` on a pull request's head commit.

This status is what holds a merge until the full run has passed. `CI OK` cannot
do it: a light run's `CI OK` is green, and auto-merge merges at once when every
required check is already green, so requiring `CI OK` alone would merge a pull
request the moment auto-merge starts the run that was meant to prove it.

A status, not a job: a skipped job reports success to a required check, so a
job a light run skips would pass the gate it exists to hold.

- pending: Plan, on every pull request run. A light run says how to start the
  full one.
- success, failure: `CI OK`, on a full run only. Success needs the commit this
  run tested to be a merge of the head it reports on. Jobs check out GITHUB_SHA,
  and a run started right after a push can name the merge of the previous head,
  so without this a run could prove one commit and report on another.

A fork's read-only token cannot post a status, so for a fork that is a notice
and not a failure: a maintainer pushes the branch to this repository to merge it.

Usage: full_suite_status.py pending|success|failure
Environment: GITHUB_TOKEN (statuses: write, contents: read), GITHUB_REPOSITORY,
GITHUB_API_URL, GITHUB_SERVER_URL, GITHUB_RUN_ID, GITHUB_SHA, HEAD_SHA, HEAD_REPO, DEPTH.
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
STALE = "Tested another head than this one. Push again to re-run."
DESCRIPTIONS = {
    "pending": "Full suite running.",
    "success": "Full suite passed.",
    "failure": "Full suite failed: see this run.",
}


def payload(state: str, depth: str, target_url: str, description: str | None = None) -> dict[str, str]:
    text = description or (LIGHT if state == "pending" and depth == "light" else DESCRIPTIONS[state])
    return {"state": state, "context": CONTEXT, "description": text, "target_url": target_url}


def tested_head(merge_commit: dict) -> str:
    """The pull request head a `refs/pull/N/merge` commit merged: its second parent."""
    parents = merge_commit.get("parents", [])
    return parents[1].get("sha", "") if len(parents) > 1 else ""


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
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    target_url = f"{server}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    code = 0
    description = None
    if state == "success":
        merge_sha = os.environ["GITHUB_SHA"]
        tested = tested_head(github_get(api_url, token)(f"/repos/{repo}/git/commits/{merge_sha}"))
        if tested != sha:
            print(f"::error::this run tested {merge_sha[:12]}, a merge of {tested[:12]}, not head {sha[:12]}")
            state, description, code = "failure", STALE, 1
    try:
        post(api_url, token, repo, sha, payload(state, depth, target_url, description))
    except urllib.error.HTTPError as error:
        if error.code != 403 or os.environ.get("HEAD_REPO", repo) == repo:
            raise
        print(f"::notice::{CONTEXT} not reported: a fork's token cannot write statuses")
        return code
    print(f"{CONTEXT}: {state} on {sha[:12]}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))

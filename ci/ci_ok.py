"""The CI OK gate: every job it needs passed or was skipped.

    NEEDS='{"repo": {"result": "success"}}' task ci:ok

NEEDS is GitHub Actions' `toJSON(needs)`: each job's `result` is success,
failure, cancelled or skipped. A skipped job is one the plan did not select,
so it passes; anything else but success fails the gate and names the jobs.
"""

from __future__ import annotations

import json
import os
import sys

PASSING = ("success", "skipped")


def failing(needs: dict) -> dict[str, str]:
    return {job: state["result"] for job, state in needs.items() if state["result"] not in PASSING}


def main() -> int:
    failed = failing(json.loads(os.environ["NEEDS"]))
    if failed:
        print(f"Failing jobs: {failed}", file=sys.stderr)
        return 1
    print("All required jobs passed or were skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Which CI tiers a change touches: .github/filters.yml against `git diff`.

What dorny/paths-filter did for the Plan job, as a script any CI and a laptop
can run (`task ci:changes`). A tier is 'true' when any file the diff names
(added, modified or deleted) matches any of its patterns, with picomatch 2's
`{dot: true}` semantics on the grammar .github/filters.yml is held to
(tests/test_ci_filter_coverage.py): literal segments, `*` inside one segment,
`**` as a whole segment. A pattern outside that grammar is refused, not read
some other way.

    python3 ci/paths_filter.py --base SHA [--head REF] [--filters FILE]

The diff is two-dot, `BASE..HEAD`, as dorny's git mode compared them. An empty
`--base` means nothing to diff against, and every tier reads 'true'. Writes
`<tier>=true|false`, `<tier>_count=N` and `changes=[...]` to $GITHUB_OUTPUT,
or to stdout without it; what matched goes to stderr, for the log.

Standard library only: the Plan job runs it on the runner's own python3,
before any environment is set up.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FILTERS = Path(".github/filters.yml")

_KEY = re.compile(r"(?P<key>[a-z_]+):")
_ENTRY = re.compile(r"  - '(?P<pattern>[^']+)'")
# One segment of the shared grammar, or `**` whole (tests/test_ci_filter_coverage.py).
_SEGMENT = re.compile(r"[A-Za-z0-9_.*-]+")


class FilterError(ValueError):
    """filters.yml says something this parser does not read the way YAML would."""


def parse_filters(text: str) -> dict[str, list[str]]:
    """The tiers of filters.yml: `key:` lines, each followed by `  - 'glob'` lines.

    Anything else (an anchor, a flow list, an unquoted or double-quoted entry,
    a trailing comment) is an error, so the file cannot grow YAML this parser
    would misread; tests/test_paths_filter.py holds it to yaml.safe_load.
    """
    tiers: dict[str, list[str]] = {}
    current: list[str] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if key := _KEY.fullmatch(line):
            if key["key"] in tiers:
                raise FilterError(f"line {number}: tier {key['key']!r} is defined twice")
            current = tiers[key["key"]] = []
        elif (entry := _ENTRY.fullmatch(line)) and current is not None:
            pattern = entry["pattern"]
            if not all(s == "**" or ("**" not in s and _SEGMENT.fullmatch(s)) for s in pattern.split("/")):
                raise FilterError(f"line {number}: {pattern!r} is outside the glob grammar this filter reads")
            current.append(pattern)
        else:
            raise FilterError(f"line {number}: expected `tier:` or `  - 'glob'`, got {line!r}")
    empty = [tier for tier, patterns in tiers.items() if not patterns]
    if empty:
        raise FilterError(f"tiers without a pattern: {empty}")
    return tiers


def pattern_regex(pattern: str) -> str:
    """A grammar pattern as a regex, read as picomatch reads it with `dot: true`."""
    out = ""
    parts = pattern.split("/")
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**" and last:
            # Trailing `/**` matches the directory itself too: `a/**` matches `a`.
            out = out[:-1] + "(?:/.*)?" if out.endswith("/") else out + ".*"
        elif part == "**":
            out += "(?:[^/]+/)*"
        else:
            out += "[^/]*".join(re.escape(piece) for piece in part.split("*")) + ("" if last else "/")
    return out


def compile_tiers(tiers: dict[str, list[str]]) -> dict[str, re.Pattern[str]]:
    return {tier: re.compile("|".join(f"(?:{pattern_regex(p)})" for p in patterns)) for tier, patterns in tiers.items()}


def changed_files(base: str, head: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", f"{base}..{head}"],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout
    return [path for path in out.split("\0") if path]


def outputs(tiers: dict[str, re.Pattern[str]], files: list[str] | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The step outputs and, per tier, the files that matched. `files=None`: no base, all true."""
    values: dict[str, str] = {}
    matched: dict[str, list[str]] = {}
    for tier, regex in tiers.items():
        hits = [] if files is None else [path for path in files if regex.fullmatch(path)]
        matched[tier] = hits
        values[tier] = "true" if files is None or hits else "false"
        values[f"{tier}_count"] = str(len(hits))
    values["changes"] = json.dumps([tier for tier in tiers if values[tier] == "true"])
    return values, matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="", help="the commit to diff against; empty runs every tier")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--filters", type=Path, default=FILTERS)
    args = parser.parse_args(argv)

    tiers = compile_tiers(parse_filters(args.filters.read_text(encoding="utf-8")))
    files = changed_files(args.base, args.head) if args.base else None
    values, matched = outputs(tiers, files)

    if files is None:
        print("No diff base: every tier runs.", file=sys.stderr)
    else:
        print(f"{len(files)} files changed in {args.base}..{args.head}.", file=sys.stderr)
    for tier in tiers:
        print(f"{tier}: {values[tier]} ({len(matched[tier])} files)", file=sys.stderr)
        for path in matched[tier][:20]:
            print(f"  {path}", file=sys.stderr)

    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())

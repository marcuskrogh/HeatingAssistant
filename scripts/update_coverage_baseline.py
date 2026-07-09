#!/usr/bin/env python3
"""Regenerate coverage_baseline.json from a coverage report.

Maintainers run this after a full local fast+slow run with combined coverage:

    python3 -m pytest tests/ -m "not slow" \\
        --cov=custom_components/heating_assistant --cov-report= \\
    COVERAGE_FILE=.coverage.slow python3 -m pytest tests/ -m slow \\
        --cov=custom_components/heating_assistant --cov-append --cov-report= \\
    coverage combine .coverage .coverage.slow
    coverage report -m | tee coverage_report.txt
    python3 scripts/update_coverage_baseline.py coverage_report.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from check_coverage import BASELINE_PATH, parse_coverage_percent  # noqa: E402

# coverage report TOTAL line:
# TOTAL  11819  3092  3440   484  70.6%
_TOTAL_DETAIL_RE = re.compile(
    r"^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%",
    re.MULTILINE,
)


def parse_coverage_totals(report_text: str) -> dict[str, int | float] | None:
    match = _TOTAL_DETAIL_RE.search(report_text)
    if not match:
        return None
    stmts, missed, branches, partial, percent = match.groups()
    return {
        "total_statements": int(stmts),
        "total_missed": int(missed),
        "total_branches": int(branches),
        "partial_branches": int(partial),
        "line_coverage_percent": float(percent),
    }


def load_existing_baseline() -> dict:
    if BASELINE_PATH.is_file():
        with BASELINE_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def build_baseline(
    report_text: str,
    *,
    pytest_filter: str,
    tests_passed: int | None,
    tests_skipped: int | None,
    wall_time_seconds: float | None,
) -> dict:
    totals = parse_coverage_totals(report_text)
    if totals is None:
        percent = parse_coverage_percent(report_text)
        if percent is None:
            raise ValueError("Could not parse TOTAL line from coverage report")
        totals = {"line_coverage_percent": percent}

    existing = load_existing_baseline()
    baseline = {
        "captured_at": date.today().isoformat(),
        "pytest_filter": pytest_filter,
        **totals,
        "tests_passed": tests_passed
        if tests_passed is not None
        else existing.get("tests_passed"),
        "tests_skipped": tests_skipped
        if tests_skipped is not None
        else existing.get("tests_skipped"),
        "wall_time_seconds": wall_time_seconds
        if wall_time_seconds is not None
        else existing.get("wall_time_seconds"),
    }
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update scripts/coverage_baseline.json from a coverage report."
    )
    parser.add_argument(
        "report",
        type=Path,
        help="Path to coverage report text (e.g. from `coverage report -m`).",
    )
    parser.add_argument(
        "--pytest-filter",
        default="not slow",
        help='Recorded pytest -m filter (default: "not slow").',
    )
    parser.add_argument("--tests-passed", type=int, default=None)
    parser.add_argument("--tests-skipped", type=int, default=None)
    parser.add_argument("--wall-time-seconds", type=float, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the new baseline JSON without writing the file.",
    )
    args = parser.parse_args()

    report_text = args.report.read_text(encoding="utf-8")
    try:
        baseline = build_baseline(
            report_text,
            pytest_filter=args.pytest_filter,
            tests_passed=args.tests_passed,
            tests_skipped=args.tests_skipped,
            wall_time_seconds=args.wall_time_seconds,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    rendered = json.dumps(baseline, indent=2) + "\n"
    if args.dry_run:
        print(rendered, end="")
        return 0

    BASELINE_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {BASELINE_PATH}")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

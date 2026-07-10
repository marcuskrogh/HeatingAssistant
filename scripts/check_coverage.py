#!/usr/bin/env python3
"""Compare pytest-cov totals against the recorded baseline and package floors.

To refresh the baseline after a full local run, see ``update_coverage_baseline.py``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).with_name("coverage_baseline.json")
PACKAGE_FLOORS_PATH = Path(__file__).with_name("coverage_package_floors.json")
# pytest-cov summary line: TOTAL ... 70.6%
_TOTAL_RE = re.compile(
    r"^TOTAL\s+\d+\s+\d+\s+\d+\s+\d+\s+([\d.]+)%",
    re.MULTILINE,
)
_FILE_LINE_RE = re.compile(
    r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%"
)


def load_baseline() -> dict:
    with BASELINE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_package_floors() -> dict:
    if not PACKAGE_FLOORS_PATH.is_file():
        return {"tolerance_percent": 2.0, "packages": {}}
    with PACKAGE_FLOORS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def parse_coverage_percent(report_text: str) -> float | None:
    match = _TOTAL_RE.search(report_text)
    return float(match.group(1)) if match else None


def aggregate_package_coverage(report_text: str) -> dict[str, dict[str, int | float]]:
    """Sum statement counts per package prefix from a ``coverage report -m``."""
    floors_cfg = load_package_floors()
    prefixes = list(floors_cfg.get("packages", {}).keys())
    agg: dict[str, dict[str, int | float]] = {
        prefix: {"statements": 0, "missed": 0} for prefix in prefixes
    }
    for line in report_text.splitlines():
        match = _FILE_LINE_RE.match(line)
        if not match:
            continue
        path, stmts, missed, *_rest = match.groups()
        for prefix in prefixes:
            if path.startswith(prefix):
                bucket = agg[prefix]
                bucket["statements"] = int(bucket["statements"]) + int(stmts)
                bucket["missed"] = int(bucket["missed"]) + int(missed)
                break
    for prefix, bucket in agg.items():
        stmts = int(bucket["statements"])
        missed = int(bucket["missed"])
        bucket["line_coverage_percent"] = (
            round(100.0 * (stmts - missed) / stmts, 1) if stmts else 0.0
        )
    return agg


def check_package_floors(report_text: str) -> list[str]:
    """Return error messages for packages below their configured floor."""
    floors_cfg = load_package_floors()
    tolerance = float(floors_cfg.get("tolerance_percent", 2.0))
    package_floors: dict[str, float] = floors_cfg.get("packages", {})
    if not package_floors:
        return []

    agg = aggregate_package_coverage(report_text)
    errors: list[str] = []
    for prefix, floor in sorted(package_floors.items()):
        bucket = agg.get(prefix)
        if not bucket or int(bucket["statements"]) == 0:
            errors.append(f"Package {prefix}: no coverage data in report")
            continue
        current = float(bucket["line_coverage_percent"])
        effective_floor = floor - tolerance
        if current < effective_floor:
            errors.append(
                f"Package {prefix} regressed: {current:.1f}% < "
                f"floor {effective_floor:.1f}% (configured {floor:.1f}%)"
            )
        else:
            print(
                f"Package OK: {prefix} {current:.1f}% "
                f"(floor {effective_floor:.1f}%)"
            )
    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: check_coverage.py <pytest-cov-report.txt>", file=sys.stderr)
        return 2

    report_path = Path(sys.argv[1])
    report_text = report_path.read_text(encoding="utf-8")
    current = parse_coverage_percent(report_text)
    if current is None:
        print("Could not parse TOTAL line from coverage report", file=sys.stderr)
        return 2

    baseline = load_baseline()
    floor = baseline["line_coverage_percent"] - 0.5  # allow 0.5 pp drift
    if current < floor:
        print(
            f"Coverage regressed: {current:.1f}% < baseline floor {floor:.1f}% "
            f"(baseline {baseline['line_coverage_percent']}%)",
            file=sys.stderr,
        )
        return 1

    print(
        f"Coverage OK: {current:.1f}% (baseline {baseline['line_coverage_percent']}%, "
        f"floor {floor:.1f}%)"
    )

    package_errors = check_package_floors(report_text)
    if package_errors:
        for msg in package_errors:
            print(msg, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

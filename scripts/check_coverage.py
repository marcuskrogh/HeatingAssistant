#!/usr/bin/env python3
"""Compare pytest-cov totals against the recorded baseline.

To refresh the baseline after a full local run, see ``update_coverage_baseline.py``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).with_name("coverage_baseline.json")
# pytest-cov summary line: TOTAL ... 70.6%
_TOTAL_RE = re.compile(
    r"^TOTAL\s+\d+\s+\d+\s+\d+\s+\d+\s+([\d.]+)%",
    re.MULTILINE,
)


def load_baseline() -> dict:
    with BASELINE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def parse_coverage_percent(report_text: str) -> float | None:
    match = _TOTAL_RE.search(report_text)
    return float(match.group(1)) if match else None


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

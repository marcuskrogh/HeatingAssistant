#!/usr/bin/env bash
# Combine pytest-cov fragments uploaded from parallel CI shards.
#
# GitHub Actions download-artifact places each artifact in its own
# subdirectory, so a bare ``coverage combine`` in the job root finds nothing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

mapfile -t COV_FILES < <(
  find . -type f \( -name '.coverage.*' -o -name '.coverage' \) \
    ! -name '.coveragerc' \
    ! -path './.git/*' \
    | sort
)

if [[ ${#COV_FILES[@]} -eq 0 ]]; then
  echo "No coverage data files found under ${REPO_ROOT}" >&2
  find . -maxdepth 3 -type f | head -40 >&2 || true
  exit 1
fi

echo "Combining ${#COV_FILES[@]} coverage fragment(s):"
printf '  %s\n' "${COV_FILES[@]}"

coverage combine "${COV_FILES[@]}"
coverage report -m | tee coverage_report.txt
python3 scripts/check_coverage.py coverage_report.txt

#!/usr/bin/env bash
# Run one of four balanced pytest shards for CI parallelization.
#
# Usage:
#   ./scripts/test_shards.sh <shard-id> [extra pytest args...]
#   ./scripts/test_shards.sh 1 -m "not slow"
#
# Shard definitions live in tests/shards.json.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARDS_FILE="${REPO_ROOT}/tests/shards.json"
SHARD_ID="${1:?usage: $0 <shard-id 1-4> [pytest args...]}"
shift

if [[ ! -f "${SHARDS_FILE}" ]]; then
  echo "missing shard map: ${SHARDS_FILE}" >&2
  exit 1
fi

mapfile -t PATHS < <(
  python3 - "${SHARDS_FILE}" "${SHARD_ID}" <<'PY'
import json
import sys

shards_file, shard_id = sys.argv[1], int(sys.argv[2])
with open(shards_file, encoding="utf-8") as fh:
    data = json.load(fh)

for shard in data["shards"]:
    if shard["id"] == shard_id:
        for path in shard["paths"]:
            print(path)
        break
else:
    raise SystemExit(f"unknown shard id: {shard_id}")
PY
)

if [[ ${#PATHS[@]} -eq 0 ]]; then
  echo "shard ${SHARD_ID} has no test paths" >&2
  exit 1
fi

cd "${REPO_ROOT}"
exec python3 -m pytest "${PATHS[@]}" -v "$@"

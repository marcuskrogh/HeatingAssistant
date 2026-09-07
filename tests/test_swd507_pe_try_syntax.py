"""SWD-507: waitForPeJob must parse (try requires catch or finally)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
DETAIL = (
    ROOT
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
    / "identification"
    / "sysid-detail.js"
)
PACKAGED = (
    ROOT
    / "heating_assistant"
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
    / "identification"
    / "sysid-detail.js"
)


def _try_blocks_missing_handler(source: str) -> list[int]:
    """Line numbers of `try {` that are not followed by catch or finally."""
    bad: list[int] = []
    i = 0
    n = len(source)
    while True:
        j = source.find("try", i)
        if j < 0:
            break
        before = source[j - 1] if j else " "
        after = source[j + 3] if j + 3 < n else " "
        if before.isalnum() or before == "_" or after.isalnum() or after == "_":
            i = j + 3
            continue
        k = j + 3
        while k < n and source[k] in " \t\n":
            k += 1
        if k >= n or source[k] != "{":
            i = j + 3
            continue
        depth = 0
        p = k
        while p < n:
            if source[p] == "{":
                depth += 1
            elif source[p] == "}":
                depth -= 1
                if depth == 0:
                    rest = source[p + 1 :].lstrip()
                    if not (rest.startswith("catch") or rest.startswith("finally")):
                        bad.append(source.count("\n", 0, j) + 1)
                    break
            p += 1
        i = j + 3
    return bad


def _extract_named_function(source: str, name: str) -> str:
    markers = (f"async function {name}(", f"function {name}(")
    start = -1
    for marker in markers:
        start = source.find(marker)
        if start >= 0:
            break
    if start < 0:
        raise AssertionError(f"missing function {name}")
    brace = source.find("{", start)
    depth = 0
    for i in range(brace, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unclosed function {name}")


def test_wait_for_pe_job_is_valid_javascript() -> None:
    source = DETAIL.read_text(encoding="utf-8")
    fn = _extract_named_function(source, "waitForPeJob")
    proc = subprocess.run(
        ["node", "--check"],
        input=fn,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert _try_blocks_missing_handler(source) == []
    assert "finally {\n      hidePeOverlay();" not in source
    assert "hidePeOverlay();" in source
    assert "cancelParameterEstimation" in source
    assert "pe-progress.js?v=159" in source
    packaged = PACKAGED.read_text(encoding="utf-8")
    assert _extract_named_function(packaged, "waitForPeJob") == fn
    assert _try_blocks_missing_handler(packaged) == []

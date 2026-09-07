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
    assert "finally {\n      hidePeOverlay();" not in source
    assert "hidePeOverlay();" in source
    assert "cancelParameterEstimation" in source

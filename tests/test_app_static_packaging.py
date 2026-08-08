"""SWD-264: Ingress static assets must be included in the pip wheel."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_wheel_includes_ingress_static_assets(tmp_path: Path) -> None:
    """setuptools package-data must ship app/static for HA Ingress UI."""
    import subprocess
    import sys

    out = tmp_path / "dist"
    out.mkdir()
    # Prefer `python -m build`; fall back to `pip wheel` when build isn't installed.
    result = subprocess.run(
        [sys.executable, "-m", "build", "-w", "-o", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), "."],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0, result.stderr or result.stdout
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, wheels

    names = zipfile.ZipFile(wheels[0]).namelist()
    required = [
        "heatingassistant/app/static/index.html",
        "heatingassistant/app/static/industrial-dashboard.js",
        "heatingassistant/app/static/js/app-hass-shim.js",
        "heatingassistant/app/static/css/industrial.css",
    ]
    missing = [path for path in required if path not in names]
    assert not missing, f"wheel missing static assets: {missing}"


@pytest.mark.unit
def test_static_dir_next_to_main_contains_index() -> None:
    """Runtime _STATIC_DIR layout used by Ingress must exist in the source tree."""
    from heatingassistant.app import __main__ as app_main

    index = app_main._STATIC_DIR / "index.html"
    assert index.is_file(), index
    assert "app-hass-shim.js" in index.read_text(encoding="utf-8")

"""Tests for Ipopt/cyipopt packaging and vendor wheel selection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.heating_assistant.controller import ipopt_deps

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR = (
    REPO_ROOT
    / "custom_components"
    / "heating_assistant"
    / "vendor"
    / "cyipopt_wheels"
)


def test_manifest_does_not_require_cyipopt_wheels():
    """HAOS has no PyPI musllinux cyipopt-wheels; keep Ipopt out of manifest."""
    manifest = json.loads(
        (
            REPO_ROOT
            / "custom_components"
            / "heating_assistant"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert not any(
        "cyipopt" in req.lower() or "ipopt" in req.lower()
        for req in manifest["requirements"]
    )

def test_requirements_txt_lists_cyipopt_wheels():
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "cyipopt-wheels" in text


def test_vendor_directory_ships_platform_wheels():
    wheels = sorted(p.name for p in VENDOR.glob("*.whl"))
    assert wheels, "expected vendored cyipopt wheels for HA platforms"
    joined = " ".join(wheels).lower()
    for tag in (
        "manylinux_2_27_x86_64",
        "manylinux_2_27_aarch64",
        "musllinux_1_2_x86_64",
        "musllinux_1_2_aarch64",
    ):
        assert tag in joined, f"missing vendored wheel for {tag}"
    for py in ("cp312", "cp313"):
        assert py in joined


@pytest.mark.parametrize(
    ("python_tag", "platform_tag", "needle"),
    [
        ("cp312", "manylinux_2_27_x86_64", "manylinux"),
        ("cp312", "manylinux_2_27_aarch64", "aarch64"),
        ("cp313", "musllinux_1_2_x86_64", "musllinux"),
        ("cp313", "musllinux_1_2_aarch64", "musllinux"),
    ],
)
def test_select_vendor_wheel_covers_linux_matrix(python_tag, platform_tag, needle):
    wheels = ipopt_deps.list_vendor_wheels()
    assert wheels
    chosen = ipopt_deps.select_vendor_wheel(
        wheels,
        python_tag=python_tag,
        platform_tags=(platform_tag,),
    )
    assert chosen is not None
    assert python_tag in chosen.name.lower()
    assert needle in chosen.name.lower()
    arch = platform_tag.split("_")[-1]
    assert arch in chosen.name.lower()


def test_pip_install_command_is_binary_only_and_no_deps():
    cmd = ipopt_deps._pip_install_command("cyipopt-wheels")
    assert cmd[:4] == [cmd[0], "-m", "pip", "install"]
    assert "--no-deps" in cmd
    assert "--only-binary=:all:" in cmd
    assert "--upgrade" not in cmd
    assert cmd[-1] == "cyipopt-wheels"


def test_pip_install_surfaces_stderr_on_failure(monkeypatch):
    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "ERROR: No matching distribution found for cyipopt-wheels"

    monkeypatch.setattr(ipopt_deps.subprocess, "run", lambda *_a, **_k: _Completed())
    with pytest.raises(RuntimeError, match="No matching distribution"):
        ipopt_deps._pip_install("cyipopt-wheels")


def test_select_vendor_wheel_prefers_matching_python_and_platform(tmp_path):
    names = [
        "cyipopt_wheels-1.7.0.dev0-cp312-cp312-manylinux_2_28_x86_64.whl",
        "cyipopt_wheels-1.7.0.dev0-cp313-cp313-manylinux_2_28_x86_64.whl",
        "cyipopt-1.7.0-cp312-cp312-musllinux_1_2_x86_64.whl",
    ]
    wheels = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"0")
        wheels.append(path)

    chosen = ipopt_deps.select_vendor_wheel(
        wheels,
        python_tag="cp312",
        platform_tags=("manylinux_2_28_x86_64", "linux_x86_64"),
    )
    assert chosen is not None
    assert "cp312" in chosen.name
    assert "manylinux" in chosen.name

    musl = ipopt_deps.select_vendor_wheel(
        wheels,
        python_tag="cp312",
        platform_tags=("musllinux_1_2_x86_64",),
    )
    assert musl is not None
    assert "musllinux" in musl.name


def test_ensure_cyipopt_reports_already_present_when_importable(monkeypatch):
    monkeypatch.setattr(ipopt_deps, "cyipopt_importable", lambda: True)
    result = ipopt_deps.ensure_cyipopt_installed()
    assert result.available is True
    assert result.source == "already-present"


def test_ensure_cyipopt_installs_vendor_wheel_when_pip_fails(monkeypatch, tmp_path):
    wheel = tmp_path / "cyipopt_wheels-1.7.0.dev0-cp312-cp312-manylinux_2_28_x86_64.whl"
    wheel.write_bytes(b"0")
    calls: list[str] = []
    state = {"ok": False}

    def fake_importable() -> bool:
        return state["ok"]

    def fake_pip(req: str) -> None:
        calls.append(req)
        if req == str(wheel):
            state["ok"] = True
        elif req == ipopt_deps.CYIPOPT_WHEELS_REQUIREMENT:
            raise RuntimeError("no musllinux wheel on index")

    monkeypatch.setattr(ipopt_deps, "cyipopt_importable", fake_importable)
    monkeypatch.setattr(ipopt_deps, "_pip_install", fake_pip)
    monkeypatch.setattr(ipopt_deps, "_python_tag", lambda: "cp312")
    monkeypatch.setattr(ipopt_deps, "_looks_like_musl", lambda: False)
    monkeypatch.setattr(
        ipopt_deps,
        "_platform_tags",
        lambda: ("manylinux_2_28_x86_64",),
    )

    result = ipopt_deps.ensure_cyipopt_installed(vendor_dir=tmp_path)
    assert result.available is True
    assert result.source == "vendor-wheel"
    assert calls[0] == ipopt_deps.CYIPOPT_WHEELS_REQUIREMENT
    assert calls[1] == str(wheel)


def test_ensure_cyipopt_prefers_vendor_first_on_musl(monkeypatch, tmp_path):
    wheel = tmp_path / "cyipopt_wheels-1.7.0.dev0-cp312-cp312-musllinux_1_2_x86_64.whl"
    wheel.write_bytes(b"0")
    calls: list[str] = []
    state = {"ok": False}

    def fake_importable() -> bool:
        return state["ok"]

    def fake_pip(req: str) -> None:
        calls.append(req)
        if req == str(wheel):
            state["ok"] = True

    monkeypatch.setattr(ipopt_deps, "cyipopt_importable", fake_importable)
    monkeypatch.setattr(ipopt_deps, "_pip_install", fake_pip)
    monkeypatch.setattr(ipopt_deps, "_python_tag", lambda: "cp312")
    monkeypatch.setattr(ipopt_deps, "_looks_like_musl", lambda: True)
    monkeypatch.setattr(
        ipopt_deps,
        "_platform_tags",
        lambda: ("musllinux_1_2_x86_64",),
    )

    result = ipopt_deps.ensure_cyipopt_installed(vendor_dir=tmp_path)
    assert result.available is True
    assert result.source == "vendor-wheel"
    assert calls[0] == str(wheel)
    assert ipopt_deps.CYIPOPT_WHEELS_REQUIREMENT not in calls


def test_mode_selector_labels_omit_solver_names():
    source = (
        REPO_ROOT
        / "custom_components"
        / "heating_assistant"
        / "www"
        / "js"
        / "pages"
        / "tuning-controller.js"
    ).read_text(encoding="utf-8")
    assert "{ value: 'linear', label: 'Linear' }" in source
    assert "{ value: 'non-linear', label: 'Non-linear' }" in source
    assert "Linear (HiGHS)" not in source
    assert "Non-linear (Ipopt)" not in source
    assert "nonlinear_available" in source
    assert "ipopt_unavailable_reason" not in source
    assert ".whl" not in source
    assert "cyipopt" not in source.lower()


def test_mpc_mode_help_omits_solver_names_beside_modes():
    for rel in (
        "strings.json",
        "translations/en.json",
        "services.yaml",
    ):
        text = (
            REPO_ROOT / "custom_components" / "heating_assistant" / rel
        ).read_text(encoding="utf-8")
        # Mode help should describe the trade-off without naming solvers.
        assert "HiGHS" not in text
        assert "Ipopt" not in text
        assert "wheel" not in text.lower()
        assert "cyipopt" not in text.lower()
        assert "fidelity" in text.lower() or "accuracy" in text.lower()


def test_async_setup_entry_prepares_ipopt_off_event_loop():
    init_source = (
        REPO_ROOT
        / "custom_components"
        / "heating_assistant"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "async_add_executor_job" in init_source
    assert "ensure_cyipopt_installed" in init_source
    assert "probe_nonlinear_backend" in init_source
    assert "_prepare_ipopt_backend" in init_source

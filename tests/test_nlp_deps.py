"""Tests for SciPy-only NLP packaging and startup probe wiring."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from custom_components.heating_assistant.controller import nlp_probe

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_does_not_require_cyipopt_or_ipopt():
    """Native Ipopt/cyipopt must never be a HA requirement (startup hang risk)."""
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


def test_requirements_txt_does_not_list_cyipopt():
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "cyipopt" not in text.lower()
    assert "ipopt" not in text.lower()


def test_vendor_cyipopt_wheels_directory_removed():
    vendor = (
        REPO_ROOT
        / "custom_components"
        / "heating_assistant"
        / "vendor"
        / "cyipopt_wheels"
    )
    assert not vendor.exists()


def test_ipopt_deps_module_removed():
    path = (
        REPO_ROOT
        / "custom_components"
        / "heating_assistant"
        / "controller"
        / "ipopt_deps.py"
    )
    assert not path.exists()


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
    assert "ipopt_available" not in source
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
        assert "HiGHS" not in text
        assert "Ipopt" not in text
        assert "wheel" not in text.lower()
        assert "cyipopt" not in text.lower()
        assert "fidelity" in text.lower() or "accuracy" in text.lower()


def test_async_setup_entry_probes_scipy_off_event_loop():
    init_source = (
        REPO_ROOT
        / "custom_components"
        / "heating_assistant"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "async_add_executor_job" in init_source
    assert "probe_nonlinear_backend" in init_source
    assert "ensure_cyipopt_installed" not in init_source
    assert "ipopt_deps" not in init_source
    assert "cyipopt" not in init_source.lower()


def test_scipy_nlp_probe_reports_success(monkeypatch) -> None:
    class FakeBackend:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            return type(
                "Result",
                (),
                {"success": True, "x": np.array([1.0]), "message": "", "fun": 0.0},
            )()

    monkeypatch.setattr(nlp_probe, "ScipyNLPBackend", FakeBackend)

    result = nlp_probe.probe_nonlinear_backend()

    assert result.available is True
    assert result.backend == nlp_probe.BACKEND_SCIPY
    assert result.reason is None


def test_scipy_nlp_probe_reports_failure(monkeypatch) -> None:
    class FakeBackend:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            raise RuntimeError("scipy unavailable")

    monkeypatch.setattr(nlp_probe, "ScipyNLPBackend", FakeBackend)

    result = nlp_probe.probe_nonlinear_backend()

    assert result.available is False
    assert result.backend is None
    assert "scipy unavailable" in (result.reason or "")


def test_no_pip_or_subprocess_in_nlp_probe_module():
    source = (
        REPO_ROOT
        / "custom_components"
        / "heating_assistant"
        / "controller"
        / "nlp_probe.py"
    ).read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "pip" not in source
    assert "cyipopt" not in source.lower()
    assert "IpoptNLP" not in source

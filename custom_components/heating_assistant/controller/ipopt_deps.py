"""Ensure a working ``cyipopt`` (Ipopt) install for non-linear MPC.

Official ``cyipopt`` has no usable binary wheels for Home Assistant. We try a
**light** install path only:

1. Prefer a matching wheel under ``vendor/cyipopt_wheels/`` (especially on
   musllinux / HAOS, where PyPI has no musllinux ``cyipopt-wheels``).
2. Optionally try PyPI ``cyipopt-wheels`` with ``--only-binary=:all:`` (glibc).

Do **not** declare ``cyipopt-wheels`` in ``manifest.json`` — a failed HA
requirement install can block the whole integration on HAOS.
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import site
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Iterable

_LOGGER = logging.getLogger(__name__)

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "cyipopt_wheels"
CYIPOPT_WHEELS_REQUIREMENT = "cyipopt-wheels"


@dataclass(frozen=True)
class CyipoptInstallResult:
    """Outcome of ensuring ``cyipopt`` is importable."""

    available: bool
    source: str
    detail: str | None = None


def cyipopt_importable() -> bool:
    """Return True when ``import cyipopt`` succeeds."""
    try:
        import_module("cyipopt")
    except Exception:  # noqa: BLE001 — any import failure means unavailable
        return False
    return True


def _refresh_import_path() -> None:
    """Make a just-installed wheel visible to this interpreter."""
    try:
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path:
            site.addsitedir(user_site)
    except Exception:  # noqa: BLE001
        pass
    importlib.invalidate_caches()
    # Drop a failed/partial import so the next import_module reloads.
    sys.modules.pop("cyipopt", None)
    for key in list(sys.modules):
        if key.startswith("cyipopt."):
            sys.modules.pop(key, None)


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _normalize_machine(machine: str | None = None) -> str:
    m = (machine or platform.machine()).lower()
    if m in {"amd64", "x86_64"}:
        return "x86_64"
    if m in {"aarch64", "arm64"}:
        return "aarch64"
    if m.startswith("armv7"):
        return "armv7l"
    return m


def _looks_like_musl() -> bool:
    host = sysconfig.get_config_var("HOST_GNU_TYPE") or ""
    if "musl" in str(host):
        return True
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        return bool(getattr(libc, "gnu_get_libc_version", None) is None)
    except Exception:  # noqa: BLE001
        return False


def _platform_tags() -> tuple[str, ...]:
    """Return ordered platform tag fragments to match against wheel filenames."""
    machine = _normalize_machine()
    system = platform.system().lower()
    tags: list[str] = []
    musl = _looks_like_musl()

    if system == "linux":
        try:
            from packaging.tags import sys_tags  # type: ignore

            for tag in sys_tags():
                plat = tag.platform
                if "musllinux" in plat or "manylinux" in plat or plat.startswith("linux_"):
                    tags.append(plat)
                    if len(tags) >= 12:
                        break
        except Exception:  # noqa: BLE001
            pass
        if musl:
            tags.extend(
                [
                    f"musllinux_1_2_{machine}",
                    f"musllinux_1_1_{machine}",
                ]
            )
        tags.extend(
            [
                f"manylinux_2_28_{machine}",
                f"manylinux_2_27_{machine}",
                f"manylinux_2_17_{machine}",
                f"manylinux2014_{machine}",
                f"linux_{machine}",
            ]
        )
    elif system == "darwin":
        tags.extend(
            [
                "macosx_11_0_arm64" if machine == "aarch64" else "macosx_11_0_x86_64",
                "macosx_10_9_x86_64",
            ]
        )
    elif system == "windows":
        tags.append("win_amd64" if machine == "x86_64" else f"win_{machine}")

    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return tuple(ordered)


def list_vendor_wheels(vendor_dir: Path | None = None) -> list[Path]:
    """Return vendored wheel paths (sorted)."""
    root = vendor_dir or VENDOR_DIR
    if not root.is_dir():
        return []
    return sorted(root.glob("*.whl"))


def select_vendor_wheel(
    wheels: Iterable[Path] | None = None,
    *,
    python_tag: str | None = None,
    platform_tags: Iterable[str] | None = None,
) -> Path | None:
    """Pick the best vendored wheel for this interpreter/platform."""
    py = python_tag or _python_tag()
    plats = tuple(platform_tags) if platform_tags is not None else _platform_tags()
    candidates = list(wheels) if wheels is not None else list_vendor_wheels()
    matches: list[Path] = []
    for wheel in candidates:
        name = wheel.name.lower()
        if py not in name:
            continue
        if any(tag.lower() in name for tag in plats):
            matches.append(wheel)
    if not matches:
        return None
    if _looks_like_musl():
        for wheel in matches:
            if "musllinux" in wheel.name.lower():
                return wheel
    return matches[0]


def _pip_install_command(requirement_or_path: str) -> list[str]:
    """Build a fail-fast, binary-only pip install command for cyipopt wheels."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--only-binary=:all:",
        "--no-cache-dir",
        requirement_or_path,
    ]
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if not in_venv and os.environ.get("VIRTUAL_ENV") is None:
        cmd.insert(-1, "--user")
    return cmd


def _pip_install(requirement_or_path: str) -> None:
    """Install a requirement or local wheel with pip (same interpreter)."""
    cmd = _pip_install_command(requirement_or_path)
    _LOGGER.info("Heating Assistant: installing Ipopt backend via %s", " ".join(cmd))
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        tail = stderr[-800:] if stderr else f"exit {completed.returncode}"
        raise RuntimeError(tail)
    _refresh_import_path()


def _try_vendor_install(vendor_dir: Path | None) -> CyipoptInstallResult | None:
    wheel = select_vendor_wheel(list_vendor_wheels(vendor_dir))
    if wheel is None:
        return None
    try:
        _pip_install(str(wheel))
        if cyipopt_importable():
            return CyipoptInstallResult(True, "vendor-wheel", wheel.name)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "Heating Assistant: installing vendored wheel %s failed: %s",
            wheel.name,
            exc,
        )
        return CyipoptInstallResult(False, "vendor-wheel-failed", str(exc)[:400])
    return CyipoptInstallResult(False, "vendor-wheel-failed", wheel.name)


def _try_pypi_install() -> CyipoptInstallResult | None:
    try:
        _pip_install(CYIPOPT_WHEELS_REQUIREMENT)
        if cyipopt_importable():
            return CyipoptInstallResult(True, "pip-cyipopt-wheels")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "Heating Assistant: pip install %s failed: %s",
            CYIPOPT_WHEELS_REQUIREMENT,
            exc,
        )
        return CyipoptInstallResult(False, "pip-failed", str(exc)[:400])
    return None


def ensure_cyipopt_installed(*, vendor_dir: Path | None = None) -> CyipoptInstallResult:
    """Make ``cyipopt`` importable using vendored wheels and/or PyPI."""
    _refresh_import_path()
    if cyipopt_importable():
        return CyipoptInstallResult(True, "already-present")

    musl = _looks_like_musl()
    # HAOS / musl: PyPI has no musllinux cyipopt-wheels — try vendor first.
    # Evaluate attempts lazily so a successful vendor install skips PyPI.
    attempt_fns = (
        (
            lambda: _try_vendor_install(vendor_dir),
            _try_pypi_install,
        )
        if musl
        else (
            _try_pypi_install,
            lambda: _try_vendor_install(vendor_dir),
        )
    )

    failed: list[CyipoptInstallResult] = []
    for attempt in attempt_fns:
        result = attempt()
        if result is None:
            continue
        if result.available:
            return result
        failed.append(result)

    if cyipopt_importable():
        return CyipoptInstallResult(True, "already-present")

    detail = (
        f"no matching wheel for {_python_tag()} / {_normalize_machine()}; "
        f"musl={musl}"
    )
    if failed:
        detail = f"{detail}; last={failed[-1].source}"
    return CyipoptInstallResult(False, "unavailable", detail)

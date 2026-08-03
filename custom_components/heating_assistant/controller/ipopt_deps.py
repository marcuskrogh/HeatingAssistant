"""Ensure a working ``cyipopt`` (Ipopt) install for non-linear MPC.

Home Assistant installs ``manifest.json`` requirements via pip. Official
``cyipopt`` publishes no usable binary wheels, so we depend on
``cyipopt-wheels`` (bundled Ipopt) and also ship matching wheels under
``vendor/cyipopt_wheels/`` for platforms HA's index does not cover
(especially musllinux / HAOS).
"""

from __future__ import annotations

import logging
import os
import platform
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


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tags() -> tuple[str, ...]:
    """Return ordered platform tag fragments to match against wheel filenames."""
    machine = platform.machine().lower()
    system = platform.system().lower()
    tags: list[str] = []

    if system == "linux":
        # musl (HAOS) vs glibc (Container / many supervised installs).
        is_musl = "musl" in (sysconfig.get_config_var("HOST_GNU_TYPE") or "")
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
        if is_musl or _looks_like_musl():
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
                "macosx_11_0_arm64" if machine in {"arm64", "aarch64"} else "macosx_11_0_x86_64",
                "macosx_10_9_x86_64",
            ]
        )
    elif system == "windows":
        tags.append("win_amd64" if machine in {"amd64", "x86_64"} else f"win_{machine}")

    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return tuple(ordered)


def _looks_like_musl() -> bool:
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        return bool(getattr(libc, "gnu_get_libc_version", None) is None)
    except Exception:  # noqa: BLE001
        return False


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
    # Prefer musllinux when present on this host, else first match.
    if _looks_like_musl():
        for wheel in matches:
            if "musllinux" in wheel.name.lower():
                return wheel
    return matches[0]


def _pip_install(requirement_or_path: str) -> None:
    """Install a requirement or local wheel with pip (same interpreter)."""
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-cache-dir",
        requirement_or_path,
    ]
    # Prefer user site when not in a venv (HA Core often uses a system env).
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if not in_venv and os.environ.get("VIRTUAL_ENV") is None:
        cmd.insert(-1, "--user")
    _LOGGER.info("Heating Assistant: installing Ipopt backend via %s", " ".join(cmd))
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def ensure_cyipopt_installed(*, vendor_dir: Path | None = None) -> CyipoptInstallResult:
    """Make ``cyipopt`` importable using PyPI wheels and/or vendored wheels."""
    if cyipopt_importable():
        return CyipoptInstallResult(True, "already-present")

    # 1) Try the PyPI project that ships bundled Ipopt binaries.
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

    # 2) Fall back to a matching wheel shipped with the integration.
    wheel = select_vendor_wheel(list_vendor_wheels(vendor_dir))
    if wheel is not None:
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
            return CyipoptInstallResult(False, "vendor-wheel-failed", str(exc))

    if cyipopt_importable():
        return CyipoptInstallResult(True, "already-present")

    available = list_vendor_wheels(vendor_dir)
    detail = (
        f"no matching wheel for {_python_tag()} / {platform.machine()}; "
        f"vendored={', '.join(p.name for p in available) or 'none'}"
    )
    return CyipoptInstallResult(False, "unavailable", detail)

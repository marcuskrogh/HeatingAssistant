"""Shared validation for MPC mode selections."""

from __future__ import annotations

from typing import Any, MutableMapping

from .const import CONF_MPC_MODE, MPC_MODE_NONLINEAR, normalize_mpc_mode


class MpcModeUnavailableError(ValueError):
    """Raised when non-linear MPC is requested without a usable NLP backend."""


def coordinator_nonlinear_available(coordinator: Any) -> bool:
    """Return whether a coordinator has a usable SciPy NLP backend."""
    return bool(getattr(coordinator, "_nonlinear_available", False))


def coordinator_nonlinear_backend(coordinator: Any) -> str | None:
    """Return the probed NLP backend name, or None when unavailable."""
    if not coordinator_nonlinear_available(coordinator):
        return None
    return "scipy"


def mpc_mode_unavailable_message(reason: str | None = None) -> str:
    """Return a short user-facing explanation for unavailable non-linear MPC.

    ``reason`` is accepted for call-site compatibility but intentionally ignored —
    never dump probe internals into UI copy.
    """
    _ = reason
    return (
        "Non-linear MPC is temporarily unavailable because the SciPy NLP solver "
        "did not pass the startup probe. Try restarting Home Assistant."
    )


def validate_mpc_mode_available(
    value: Any,
    *,
    nonlinear_available: bool | None = None,
    unavailable_reason: str | None = None,
    legacy_solver: Any = None,
) -> str:
    """Normalize an MPC mode and reject non-linear when SciPy NLP is not ready."""
    mode = normalize_mpc_mode(value, legacy_solver=legacy_solver)
    if mode == MPC_MODE_NONLINEAR and not bool(nonlinear_available):
        raise MpcModeUnavailableError(
            mpc_mode_unavailable_message(unavailable_reason)
        )
    return mode


def validate_mpc_mode_update(
    coordinator: Any,
    updates: MutableMapping[str, Any],
) -> None:
    """Normalize and validate ``mpc_mode`` in a mutable update mapping."""
    if CONF_MPC_MODE not in updates:
        return
    updates[CONF_MPC_MODE] = validate_mpc_mode_available(
        updates.get(CONF_MPC_MODE),
        nonlinear_available=coordinator_nonlinear_available(coordinator),
        unavailable_reason=getattr(
            coordinator, "_nonlinear_unavailable_reason", None
        ),
    )

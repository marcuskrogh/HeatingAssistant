"""Shared validation for MPC mode selections."""

from __future__ import annotations

from typing import Any, MutableMapping

from .const import CONF_MPC_MODE, MPC_MODE_NONLINEAR, normalize_mpc_mode


class MpcModeUnavailableError(ValueError):
    """Raised when non-linear MPC is requested without a usable IPOPT backend."""


def mpc_mode_unavailable_message(reason: str | None = None) -> str:
    """Return a user-facing explanation for unavailable non-linear MPC."""
    suffix = f" Probe result: {reason}" if reason else ""
    return (
        "Non-linear MPC requires the IPOPT solver, but the startup capability "
        f"probe did not pass.{suffix}"
    )


def validate_mpc_mode_available(
    value: Any,
    *,
    ipopt_available: bool,
    unavailable_reason: str | None = None,
    legacy_solver: Any = None,
) -> str:
    """Normalize an MPC mode and reject non-linear mode when IPOPT is unavailable."""
    mode = normalize_mpc_mode(value, legacy_solver=legacy_solver)
    if mode == MPC_MODE_NONLINEAR and not ipopt_available:
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
        ipopt_available=bool(getattr(coordinator, "_ipopt_available", False)),
        unavailable_reason=getattr(coordinator, "_ipopt_unavailable_reason", None),
    )

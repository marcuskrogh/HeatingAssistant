"""Create or clear the Settings Restart required repair (HACS path)."""

from __future__ import annotations

from typing import Any

from .const import DOMAIN, NAME, VERSION
from .version_sync import disk_manifest_version, restart_required

ISSUE_ID = "restart_required"
RESTART_POLL_SECONDS = 30


def _issue_registry() -> Any:
    from homeassistant.helpers import issue_registry as ir

    return ir


def sync_restart_issue(hass: Any, *, needed: bool | None = None) -> bool:
    """Create or delete the repair. Return True when a restart is needed.

    Settings shows this under **Repairs**, not **Updates**. After Core restart
    the loaded VERSION matches disk, so the issue is removed
    (``is_persistent=False`` also drops it across restart).
    """

    ir = _issue_registry()
    if needed is None:
        needed = restart_required()
    if needed:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_ID,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="restart_required",
            translation_placeholders={
                "name": NAME,
                "version": disk_manifest_version() or VERSION,
            },
        )
        return True
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
    return False

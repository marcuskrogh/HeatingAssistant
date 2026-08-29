"""Shared constants for HeatingRuntime and its collaborator mixins."""

from __future__ import annotations

import logging

_logger = logging.getLogger("heatingassistant.app.runtime")

# Keep ~48h of update_interval samples in memory for Ingress plots (SWD-269/277).
# Durable copy lives under ``<data_dir>/plot_history/`` (SWD-281).
_HISTORY_MAX_SAMPLES = 12_000
_HISTORY_MIN_INTERVAL_S = 5.0

# Synthetic sensor the room Price plot reads via /api/history (SWD-284).
_ELECTRICITY_PRICE_ENTITY = "sensor.heating_assistant_electricity_price"

# Retry Supervisor MQTT discovery while disconnected (SWD-273).
_MQTT_DISCOVERY_RETRY_INITIAL_S = 2.0
_MQTT_DISCOVERY_RETRY_MAX_S = 60.0

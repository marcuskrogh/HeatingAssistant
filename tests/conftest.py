"""
pytest configuration and stubs for running tests without a full HA install.

The custom_components/__init__.py imports several Home Assistant packages
(voluptuous, homeassistant.*, etc.) that are not available in the CI
test environment.  This conftest installs lightweight stubs before any
test module is collected, so that imports of the HA-independent submodules
(thermal_model, model_diagnostics, parameter_estimator, etc.) succeed.
"""

from __future__ import annotations

import sys
import types


def _stub_module(name: str) -> types.ModuleType:
    """Create and register an empty stub module (and all parent packages)."""
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = []  # type: ignore[attr-defined]
            sys.modules[pkg] = mod
    return sys.modules[name]


# ── Build all needed HA stubs ─────────────────────────────────────────────

_HA_PACKAGES = [
    "voluptuous",
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.helpers",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.event",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.config_validation",
    "homeassistant.helpers.entity_platform",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.components.climate",
    "homeassistant.components.button",
    "homeassistant.components.number",
    "homeassistant.components.select",
    "homeassistant.components.notify",
]

for _pkg in _HA_PACKAGES:
    _stub_module(_pkg)


# ── Attach commonly-used symbols ──────────────────────────────────────────

class _FakeEnum:
    """Stand-in for HA string-enum classes."""
    CELSIUS = "°C"
    MEASUREMENT = "measurement"
    TOTAL = "total"
    HUMIDITY = "humidity"
    TEMPERATURE = "temperature"
    POWER = "power"
    ENERGY = "energy"
    SENSOR = "sensor"
    CLIMATE = "climate"
    BUTTON = "button"


# homeassistant.core
_ha_core = sys.modules["homeassistant.core"]
_ha_core.HomeAssistant = object  # type: ignore[attr-defined]
_ha_core.callback = lambda f: f  # type: ignore[attr-defined]
_ha_core.ServiceCall = object  # type: ignore[attr-defined]

# homeassistant.config_entries
_ce = sys.modules["homeassistant.config_entries"]
_ce.ConfigEntry = object  # type: ignore[attr-defined]
_ce.ConfigFlow = object  # type: ignore[attr-defined]
_ce.OptionsFlow = object  # type: ignore[attr-defined]
_ce.SOURCE_USER = "user"  # type: ignore[attr-defined]

# homeassistant.const
_const = sys.modules["homeassistant.const"]
for _attr in [
    "CONF_NAME", "CONF_LATITUDE", "CONF_LONGITUDE",
    "STATE_UNAVAILABLE", "STATE_UNKNOWN",
    "TEMP_CELSIUS", "PERCENTAGE", "Platform",
    "ATTR_TEMPERATURE",
]:
    setattr(_const, _attr, _attr)

_const.UnitOfTemperature = _FakeEnum  # type: ignore[attr-defined]
_const.UnitOfPower = _FakeEnum  # type: ignore[attr-defined]

# homeassistant.helpers.entity
_entity = sys.modules["homeassistant.helpers.entity"]
_entity.Entity = object  # type: ignore[attr-defined]

# homeassistant.helpers.update_coordinator
class _DataUpdateCoordinatorStub:
    pass


class _CoordinatorEntityStub:
    def __init__(self, coordinator=None):
        self._coordinator_stub = coordinator


_coord = sys.modules["homeassistant.helpers.update_coordinator"]
_coord.DataUpdateCoordinator = _DataUpdateCoordinatorStub  # type: ignore[attr-defined]
_coord.CoordinatorEntity = _CoordinatorEntityStub  # type: ignore[attr-defined]
_coord.UpdateFailed = Exception  # type: ignore[attr-defined]

# homeassistant.helpers.entity_platform
_ep = sys.modules["homeassistant.helpers.entity_platform"]
_ep.AddEntitiesCallback = object  # type: ignore[attr-defined]

# homeassistant.helpers.config_validation – simple passthrough stubs
_cv = sys.modules["homeassistant.helpers.config_validation"]
_cv.string = str  # type: ignore[attr-defined]
_cv.positive_float = float  # type: ignore[attr-defined]
_cv.positive_int = int  # type: ignore[attr-defined]
_cv.boolean = bool  # type: ignore[attr-defined]
_cv.entity_id = str  # type: ignore[attr-defined]
_cv.entity_ids = list  # type: ignore[attr-defined]
_cv.ensure_list = list  # type: ignore[attr-defined]
_cv.make_entity_service_schema = lambda schema: schema  # type: ignore[attr-defined]

# homeassistant.components.sensor
_sensor = sys.modules["homeassistant.components.sensor"]
_sensor.SensorEntity = object  # type: ignore[attr-defined]
_sensor.SensorDeviceClass = _FakeEnum  # type: ignore[attr-defined]
_sensor.SensorStateClass = _FakeEnum  # type: ignore[attr-defined]

# homeassistant.components.climate
_climate = sys.modules["homeassistant.components.climate"]
class _ClimateEntityBase:
    """Distinct base class so multi-inheritance with CoordinatorEntity works."""

_climate.ClimateEntity = _ClimateEntityBase  # type: ignore[attr-defined]


class _ClimateEntityFeatureStub:
    """Minimal HA ClimateEntityFeature stub with bit-flag-like values."""
    TARGET_TEMPERATURE = 1
    TARGET_TEMPERATURE_RANGE = 2


class _HVACModeStub:
    """Minimal HA HVACMode stub exposing the values used by the integration."""
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"
    AUTO = "auto"
    DRY = "dry"
    FAN_ONLY = "fan_only"


class _HVACActionStub:
    """Minimal HA HVACAction stub used by the climate entity."""
    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


_climate.ClimateEntityFeature = _ClimateEntityFeatureStub  # type: ignore[attr-defined]
_climate.HVACMode = _HVACModeStub  # type: ignore[attr-defined]
_climate.HVACAction = _HVACActionStub  # type: ignore[attr-defined]

# homeassistant.components.button
_button = sys.modules["homeassistant.components.button"]
_button.ButtonEntity = object  # type: ignore[attr-defined]

# voluptuous
_vol = sys.modules["voluptuous"]
_vol.Schema = lambda schema=None, **kw: (lambda x: x)  # type: ignore[attr-defined]
_vol.Required = lambda key, **kw: key  # type: ignore[attr-defined]
_vol.Optional = lambda key, **kw: key  # type: ignore[attr-defined]
_vol.All = lambda *a: a[0] if a else None  # type: ignore[attr-defined]
_vol.Coerce = lambda t: t  # type: ignore[attr-defined]
_vol.Range = lambda **kw: None  # type: ignore[attr-defined]
_vol.In = lambda v: None  # type: ignore[attr-defined]
_vol.REMOVE_EXTRA = "REMOVE_EXTRA"  # type: ignore[attr-defined]
_vol.ALLOW_EXTRA = "ALLOW_EXTRA"  # type: ignore[attr-defined]
_vol.PREVENT_EXTRA = "PREVENT_EXTRA"  # type: ignore[attr-defined]

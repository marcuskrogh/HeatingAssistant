"""
pytest configuration and Home Assistant stubs.

SWD-262 moved compute tests to ``heatingassistant.engine``.  The remaining
stubs let bridge-level modules and explicitly skipped legacy HA tests import in
CI without installing a full Home Assistant runtime.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

pytest_plugins = ["tests.helpers.estimation_fixtures"]

_TIER_MARKERS = frozenset({"unit", "integration", "system"})


def pytest_configure(config: pytest.Config) -> None:
    """Register tier markers."""
    config.addinivalue_line(
        "markers",
        "unit: pure logic tests with no coordinator/HA wiring",
    )
    config.addinivalue_line(
        "markers",
        "integration: multi-module tests with coordinator stubs or mocked HA",
    )
    config.addinivalue_line(
        "markers",
        "system: end-to-end smoke tests across package boundaries",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (multi-start Nelder-Mead parameter estimation)",
    )

def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-mark top-level ``tests/test_*.py`` items as unit unless tier-tagged."""
    tests_dir = Path(__file__).resolve().parent
    for item in items:
        if any(item.get_closest_marker(name) for name in _TIER_MARKERS):
            continue
        path = Path(str(item.fspath))
        if path.parent == tests_dir and path.name.startswith("test_"):
            item.add_marker(pytest.mark.unit)


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
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.event",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.config_validation",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.reload",
    "homeassistant.helpers.service",
    "homeassistant.components",
    "homeassistant.components.websocket_api",
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
    WATT = "W"
    KILO_WATT_HOUR = "kWh"
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"
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
_ha_core.ServiceResponse = dict  # type: ignore[attr-defined]


class _SupportsResponseStub:
    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


_ha_core.SupportsResponse = _SupportsResponseStub  # type: ignore[attr-defined]

# homeassistant.exceptions
_exc = sys.modules["homeassistant.exceptions"]
_exc.ConfigEntryNotReady = Exception  # type: ignore[attr-defined]
_exc.ConfigEntryAuthFailed = Exception  # type: ignore[attr-defined]

# homeassistant.config_entries
_ce = sys.modules["homeassistant.config_entries"]
_ce.ConfigEntry = object  # type: ignore[attr-defined]


class _ConfigFlowStub:
    """Stub ConfigFlow that accepts `domain=` kwarg via __init_subclass__."""

    def __init_subclass__(cls, *, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


class _OptionsFlowStub:
    pass


_ce.ConfigFlow = _ConfigFlowStub  # type: ignore[attr-defined]
_ce.OptionsFlow = _OptionsFlowStub  # type: ignore[attr-defined]
_ce.SOURCE_USER = "user"  # type: ignore[attr-defined]
_ce.ConfigFlowResult = dict  # type: ignore[attr-defined]

# homeassistant.helpers.selector – passthrough stubs sufficient for import
_stub_module("homeassistant.helpers.selector")
_sel = sys.modules["homeassistant.helpers.selector"]


class _SelectorPassthrough:
    """Generic stub that swallows any kwargs and returns a callable."""

    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs

    def __call__(self, value):
        return value


for _attr in [
    "EntitySelector",
    "EntitySelectorConfig",
    "NumberSelector",
    "NumberSelectorConfig",
    "SelectSelector",
    "SelectSelectorConfig",
    "TextSelector",
    "TextSelectorConfig",
    "TimeSelector",
    "TimeSelectorConfig",
    "BooleanSelector",
    "BooleanSelectorConfig",
    "ObjectSelector",
    "ObjectSelectorConfig",
]:
    setattr(_sel, _attr, _SelectorPassthrough)


class _NumberSelectorModeStub:
    BOX = "box"
    SLIDER = "slider"


class _SelectSelectorModeStub:
    LIST = "list"
    DROPDOWN = "dropdown"


class _TextSelectorTypeStub:
    TEXT = "text"
    NUMBER = "number"
    PASSWORD = "password"
    EMAIL = "email"
    URL = "url"


_sel.NumberSelectorMode = _NumberSelectorModeStub  # type: ignore[attr-defined]
_sel.SelectSelectorMode = _SelectSelectorModeStub  # type: ignore[attr-defined]
_sel.TextSelectorType = _TextSelectorTypeStub  # type: ignore[attr-defined]


class _SectionStub:
    """Stub for homeassistant.data_entry_flow.section (used to group fields)."""

    def __init__(self, schema=None, options=None, **kwargs):
        self.schema = schema
        self.options = options or {}


_stub_module("homeassistant.data_entry_flow")
sys.modules["homeassistant.data_entry_flow"].section = _SectionStub  # type: ignore[attr-defined]

# homeassistant.const
_const = sys.modules["homeassistant.const"]
for _attr in [
    "CONF_NAME", "CONF_LATITUDE", "CONF_LONGITUDE",
    "STATE_UNAVAILABLE", "STATE_UNKNOWN",
    "TEMP_CELSIUS", "PERCENTAGE", "Platform",
    "ATTR_TEMPERATURE",
    "SERVICE_RELOAD",
]:
    setattr(_const, _attr, _attr)

_const.UnitOfTemperature = _FakeEnum  # type: ignore[attr-defined]
_const.UnitOfPower = _FakeEnum  # type: ignore[attr-defined]
_const.UnitOfEnergy = _FakeEnum  # type: ignore[attr-defined]

# homeassistant.helpers.entity
_entity = sys.modules["homeassistant.helpers.entity"]
_entity.Entity = object  # type: ignore[attr-defined]


class _EntityCategoryStub:
    """Mirror of homeassistant.helpers.entity.EntityCategory."""
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


_entity.EntityCategory = _EntityCategoryStub  # type: ignore[attr-defined]

# homeassistant.helpers.update_coordinator
class _DataUpdateCoordinatorStub:
    def __init__(self, *args, **kwargs):
        pass


class _CoordinatorEntityStub:
    def __init__(self, coordinator=None):
        self._coordinator_stub = coordinator


_coord = sys.modules["homeassistant.helpers.update_coordinator"]
_coord.DataUpdateCoordinator = _DataUpdateCoordinatorStub  # type: ignore[attr-defined]
_coord.CoordinatorEntity = _CoordinatorEntityStub  # type: ignore[attr-defined]
_coord.UpdateFailed = Exception  # type: ignore[attr-defined]

# homeassistant.components.websocket_api
_ws_api = sys.modules["homeassistant.components.websocket_api"]
_ws_api.ActiveConnection = object  # type: ignore[attr-defined]
_ws_api.websocket_command = lambda *_args, **_kwargs: (  # type: ignore[attr-defined]
    lambda func: func
)
_ws_api.async_response = lambda func: func  # type: ignore[attr-defined]
_ws_api.async_register_command = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]

# homeassistant.helpers.event
# Guarded so that when a real Home Assistant install is present these no-ops do
# NOT clobber the genuine implementations — they only fill the gap in the
# stub-only environment.
def _async_call_later_stub(hass, delay, action):
    """No-op stand-in returning a cancel callback."""
    return lambda: None


def _async_track_state_change_event_stub(hass, entity_ids, action):
    """No-op stand-in returning an unsubscribe callback."""
    return lambda: None


def _async_track_time_interval_stub(hass, action, interval, *args, **kwargs):
    """No-op stand-in returning a cancel callback."""
    return lambda: None


_event_mod = sys.modules["homeassistant.helpers.event"]
if not hasattr(_event_mod, "async_call_later"):
    _event_mod.async_call_later = _async_call_later_stub  # type: ignore[attr-defined]
if not hasattr(_event_mod, "async_track_state_change_event"):
    _event_mod.async_track_state_change_event = (  # type: ignore[attr-defined]
        _async_track_state_change_event_stub
    )
if not hasattr(_event_mod, "async_track_time_interval"):
    _event_mod.async_track_time_interval = (  # type: ignore[attr-defined]
        _async_track_time_interval_stub
    )

# homeassistant.helpers.entity_platform
_ep = sys.modules["homeassistant.helpers.entity_platform"]
_ep.AddEntitiesCallback = object  # type: ignore[attr-defined]

# homeassistant.helpers.reload
async def _async_integration_yaml_config_stub(hass, domain):
    return None


_reload_mod = sys.modules["homeassistant.helpers.reload"]
_reload_mod.async_integration_yaml_config = _async_integration_yaml_config_stub  # type: ignore[attr-defined]

# homeassistant.helpers.service
def _async_register_admin_service_stub(hass, domain, service, handler, schema=None):
    return None


_service_mod = sys.modules["homeassistant.helpers.service"]
_service_mod.async_register_admin_service = _async_register_admin_service_stub  # type: ignore[attr-defined]

# homeassistant.helpers.storage
class _StoreStub:
    """Minimal stub for homeassistant.helpers.storage.Store."""

    def __init__(self, hass=None, version=1, key=""):
        self._key = key

    async def async_save(self, data):
        pass

    async def async_load(self):
        return None

    def async_delay_save(self, data_func, delay=0):
        pass


_storage_mod = sys.modules["homeassistant.helpers.storage"]
_storage_mod.Store = _StoreStub  # type: ignore[attr-defined]

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


class _RestoreSensorStub:
    """Stub for RestoreSensor so multi-inheritance keeps working in tests."""

    async def async_added_to_hass(self) -> None:  # pragma: no cover
        return None

    async def async_get_last_sensor_data(self):  # pragma: no cover
        return None


_sensor.RestoreSensor = _RestoreSensorStub  # type: ignore[attr-defined]

# homeassistant.components.climate
_climate = sys.modules["homeassistant.components.climate"]
class _ClimateEntityBase:
    """Distinct base class so multi-inheritance with CoordinatorEntity works."""

_climate.ClimateEntity = _ClimateEntityBase  # type: ignore[attr-defined]


class _ClimateEntityFeatureStub:
    """Minimal HA ClimateEntityFeature stub with bit-flag-like values."""
    TARGET_TEMPERATURE = 1
    TARGET_TEMPERATURE_RANGE = 2
    TURN_ON = 4
    TURN_OFF = 8


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
if not hasattr(_vol, "Any"):
    _vol.Any = lambda *a, **kw: None  # type: ignore[attr-defined]
_vol.REMOVE_EXTRA = "REMOVE_EXTRA"  # type: ignore[attr-defined]
_vol.ALLOW_EXTRA = "ALLOW_EXTRA"  # type: ignore[attr-defined]
_vol.PREVENT_EXTRA = "PREVENT_EXTRA"  # type: ignore[attr-defined]


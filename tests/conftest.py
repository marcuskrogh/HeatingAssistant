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


_event_mod = sys.modules["homeassistant.helpers.event"]
if not hasattr(_event_mod, "async_call_later"):
    _event_mod.async_call_later = _async_call_later_stub  # type: ignore[attr-defined]
if not hasattr(_event_mod, "async_track_state_change_event"):
    _event_mod.async_track_state_change_event = (  # type: ignore[attr-defined]
        _async_track_state_change_event_stub
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

# ── Register vendored mbc under top-level "mbc.*" namespace ───────────────
# The mbc library is vendored at custom_components/heating_assistant/mbc/.
# Register it in sys.modules so any code referencing the top-level "mbc"
# namespace (e.g. in tests) resolves to the vendored copy.

import custom_components.heating_assistant.mbc as _vendored_mbc  # noqa: E402
import custom_components.heating_assistant.mbc.models as _vendored_mbc_models  # noqa: E402
import custom_components.heating_assistant.mbc.estimation as _vendored_mbc_estimation  # noqa: E402
import custom_components.heating_assistant.mbc.control as _vendored_mbc_control  # noqa: E402
import custom_components.heating_assistant.mbc.control.ocp as _vendored_mbc_control_ocp  # noqa: E402
import custom_components.heating_assistant.mbc.identification as _vendored_mbc_identification  # noqa: E402

sys.modules.setdefault("mbc", _vendored_mbc)
sys.modules.setdefault("mbc.models", _vendored_mbc_models)
sys.modules.setdefault("mbc.estimation", _vendored_mbc_estimation)
sys.modules.setdefault("mbc.control", _vendored_mbc_control)
sys.modules.setdefault("mbc.control.ocp", _vendored_mbc_control_ocp)
sys.modules.setdefault("mbc.identification", _vendored_mbc_identification)

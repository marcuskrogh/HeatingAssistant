"""SWD-446: lock leftover engine / MQTT / thin-bridge seams before splits."""

from __future__ import annotations

from heatingassistant.engine.heat_sources import (
    ElectricHeater,
    ElectricStorageHeater,
    GasHeater,
    GenericThermostat,
    GroundSourceHeatPump,
    HeatPump,
    HeatSource,
    HydronicRadiator,
    PelletStove,
    _SOFT_CEIL_K,
    _cop_at_temp,
    _soft_ceiling,
)
from heatingassistant.engine.schedule import (
    build_schedule,
    next_transition,
    resolve_effective_control_params,
)
from heatingassistant.mqtt.bridge import create_mqtt_bus
from heatingassistant.mqtt.supervisor import apply_supervisor_mqtt_discovery


def test_heat_source_types_remain_on_heat_sources() -> None:
    for cls in (
        ElectricHeater,
        GenericThermostat,
        GasHeater,
        HydronicRadiator,
        HeatPump,
        GroundSourceHeatPump,
        PelletStove,
        ElectricStorageHeater,
    ):
        assert issubclass(cls, HeatSource), cls.__name__


def test_heat_source_helpers_remain_importable() -> None:
    assert callable(_soft_ceiling)
    assert callable(_cop_at_temp)
    assert _SOFT_CEIL_K > 0
    assert _soft_ceiling(10.0, 5.0) <= 5.0


def test_schedule_public_builders_remain() -> None:
    assert callable(build_schedule)
    assert callable(resolve_effective_control_params)
    assert callable(next_transition)
    sched = build_schedule([])
    assert list(sched.periods) == []


def test_mqtt_bus_factory_and_supervisor_remain() -> None:
    assert callable(create_mqtt_bus)
    assert callable(apply_supervisor_mqtt_discovery)


def test_thin_bridge_manager_stays_on_integration_package() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "heating_assistant"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "_BridgeManager" in source
    assert "async def async_setup_entry" in source
    assert "async def async_unload_entry" in source
    assert "climate_attributes_for_publish" in source
    assert "_truthy" in source

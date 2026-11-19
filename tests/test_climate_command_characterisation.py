"""Characterise today's climate commands, disabled rooms, and target format.

Drives ``HeatingRuntime.run_control_tick`` through the real NMPC solver.
SWD-598's stand-in solver never reached ``solve_nmpc`` and missed a tick that
stopped passing outdoor temperature and forecasts, so these tests call the
real method and check those inputs on every solve.

No production behaviour changes.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from heatingassistant.app.actuation import climate_hp_command, climate_thermostat_command
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller.facade import HeatingMPCController
from heatingassistant.engine.heat_sources import HeatPump
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload, tag_out


_TEMPERATURE_TOKEN = re.compile(r'"temperature":(-?(?:\d+\.\d+|\d+)(?:[eE][+\-]?\d+)?)')


def _options(*, window: bool = False) -> dict[str, Any]:
    office: dict[str, Any] = {
        "name": "Office",
        "setpoint": 21.0,
        "comfort_offset": 1.5,
        "temp_tags": ["office_temp"],
        "enabled": True,
    }
    if window:
        office["window_sensors"] = ["binary_sensor.office_window"]
    return {
        "instance_id": "haos",
        "system_enabled": True,
        "mpc_mode": "nmpc",
        "update_interval": 900,
        "nmpc_period": 900,
        "nmpc_horizon_h": 0.25,
        "nmpc_fast_substeps": 1,
        "window_open_debounce": 0,
        "window_open_close_settle": 0,
        "price_entity": "sensor.energy_price",
        "outdoor_temp_entity": "sensor.outdoor_temperature",
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 21.0,
                "comfort_offset": 1.5,
                "temp_tags": ["living_temp"],
                "enabled": True,
            },
            office,
        ],
        "heat_sources": [
            {
                "name": "living_hp",
                "room": "Living Room",
                "type": "heat_pump",
                "max_power": 4000.0,
                "cop_rated": 3.5,
                "hvac_mode": "heat_cool",
                "heater_entity": "climate.living_room_heat_pump",
                "output_tag": "living_heat",
            },
            {
                "name": "office_panel",
                "room": "Office",
                "type": "electric_heater",
                "max_power": 1300.0,
                "heater_entity": "climate.office_heater",
                "output_tag": "office_heat",
            },
        ],
    }


def _runtime(tmp_path: Path, *, window: bool = False) -> tuple[HeatingRuntime, list[tuple[str, str, bool]]]:
    bus = InMemoryMqttBus()
    published: list[tuple[str, str, bool]] = []

    async def capture(topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        text = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
        published.append((topic, text, retain))

    bus.publish = capture  # type: ignore[method-assign]
    runtime = HeatingRuntime(tmp_path, bus=bus, options=_options(window=window))
    return runtime, published


def _arm_real_solver(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    real = HeatingMPCController.solve_nmpc

    def _solve(self: HeatingMPCController, *args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(dict(kwargs))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(HeatingMPCController, "solve_nmpc", _solve)
    return seen


def _assert_real_solve(seen: list[dict[str, Any]], *, new_calls: int) -> None:
    assert len(seen) >= new_calls
    for kwargs in seen[-new_calls:]:
        assert kwargs["outdoor_temp"] == pytest.approx(-5.0)
        assert kwargs["outdoor_forecast"]
        assert kwargs["price_forecast"]
        trajectory = kwargs["control_trajectory"]
        assert trajectory is not None
        assert "Living Room" in trajectory.setpoints
        assert "Office" in trajectory.setpoints


async def _tag(
    runtime: HeatingRuntime,
    tag: str,
    value: Any,
    now: float,
    attributes: dict[str, Any] | None = None,
) -> None:
    await runtime._handle_tag_message(
        f"heatingassistant/haos/tag/{tag}/in",
        MqttTagPayload(value=value, ts=now, attributes=attributes).encode(),
        qos=0,
        retain=False,
        now=now,
    )


def _join(runtime: HeatingRuntime) -> None:
    thread = getattr(runtime, "_nmpc_thread", None)
    if thread is not None:
        thread.join(timeout=90)
        assert not thread.is_alive()


async def _tick(runtime: HeatingRuntime, now: float) -> None:
    await runtime.run_control_tick(now=now)
    _join(runtime)


def _anchor(runtime: HeatingRuntime, now: float) -> None:
    """Pin the sample grid the way ``start`` does when the system is enabled.

    A later tick solves again only on a later 900 s slot. ``start`` also
    launches the background ticker; these tests leave that off.
    """

    runtime._anchor_schedule_epoch(now)


async def _prime(
    runtime: HeatingRuntime,
    now: float,
    *,
    office_temp: float = 23.0,
    office_internal: float = 6.2,
) -> None:
    """Living Room is cold so a plan beats zero heat.

    Office defaults to above its setpoint, so the electric climate path idles:
    target = internal − 1°C − overshoot, with no step and no clamp.
    """

    await _tag(runtime, "living_temp", 16.0, now)
    await _tag(runtime, "office_temp", office_temp, now)
    await _tag(runtime, "outdoor_temp", -5.0, now)
    await _tag(runtime, "energy_price", 0.4, now)
    await _tag(
        runtime,
        "living_heat_state",
        "heat_cool",
        now,
        {
            "current_temperature": 21.37,
            "temperature": 21.0,
            "hvac_modes": ["off", "heat", "cool", "heat_cool"],
            "hvac_action": "heating",
            "min_temp": 5,
            "max_temp": 35,
            "target_temp_step": 0.5,
            "precision": 0.5,
        },
    )
    await _tag(
        runtime,
        "office_heat_state",
        "heat",
        now,
        {
            "current_temperature": office_internal,
            "temperature": 18.8,
            "hvac_modes": ["off", "heat"],
            "hvac_action": "idle",
            "min_temp": 5,
            "max_temp": 35,
            "target_temp_step": 1,
            "precision": 1.0,
        },
    )


def _source(runtime: HeatingRuntime, name: str) -> dict[str, Any]:
    for source in runtime._heat_sources():
        if source.get("name") == name:
            return dict(source)
    raise AssertionError(name)


def _payloads(published: list[tuple[str, str, bool]], tag: str) -> list[dict[str, Any]]:
    topic = tag_out("haos", tag)
    found: list[dict[str, Any]] = []
    for published_topic, body, retain in published:
        if published_topic != topic:
            continue
        assert retain is True
        value = MqttTagPayload.decode(body).value
        assert isinstance(value, dict)
        found.append(value)
    return found


def _temperature_token(published: list[tuple[str, str, bool]], tag: str) -> str:
    topic = tag_out("haos", tag)
    for published_topic, body, _retain in reversed(published):
        if published_topic != topic:
            continue
        match = _TEMPERATURE_TOKEN.search(body)
        assert match is not None
        return match.group(1)
    raise AssertionError(tag)


def _records(runtime: HeatingRuntime, kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    directory = runtime.control_log.directory
    for path in sorted(directory.glob(f"*.{kind}.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_tick_sends_unrounded_climate_targets_on_every_accepted_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, published = _runtime(tmp_path)
    seen = _arm_real_solver(monkeypatch)

    async def _run() -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin)
        await _tick(runtime, origin)
        await _tick(runtime, origin + 900.0)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=2)

    office_cfg = _source(runtime, "office_panel")
    living_cfg = _source(runtime, "living_hp")
    office_fraction = float(runtime.actuator_outputs[office_cfg["output_tag"]])
    living_fraction = float(runtime.actuator_outputs[living_cfg["output_tag"]])
    office_source = runtime._heat_source_object(office_cfg)
    living_source = runtime._heat_source_object(living_cfg)
    assert office_source is not None and not isinstance(office_source, HeatPump)
    assert isinstance(living_source, HeatPump)

    # Office air is above the room setpoint, so the electric climate path
    # idles: target = internal − 1°C − overshoot, with no step and no clamp.
    expected_office = climate_thermostat_command(
        office_source,
        office_fraction,
        internal_temp=6.2,
        room_temp=23.0,
        room_setpoint=21.0,
    )
    expected_living = climate_hp_command(
        living_source,
        living_fraction,
        internal_temp=21.37,
        outdoor_temp=-5.0,
        supported_modes=["off", "heat", "cool", "heat_cool"],
    )
    assert expected_office["hvac_mode"] == "heat"
    assert expected_office["temperature"] == pytest.approx(3.2)
    assert expected_living["hvac_mode"] == "heat_cool"

    office_cmds = _payloads(published, "office_heat")
    living_cmds = _payloads(published, "living_heat")
    assert len(office_cmds) == 2
    assert len(living_cmds) == 2
    assert office_cmds[0] == expected_office
    assert office_cmds[1] == expected_office
    assert living_cmds[1] == expected_living
    assert living_cmds[0]["hvac_mode"] == "heat_cool"
    assert "temperature" in living_cmds[0]
    assert abs(office_cmds[0]["temperature"] - round(office_cmds[0]["temperature"])) > 0.05
    assert office_cmds[0]["temperature"] < 5
    token = _temperature_token(published, "office_heat")
    assert "." in token
    assert float(token) == pytest.approx(3.2)

    # Step, min, and max are present on the tag the App stored, and unused.
    office_attrs = runtime.tag_attributes["office_heat_state"]
    assert office_attrs["target_temp_step"] == 1
    assert office_attrs["min_temp"] == 5
    assert office_attrs["max_temp"] == 35
    assert office_attrs["precision"] == 1.0
    living_attrs = runtime.tag_attributes["living_heat_state"]
    assert living_attrs["target_temp_step"] == 0.5
    assert living_cmds[0]["temperature"] == pytest.approx(expected_living["temperature"])
    snapped = round(expected_living["temperature"] * 2.0) / 2.0
    if abs(expected_living["temperature"] - snapped) > 1e-9:
        assert living_cmds[0]["temperature"] != pytest.approx(snapped)


def test_disabled_room_is_commanded_off_on_every_tick_and_stays_in_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, published = _runtime(tmp_path)
    seen = _arm_real_solver(monkeypatch)

    held: dict[str, Any] = {}

    async def _set_enabled(enabled: bool, now: float) -> None:
        rooms = [dict(room) for room in runtime._rooms()]
        for room in rooms:
            if room.get("name") == "Office":
                room["enabled"] = enabled
        await runtime.update_config({"rooms": rooms}, now=now)
        _join(runtime)

    async def _run() -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin, office_temp=16.0, office_internal=19.8)
        await _tick(runtime, origin)
        await _set_enabled(False, origin + 900.0)
        await _tick(runtime, origin + 1_800.0)
        held["forecast"] = runtime.forecasts()
        await _set_enabled(True, origin + 2_700.0)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=4)
    for kwargs in seen:
        assert "Office" in kwargs["control_trajectory"].setpoints

    office_cmds = _payloads(published, "office_heat")
    living_cmds = _payloads(published, "living_heat")
    assert len(office_cmds) == 4
    assert office_cmds[0]["hvac_mode"] == "heat"
    assert "temperature" in office_cmds[0]
    assert office_cmds[1] == {"hvac_mode": "off"}
    assert office_cmds[2] == {"hvac_mode": "off"}
    assert office_cmds[3]["hvac_mode"] == "heat"
    assert "temperature" in office_cmds[3]
    assert all("temperature" in command for command in living_cmds)
    assert all(command["hvac_mode"] != "off" for command in living_cmds)

    assert "Office" in runtime.control_engine.model.room_names
    disabled_solves = seen[1:3]
    for kwargs in disabled_solves:
        enabled = kwargs["control_trajectory"].enabled_steps["Office"]
        assert not bool(enabled.any())

    plans = [row for row in _records(runtime, "plan") if row.get("status") == "accepted"]
    assert len(plans) >= 3
    names = [source.name for source in runtime.control_engine.heat_sources]
    office_index = names.index("office_panel")
    # The disable tick and the following tick pin the Office column.
    # Plan rows are written when a solve is accepted; the first is enabled.
    off_plan = plans[1]
    column = [float(step[office_index]) for step in off_plan["U"]]
    assert column
    assert all(abs(value) < 1e-8 for value in column)

    ticks = _records(runtime, "tick")
    assert any("office" in row["rooms"] and "office_panel" in row["sources"] for row in ticks)
    off_ticks = [
        row for row in ticks if row["sources"]["office_panel"].get("hvac_mode") == "off"
    ]
    assert len(off_ticks) >= 2
    assert all(row["rooms"]["office"]["name"] == "Office" for row in off_ticks)

    forecast = held["forecast"]
    office_block = forecast["rooms"]["office"]
    assert office_block["forecast"]
    assert office_block["forecast"][0]["enabled"] is False
    assert office_block["forecast"][0]["temperature"] is not None
    assert "living_room" in forecast["rooms"]


def test_open_window_sends_off_on_the_transition_and_on_later_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, published = _runtime(tmp_path, window=True)
    seen = _arm_real_solver(monkeypatch)

    async def _flush() -> None:
        for _ in range(6):
            await asyncio.sleep(0)

    async def _run() -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin, office_temp=16.0, office_internal=19.8)
        await _tick(runtime, origin)
        window_tag = next(
            room["window_tags"][0]
            for room in runtime._rooms()
            if room.get("name") == "Office"
        )
        await _tag(runtime, window_tag, True, origin + 1.0)
        await _flush()
        await _tick(runtime, origin + 900.0)
        await _tag(runtime, window_tag, False, origin + 901.0)
        await _flush()
        await _tick(runtime, origin + 1_800.0)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=3)

    office_cmds = _payloads(published, "office_heat")
    # Enabled tick, open-transition publish, later tick, settle publish, later tick.
    assert [command.get("hvac_mode") for command in office_cmds] == [
        "heat",
        "off",
        "off",
        "heat",
        "heat",
    ]
    assert "temperature" not in office_cmds[1]
    assert "temperature" not in office_cmds[2]
    assert "temperature" in office_cmds[0]
    assert "temperature" in office_cmds[3]
    assert "temperature" in office_cmds[4]
    assert runtime.get_window_state("Office") == "closed"
    assert "Office" in runtime.control_engine.model.room_names

    # A window does not clear the room's enabled steps. The slow plan can
    # still ask for heat; the publish path is what sends hvac_mode off.
    window_solve = seen[1]
    assert bool(window_solve["control_trajectory"].enabled_steps["Office"].any())
    plans = [row for row in _records(runtime, "plan") if row.get("status") == "accepted"]
    assert len(plans) >= 3
    names = [source.name for source in runtime.control_engine.heat_sources]
    office_index = names.index("office_panel")
    planned = [float(step[office_index]) for step in plans[1]["U"]]
    assert planned
    assert max(abs(value) for value in planned) > 1e-3
    forecast = runtime.forecasts()
    assert forecast["rooms"]["office"]["forecast"][0]["temperature"] is not None


def test_bridge_forwards_min_and_max_but_not_step_and_writes_every_payload() -> None:
    fake_ha = MagicMock()
    fake_ha.const.SERVICE_TURN_OFF = "turn_off"
    fake_ha.const.SERVICE_TURN_ON = "turn_on"
    fake_ha.const.STATE_UNAVAILABLE = "unavailable"
    fake_ha.const.STATE_UNKNOWN = "unknown"
    modules = {
        "homeassistant": fake_ha,
        "homeassistant.components": fake_ha.components,
        "homeassistant.components.mqtt": fake_ha.components.mqtt,
        "homeassistant.config_entries": fake_ha.config_entries,
        "homeassistant.const": fake_ha.const,
        "homeassistant.core": fake_ha.core,
        "homeassistant.helpers": fake_ha.helpers,
        "homeassistant.helpers.event": fake_ha.helpers.event,
    }
    patcher = patch.dict(sys.modules, modules)
    patcher.start()
    for name in list(sys.modules):
        if name.startswith("custom_components.heating_assistant"):
            del sys.modules[name]
    try:
        module = importlib.import_module("custom_components.heating_assistant.bridge_manager")
        state = SimpleNamespace(
            domain="climate",
            attributes={
                "current_temperature": 19.8,
                "temperature": 18.8,
                "hvac_modes": ["off", "heat"],
                "hvac_action": "idle",
                "min_temp": 5,
                "max_temp": 35,
                "target_temp_step": 1,
                "precision": 1.0,
            },
        )
        forwarded = module.climate_attributes_for_publish(state)
        assert forwarded["min_temp"] == 5
        assert forwarded["max_temp"] == 35
        assert forwarded["current_temperature"] == pytest.approx(19.8)
        assert "target_temp_step" not in forwarded
        assert "precision" not in forwarded

        calls: list[tuple[str, str, dict[str, Any]]] = []

        async def _call(
            domain: str, service: str, data: dict[str, Any], blocking: bool = False
        ) -> None:
            calls.append((domain, service, dict(data)))

        hass = SimpleNamespace(services=SimpleNamespace(async_call=_call))
        manager = module._BridgeManager(hass, SimpleNamespace(data={"instance_id": "haos"}))

        async def _write() -> None:
            await manager._write_climate_entity(
                "climate.office_heater",
                {"hvac_mode": "heat", "temperature": 18.8},
            )
            await manager._write_climate_entity(
                "climate.office_heater",
                {"hvac_mode": "heat", "temperature": 18.9},
            )
            await manager._write_climate_entity(
                "climate.living_room_heat_pump",
                {"hvac_mode": "heat_cool", "temperature": 21.37},
            )
            await manager._write_climate_entity(
                "climate.office_heater", {"hvac_mode": "off"}
            )

        asyncio.run(_write())
    finally:
        patcher.stop()
        for name in list(sys.modules):
            if name.startswith("custom_components.heating_assistant"):
                del sys.modules[name]

    assert calls == [
        ("climate", "set_hvac_mode", {"entity_id": "climate.office_heater", "hvac_mode": "heat"}),
        ("climate", "set_temperature", {"entity_id": "climate.office_heater", "temperature": 18.8}),
        ("climate", "set_hvac_mode", {"entity_id": "climate.office_heater", "hvac_mode": "heat"}),
        ("climate", "set_temperature", {"entity_id": "climate.office_heater", "temperature": 18.9}),
        (
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.living_room_heat_pump", "hvac_mode": "heat_cool"},
        ),
        (
            "climate",
            "set_temperature",
            {"entity_id": "climate.living_room_heat_pump", "temperature": 21.37},
        ),
        ("climate", "set_hvac_mode", {"entity_id": "climate.office_heater", "hvac_mode": "off"}),
    ]

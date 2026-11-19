"""Per-room watch-only: publish nothing, leave the planner alone.

Drives ``HeatingRuntime`` through the real NMPC solver, the same way as
``test_climate_command_characterisation``. A stand-in solver is not used.

Watch-only wins over a disabled room: the publish path sends no ``off``.
The first publish while the flag is true clears the retained command with
an empty payload. The bridge treats that payload as invalid and does not
call a climate service. Later publishes, including window override and its
settle timer, send nothing for that room.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import tag_out
from tests.test_climate_command_characterisation import (
    _anchor,
    _arm_real_solver,
    _assert_real_solve,
    _join,
    _options,
    _prime,
    _records,
    _tag,
    _tick,
)


def _runtime_from(
    tmp_path: Path, options: dict[str, Any]
) -> tuple[HeatingRuntime, list[tuple[str, str, bool]]]:
    bus = InMemoryMqttBus()
    published: list[tuple[str, str, bool]] = []

    async def capture(
        topic: str, payload: str | bytes, qos: int = 0, retain: bool = False
    ) -> None:
        text = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
        published.append((topic, text, retain))

    bus.publish = capture  # type: ignore[method-assign]
    runtime = HeatingRuntime(tmp_path, bus=bus, options=options)
    return runtime, published


def _options_for(
    *,
    window: bool = False,
    watch_only: bool | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    options = _options(window=window)
    office = options["rooms"][1]
    office["enabled"] = enabled
    if watch_only is not None:
        office["watch_only"] = watch_only
    return options


def _tag_out(published: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    return [
        (topic, body, retain)
        for topic, body, retain in published
        if "/tag/" in topic and topic.endswith("/out")
    ]


def _bodies(published: list[tuple[str, str, bool]], tag: str) -> list[tuple[str, bool]]:
    topic = tag_out("haos", tag)
    return [(body, retain) for published_topic, body, retain in published if published_topic == topic]


def _accepted_u(runtime: HeatingRuntime) -> list[list[list[float]]]:
    rows = [row for row in _records(runtime, "plan") if row.get("status") == "accepted"]
    return [[[float(value) for value in step] for step in row["U"]] for row in rows]


def _living_column(plans: list[list[list[float]]], runtime: HeatingRuntime) -> list[list[float]]:
    index = [source.name for source in runtime.control_engine.heat_sources].index("living_hp")
    return [[step[index] for step in plan] for plan in plans]


async def _set_office(runtime: HeatingRuntime, now: float, **fields: Any) -> None:
    rooms = [dict(room) for room in runtime._rooms()]
    for room in rooms:
        if room.get("name") == "Office":
            room.update(fields)
    await runtime.update_config({"rooms": rooms}, now=now)
    _join(runtime)


def _show(left: Any, right: Any) -> str:
    return (
        "left:\n"
        + json.dumps(left, sort_keys=True)
        + "\nright:\n"
        + json.dumps(right, sort_keys=True)
    )


def test_watch_only_on_clears_retained_command_then_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, published = _runtime_from(tmp_path, _options_for())
    seen = _arm_real_solver(monkeypatch)

    async def _run() -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin, office_temp=16.0, office_internal=19.8)
        await _tick(runtime, origin)
        await _set_office(runtime, origin + 900.0, watch_only=True)
        await _tick(runtime, origin + 1_800.0)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=3)

    office = _bodies(published, "office_heat")
    assert len(office) == 2
    assert office[0][1] is True
    assert '"hvac_mode":"heat"' in office[0][0]
    assert office[1] == ("", True)
    living = _bodies(published, "living_heat")
    assert len(living) == 3
    assert all(retain is True and body.startswith("{") for body, retain in living)

    forecast = runtime.forecasts()
    assert forecast["rooms"]["office"]["watch_only"] is True
    assert "watch_only" not in forecast["rooms"]["living_room"]
    assert "Office" in runtime.control_engine.model.room_names
    ticks = _records(runtime, "tick")
    assert any("office" in row["rooms"] for row in ticks)


def test_watch_only_off_publishes_the_normal_command_on_the_config_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, published = _runtime_from(tmp_path, _options_for(watch_only=True))
    seen = _arm_real_solver(monkeypatch)

    async def _run() -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin, office_temp=16.0, office_internal=19.8)
        await _tick(runtime, origin)
        await _set_office(runtime, origin + 900.0, watch_only=False)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=2)

    office = _bodies(published, "office_heat")
    assert office[0] == ("", True)
    assert office[-1][1] is True
    sent = json.loads(office[-1][0])["value"]
    assert sent["hvac_mode"] == "heat"
    assert "temperature" in sent
    living = _bodies(published, "living_heat")
    assert len(living) == 2
    assert all('"temperature":' in body for body, _retain in living)


def test_window_while_watch_only_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_runtime, watch_published = _runtime_from(
        tmp_path / "watch", _options_for(window=True, watch_only=True)
    )
    base_runtime, base_published = _runtime_from(
        tmp_path / "base", _options_for(window=True)
    )
    seen = _arm_real_solver(monkeypatch)

    async def _flush() -> None:
        for _ in range(6):
            await asyncio.sleep(0)

    async def _drive(runtime: HeatingRuntime) -> None:
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

    async def _run() -> None:
        await _drive(base_runtime)
        await _drive(watch_runtime)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=6)

    base_office = _bodies(base_published, "office_heat")
    assert [json.loads(body)["value"].get("hvac_mode") for body, _retain in base_office] == [
        "heat",
        "off",
        "off",
        "heat",
        "heat",
    ]
    watch_office = _bodies(watch_published, "office_heat")
    assert watch_office == [("", True)]
    assert watch_runtime.get_window_state("Office") == "closed"
    assert base_runtime.get_window_state("Office") == "closed"

    base_plans = _accepted_u(base_runtime)
    watch_plans = _accepted_u(watch_runtime)
    assert watch_plans == base_plans, _show(watch_plans, base_plans)
    window_solve = seen[4]
    assert bool(window_solve["control_trajectory"].enabled_steps["Office"].any())


def test_disabled_and_watch_only_sends_nothing_including_no_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Watch-only wins over disabled: the room is not commanded off."""

    runtime, published = _runtime_from(tmp_path, _options_for())
    seen = _arm_real_solver(monkeypatch)

    async def _run() -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin, office_temp=16.0, office_internal=19.8)
        await _tick(runtime, origin)
        await _set_office(runtime, origin + 900.0, enabled=False, watch_only=True)
        await _tick(runtime, origin + 1_800.0)
        await _set_office(runtime, origin + 2_700.0, watch_only=False)

    asyncio.run(_run())
    _assert_real_solve(seen, new_calls=4)

    office = _bodies(published, "office_heat")
    assert '"hvac_mode":"heat"' in office[0][0]
    assert office[1] == ("", True)
    assert len(office) == 3
    restored = json.loads(office[2][0])["value"]
    assert restored == {"hvac_mode": "off"}
    assert "watch_only" not in runtime.forecasts()["rooms"]["office"]


def test_flag_absent_or_false_matches_and_living_room_is_unchanged_under_watch_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent_runtime, absent_published = _runtime_from(tmp_path / "absent", _options_for())
    false_runtime, false_published = _runtime_from(
        tmp_path / "false", _options_for(watch_only=False)
    )
    watch_runtime, watch_published = _runtime_from(
        tmp_path / "watch", _options_for(watch_only=True)
    )
    _arm_real_solver(monkeypatch)

    async def _drive(runtime: HeatingRuntime) -> None:
        origin = 1_700_000_000.0
        _anchor(runtime, origin)
        await _prime(runtime, origin)
        await _tick(runtime, origin)
        await _tick(runtime, origin + 900.0)

    async def _run() -> None:
        await _drive(absent_runtime)
        await _drive(false_runtime)
        await _drive(watch_runtime)

    asyncio.run(_run())

    absent_out = _tag_out(absent_published)
    false_out = _tag_out(false_published)
    assert false_out == absent_out, _show(false_out, absent_out)
    assert all(retain is True and body.startswith("{") for _topic, body, retain in absent_out)

    def _living(published: list[tuple[str, str, bool]]) -> list[tuple[str, bool]]:
        return _bodies(published, "living_heat")

    watch_living = _living(watch_published)
    absent_living = _living(absent_published)
    assert watch_living == absent_living, _show(watch_living, absent_living)

    absent_plans = _accepted_u(absent_runtime)
    false_plans = _accepted_u(false_runtime)
    watch_plans = _accepted_u(watch_runtime)
    assert false_plans == absent_plans, _show(false_plans, absent_plans)
    assert _living_column(watch_plans, watch_runtime) == _living_column(
        absent_plans, absent_runtime
    ), _show(
        _living_column(watch_plans, watch_runtime),
        _living_column(absent_plans, absent_runtime),
    )
    assert watch_plans == absent_plans, _show(watch_plans, absent_plans)
    assert watch_runtime.actuator_outputs["living_heat"] == pytest.approx(
        absent_runtime.actuator_outputs["living_heat"]
    )
    assert watch_runtime.actuator_outputs["office_heat"] == pytest.approx(
        absent_runtime.actuator_outputs["office_heat"]
    )
    assert _bodies(watch_published, "office_heat") == [("", True)]
    office = watch_runtime.forecasts()["rooms"]["office"]
    assert office["watch_only"] is True
    assert "watch_only" not in watch_runtime.forecasts()["rooms"]["living_room"]
    assert "watch_only" not in absent_runtime.forecasts()["rooms"]["office"]


def test_room_editor_exposes_watch_only() -> None:
    editor = Path("heatingassistant/app/static/js/config/config-room-editor.js").read_text(
        encoding="utf-8"
    )
    assert "watch_only" in editor
    assert "Yes — plan only, send nothing" in editor
    assert "out.watch_only = true" in editor


def test_bridge_ignores_empty_retained_payload() -> None:
    fake_ha = MagicMock()
    fake_ha.const.SERVICE_TURN_OFF = "turn_off"
    fake_ha.const.SERVICE_TURN_ON = "turn_on"
    fake_ha.const.STATE_UNAVAILABLE = "unavailable"
    fake_ha.const.STATE_UNKNOWN = "unknown"
    captured: list[Any] = []

    async def _subscribe(hass: Any, topic: str, callback: Any, qos: int = 0) -> Any:
        captured.append(callback)
        return lambda: None

    fake_ha.components.mqtt.async_subscribe = _subscribe
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
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _call(
        domain: str, service: str, data: dict[str, Any], blocking: bool = False
    ) -> None:
        calls.append((domain, service, dict(data)))

    patcher = patch.dict(sys.modules, modules)
    patcher.start()
    for name in list(sys.modules):
        if name.startswith("custom_components.heating_assistant"):
            del sys.modules[name]
    try:
        module = importlib.import_module("custom_components.heating_assistant.bridge_manager")
        hass = SimpleNamespace(services=SimpleNamespace(async_call=_call))
        manager = module._BridgeManager(hass, SimpleNamespace(data={"instance_id": "haos"}))

        async def _run() -> None:
            await manager._bind_entity_output("office_heat", "climate.office_heater")
            callback = captured[-1]
            await callback(SimpleNamespace(payload=""))
            await callback(SimpleNamespace(payload=b""))
            await callback(
                SimpleNamespace(
                    payload='{"value":{"hvac_mode":"heat","temperature":17.6},"status":"GOOD","reason":null,"ts":1}'
                )
            )

        asyncio.run(_run())
    finally:
        patcher.stop()
        for name in list(sys.modules):
            if name.startswith("custom_components.heating_assistant"):
                del sys.modules[name]

    assert calls == [
        ("climate", "set_hvac_mode", {"entity_id": "climate.office_heater", "hvac_mode": "heat"}),
        ("climate", "set_temperature", {"entity_id": "climate.office_heater", "temperature": 17.6}),
    ]

"""Unit tests for identification service handlers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.const import DOMAIN
from custom_components.heating_assistant.services import identification as ident_mod
from custom_components.heating_assistant.services.identification import (
    _resolve_room,
    handle_apply_heater_scales,
    handle_apply_manual_parameters,
    handle_estimate,
    handle_estimate_ml,
    handle_schedule_experiment,
    handle_simulate,
    handle_store_identified_parameters,
)


def _stub_coordinator(*, room_names=None):
    """Minimal coordinator stand-in for identification service handler tests."""
    if room_names is None:
        room_names = ["Living Room", "Kitchen"]
    coordinator = MagicMock(spec=HeatingAssistantCoordinator)
    coordinator.model = SimpleNamespace(room_names=list(room_names))
    coordinator.dt = 900.0
    coordinator.sysid_results = {}
    coordinator.heat_sources = []
    coordinator.async_update_listeners = MagicMock()
    coordinator.simulate_thermal_response = MagicMock(
        return_value={"room": "Living Room", "temps": [21.0, 22.0]}
    )
    coordinator.estimate_parameters = MagicMock(
        return_value={"thermal_mass": 5_000_000.0, "r_external": 0.05}
    )
    coordinator.apply_manual_parameters = MagicMock()
    coordinator.apply_heater_scales = MagicMock()
    coordinator.store_identified_parameters = MagicMock()
    coordinator.schedule_experiment = MagicMock()
    coordinator.async_estimate_parameters_ml = AsyncMock(
        return_value={"success": False}
    )
    return coordinator


def test_resolve_room_accepts_slug_and_canonical_name():
    """_resolve_room accepts both canonical room names and slugs."""
    coordinator = _stub_coordinator(room_names=["Living Room", "Kitchen"])

    assert _resolve_room(coordinator, "Living Room") == "Living Room"
    assert _resolve_room(coordinator, "living_room") == "Living Room"
    assert _resolve_room(coordinator, "Kitchen") == "Kitchen"


def test_resolve_room_raises_for_unknown_room():
    """_resolve_room raises ValueError when the room is not configured."""
    coordinator = _stub_coordinator(room_names=["Living Room"])

    with pytest.raises(ValueError, match="Room 'Bedroom' not found"):
        _resolve_room(coordinator, "Bedroom")


@pytest.mark.asyncio
async def test_handle_simulate_returns_result_and_fires_event(monkeypatch):
    """simulate_thermal_response returns data and fires a simulation_result event."""
    coordinator = _stub_coordinator()
    expected = {"room": "Living Room", "temps": [21.0, 22.0]}
    coordinator.simulate_thermal_response.return_value = expected
    hass = SimpleNamespace()
    hass.bus = SimpleNamespace(async_fire=MagicMock())
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    call = SimpleNamespace(
        data={
            "room_name": "Living Room",
            "initial_temp": 21.0,
            "outdoor_temp": 5.0,
            "heating_power": 1500.0,
            "duration_hours": 4.0,
        }
    )
    result = await handle_simulate(hass, call)

    assert result == expected
    coordinator.simulate_thermal_response.assert_called_once_with(
        room_name="Living Room",
        initial_temp=21.0,
        outdoor_temp=5.0,
        heating_power=1500.0,
        duration_hours=4.0,
    )
    hass.bus.async_fire.assert_called_once_with(
        f"{DOMAIN}_simulation_result", expected
    )


@pytest.mark.asyncio
async def test_handle_estimate_returns_result_and_fires_event(monkeypatch):
    """estimate_parameters returns data and fires an estimation_result event."""
    coordinator = _stub_coordinator()
    expected = {"thermal_mass": 5_000_000.0, "r_external": 0.05}
    coordinator.estimate_parameters.return_value = expected
    hass = SimpleNamespace()
    hass.bus = SimpleNamespace(async_fire=MagicMock())
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    call = SimpleNamespace(
        data={
            "room_name": "Living Room",
            "heating_power": 1500.0,
            "outdoor_temp": 5.0,
            "initial_temp": 20.0,
            "final_temp": 22.0,
            "duration_seconds": 7200.0,
        }
    )
    result = await handle_estimate(hass, call)

    assert result == expected
    coordinator.estimate_parameters.assert_called_once_with(
        room_name="Living Room",
        heating_power=1500.0,
        outdoor_temp=5.0,
        initial_temp=20.0,
        final_temp=22.0,
        duration_seconds=7200.0,
    )
    hass.bus.async_fire.assert_called_once_with(
        f"{DOMAIN}_estimation_result", expected
    )


@pytest.mark.asyncio
async def test_handle_apply_manual_parameters_delegates_and_updates(monkeypatch):
    """apply_manual_parameters delegates to the coordinator and refreshes listeners."""
    coordinator = _stub_coordinator()
    hass = SimpleNamespace()
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    await handle_apply_manual_parameters(
        hass,
        SimpleNamespace(
            data={
                "room_name": "Living Room",
                "thermal_mass": 5_500_000.0,
                "r_external": 0.06,
            }
        ),
    )

    coordinator.apply_manual_parameters.assert_called_once_with(
        "Living Room", 5_500_000.0, 0.06
    )
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_apply_heater_scales_with_explicit_scales(monkeypatch):
    """apply_heater_scales applies an explicit scales dict from service data."""
    coordinator = _stub_coordinator()
    hass = SimpleNamespace()
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)
    scales = {"lr_radiator": 0.92, "kitchen_radiator": 1.05}

    await handle_apply_heater_scales(
        hass, SimpleNamespace(data={"scales": scales})
    )

    coordinator.apply_heater_scales.assert_called_once_with(scales)
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_apply_heater_scales_noop_without_scales_or_cache(monkeypatch):
    """apply_heater_scales is a no-op when no scales are provided or cached."""
    coordinator = _stub_coordinator()
    hass = SimpleNamespace()
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    await handle_apply_heater_scales(hass, SimpleNamespace(data={}))

    coordinator.apply_heater_scales.assert_not_called()
    coordinator.async_update_listeners.assert_not_called()


@pytest.mark.asyncio
async def test_handle_store_identified_parameters_resolves_slug(monkeypatch):
    """store_identified_parameters resolves a room slug to the canonical name."""
    coordinator = _stub_coordinator(room_names=["Living Room"])
    hass = SimpleNamespace()
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    await handle_store_identified_parameters(
        hass,
        SimpleNamespace(
            data={
                "room_name": "living_room",
                "thermal_mass": 5_000_000.0,
                "r_external": 0.05,
            }
        ),
    )

    coordinator.store_identified_parameters.assert_called_once_with(
        "Living Room",
        5_000_000.0,
        0.05,
        source="manual",
        rmse=None,
        internal_gain=None,
        solar_scale=None,
        c_air_fraction=None,
        r_aw_fraction=None,
        heater_scales=None,
    )
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_schedule_experiment_rejects_end_before_start(monkeypatch):
    """schedule_experiment raises when end is not after start."""
    coordinator = _stub_coordinator()
    hass = SimpleNamespace()
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    with pytest.raises(ValueError, match="Experiment end must be after start"):
        await handle_schedule_experiment(
            hass,
            SimpleNamespace(
                data={
                    "room_name": "Living Room",
                    "start": 1_700_000_000.0,
                    "end": 1_700_000_000.0,
                }
            ),
        )

    coordinator.schedule_experiment.assert_not_called()


@pytest.mark.asyncio
async def test_handle_schedule_experiment_returns_experiment_id(monkeypatch):
    """schedule_experiment returns experiment_id for valid input."""
    coordinator = _stub_coordinator()
    hass = SimpleNamespace()
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)

    def _capture_experiment(exp):
        coordinator._scheduled = exp

    coordinator.schedule_experiment.side_effect = _capture_experiment

    result = await handle_schedule_experiment(
        hass,
        SimpleNamespace(
            data={
                "room_name": "living_room",
                "start": 1_700_000_000.0,
                "end": 1_700_086_400.0,
            }
        ),
    )

    assert "experiment_id" in result
    assert result["experiment_id"] == coordinator._scheduled.id
    coordinator.schedule_experiment.assert_called_once()
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_estimate_ml_merges_success_into_sysid_results(monkeypatch):
    """estimate_parameters_ml merges successful results into sysid_results."""
    coordinator = _stub_coordinator(room_names=["Living Room"])
    coordinator.sysid_results = {"Living Room": {"rmse": 0.2}}
    coordinator.async_estimate_parameters_ml = AsyncMock(
        return_value={
            "success": True,
            "estimated_params": {
                "Living Room": {
                    "thermal_mass": 5_500_000.0,
                    "r_external": 0.06,
                }
            },
            "estimated_internal_gains": {"Living Room": 180.0},
            "estimated_solar_scales": {},
            "estimated_envelope_splits": {},
            "estimated_t_wall_initial": {"Living Room": 19.8},
            "estimated_heater_scales": {},
            "estimated_inter_room_r": {},
        }
    )
    hass = SimpleNamespace()
    hass.bus = SimpleNamespace(async_fire=MagicMock())
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)
    monkeypatch.setattr(ident_mod, "records_for_datasets", lambda *_a, **_k: None)
    monkeypatch.setattr(ident_mod, "records_for_dataset", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ident_mod,
        "get_history_for_horizon",
        AsyncMock(return_value=[{"y": [21.0]}]),
    )

    await handle_estimate_ml(
        hass,
        SimpleNamespace(data={"horizon_hours": 6.0}),
    )

    room = coordinator.sysid_results["Living Room"]
    assert room["thermal_mass"] == 5_500_000.0
    assert room["r_external"] == 0.06
    assert room["internal_gain"] == 180.0
    assert room["t_wall_initial"] == 19.8
    assert room["rmse"] == 0.2
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_handle_estimate_ml_prefers_dataset_ids_over_horizon(monkeypatch):
    """dataset_ids history takes precedence over horizon_hours lookup."""
    coordinator = _stub_coordinator()
    dataset_history = [{"y": [20.5], "ts": 1.0}]
    coordinator.async_estimate_parameters_ml = AsyncMock(
        return_value={"success": False}
    )
    hass = SimpleNamespace()
    hass.bus = SimpleNamespace(async_fire=MagicMock())
    monkeypatch.setattr(ident_mod, "get_coordinator", lambda _h: coordinator)
    monkeypatch.setattr(
        ident_mod,
        "records_for_datasets",
        lambda _coord, dataset_ids: dataset_history if dataset_ids else None,
    )
    monkeypatch.setattr(ident_mod, "dataset_boundaries", lambda *_a, **_k: [1.0])
    horizon_mock = AsyncMock(return_value=[{"y": [99.0]}])
    monkeypatch.setattr(ident_mod, "get_history_for_horizon", horizon_mock)

    await handle_estimate_ml(
        hass,
        SimpleNamespace(
            data={"dataset_ids": ["ds-1"], "horizon_hours": 6.0}
        ),
    )

    horizon_mock.assert_not_awaited()
    coordinator.async_estimate_parameters_ml.assert_awaited_once_with(
        apply_params=False,
        horizon_hours=None,
        locked_params=None,
        history_override=dataset_history,
        dataset_start_timestamps=[1.0],
    )

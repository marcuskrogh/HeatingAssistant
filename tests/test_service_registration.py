"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import inspect
from types import SimpleNamespace

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from heatingassistant.engine.const import DOMAIN


def _capture_handlers(hass):
    handlers: dict = {}

    def _register(domain, name, handler, **_kwargs):
        assert domain == DOMAIN
        handlers[name] = handler

    hass.services = SimpleNamespace(async_register=_register)
    init_mod._register_services(hass)
    return handlers


def test_registered_handlers_are_awaitable_coroutines():
    """Every domain service must register as an awaitable coroutine callable."""
    handlers = _capture_handlers(SimpleNamespace())

    assert handlers, "expected Heating Assistant services to be registered"
    non_coroutine = {
        name: handler
        for name, handler in handlers.items()
        if not inspect.iscoroutinefunction(handler)
    }
    assert not non_coroutine, (
        "async service handlers must use functools.partial(handler, hass), "
        f"not lambda wrappers: {sorted(non_coroutine)}"
    )


@pytest.mark.parametrize(
    "expected_services",
    [
        {
            "set_schedule_enabled",
            "set_system_enabled",
            "update_room_schedule",
            "set_room_comfort_offset",
            "set_room_setpoint",
            "set_room_enabled",
            "update_controller_tuning",
            "update_estimation_params",
            "update_ui_settings",
            "update_system_params",
            "update_rooms",
            "update_heat_sources",
            "update_system_config",
            "regenerate_dashboard",
            "analyze_model_fit",
            "validate_parameters",
            "controller_performance_report",
            "compute_loglik_slice",
            "simulate_thermal_response",
            "estimate_parameters",
            "estimate_parameters_ml",
            "run_open_loop_simulation",
            "run_sysid_simulation",
            "apply_manual_parameters",
            "apply_heater_scales",
            "reset_estimated_parameters",
            "store_identified_parameters",
            "revert_parameters",
            "delete_parameter_history",
            "schedule_experiment",
            "cancel_experiment",
            "delete_experiment",
            "create_dataset",
            "delete_dataset",
        }
    ],
)
def test_expected_persistence_services_registered(expected_services):
    """Guard the panel-facing services that must survive reload."""
    handlers = _capture_handlers(SimpleNamespace())
    missing = expected_services - set(handlers)
    assert not missing, f"missing registered services: {sorted(missing)}"

"""Lock ControlEngine build/preview mixins after the SWD-443 split."""

from heatingassistant.engine.control_engine_build import BuildMixin
from heatingassistant.engine.control_engine_preview import PreviewMixin
from heatingassistant.engine.control_loop import ControlEngine


def test_control_engine_is_composed_of_build_and_preview_mixins() -> None:
    assert issubclass(ControlEngine, BuildMixin)
    assert issubclass(ControlEngine, PreviewMixin)
    assert ControlEngine._try_build_controller is BuildMixin._try_build_controller
    assert (
        ControlEngine._build_controller_from_config
        is BuildMixin._build_controller_from_config
    )
    assert ControlEngine.preview_tuning_forecast is PreviewMixin.preview_tuning_forecast
    assert ControlEngine._preview_matches_live is PreviewMixin._preview_matches_live


def test_preview_helpers_reexported_from_control_loop() -> None:
    from heatingassistant.engine import control_engine_preview as preview
    from heatingassistant.engine import control_loop as loop

    assert loop._PREVIEW_TUNING_KEYS is preview._PREVIEW_TUNING_KEYS
    assert loop._PREVIEW_WEIGHT_DEFAULTS is preview._PREVIEW_WEIGHT_DEFAULTS
    assert loop._snapshot_from_controller is preview._snapshot_from_controller

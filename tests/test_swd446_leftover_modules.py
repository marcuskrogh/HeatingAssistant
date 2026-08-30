"""Lock package re-exports after the SWD-446 leftover splits."""

from heatingassistant.engine.heat_sources import (
    ElectricHeater,
    HeatPump,
    HeatSource,
    _cop_at_temp,
    _soft_ceiling,
)
from heatingassistant.engine.heat_sources.base import HeatSource as BaseHeatSource
from heatingassistant.engine.heat_sources.electric import ElectricHeater as Electric
from heatingassistant.engine.heat_sources.heat_pump import HeatPump as Pump
from heatingassistant.engine.heat_sources.heat_pump import _cop_at_temp as cop
from heatingassistant.engine.heat_sources.base import _soft_ceiling as ceiling


def test_heat_source_package_reexports_match_modules() -> None:
    assert HeatSource is BaseHeatSource
    assert ElectricHeater is Electric
    assert HeatPump is Pump
    assert _cop_at_temp is cop
    assert _soft_ceiling is ceiling


def test_thin_init_imports_bridge_manager() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (
        root / "custom_components" / "heating_assistant" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "from .bridge_manager import" in source
    assert "_BridgeManager" in source
    sync = (root / "scripts" / "sync-ha-app-package.sh").read_text(encoding="utf-8")
    assert sync.count("bridge_manager.py") >= 2
    packed = (
        root
        / "heating_assistant"
        / "custom_components"
        / "heating_assistant"
        / "bridge_manager.py"
    )
    assert packed.is_file()

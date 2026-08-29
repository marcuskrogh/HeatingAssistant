"""Lock package re-exports after the SWD-441 controller module split."""

from heatingassistant.engine.controller import (
    HeatingLinearisedMPC,
    HeatingMPCController,
    HouseThermalSDE,
    _InnovationEKF,
)
from heatingassistant.engine.controller.ekf import _InnovationEKF as EKF
from heatingassistant.engine.controller.facade import HeatingMPCController as Facade
from heatingassistant.engine.controller.linearised import (
    HeatingLinearisedMPC as Linearised,
)
from heatingassistant.engine.controller.sde import HouseThermalSDE as SDE


def test_package_reexports_are_the_module_classes() -> None:
    assert HouseThermalSDE is SDE
    assert HeatingLinearisedMPC is Linearised
    assert HeatingMPCController is Facade
    assert _InnovationEKF is EKF

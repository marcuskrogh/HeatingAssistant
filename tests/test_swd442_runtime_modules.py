"""Lock HeatingRuntime collaborator mixins after the SWD-442 split."""

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.app.runtime_history import HistoryMixin
from heatingassistant.app.runtime_nmpc import NmpcMixin
from heatingassistant.app.runtime_states import HassStatesMixin
from heatingassistant.app.runtime_ticker import TickerMixin
from heatingassistant.app.runtime_wiring import WiringMixin


def test_heating_runtime_is_composed_of_collaborator_mixins() -> None:
    assert issubclass(HeatingRuntime, TickerMixin)
    assert issubclass(HeatingRuntime, NmpcMixin)
    assert issubclass(HeatingRuntime, HassStatesMixin)
    assert issubclass(HeatingRuntime, WiringMixin)
    assert issubclass(HeatingRuntime, HistoryMixin)
    assert HeatingRuntime.hass_states is HassStatesMixin.hass_states
    assert HeatingRuntime._apply_entity_wiring is WiringMixin._apply_entity_wiring
    assert HeatingRuntime._background_ticker_loop is TickerMixin._background_ticker_loop
    assert HeatingRuntime._nmpc_worker_thread is NmpcMixin._nmpc_worker_thread
    assert HeatingRuntime._record_history_samples is HistoryMixin._record_history_samples

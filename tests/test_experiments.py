"""Unit tests for the system-identification experiment data model and signals."""

from __future__ import annotations

from custom_components.heating_assistant import experiments as E


def _make_exp(**kw):
    base = dict(
        room_name="Living Room",
        room_slug="living_room",
        start_ts=1_000.0,
        end_ts=1_000.0 + 8 * 3600,
        signal_type="prbs",
        amplitude_high=1.0,
        amplitude_low=0.0,
        period_s=3600.0,
        seed=42,
    )
    base.update(kw)
    return E.Experiment(**base)


# --------------------------------------------------------------------------
# Excitation signal
# --------------------------------------------------------------------------

def test_prbs_is_deterministic_for_a_given_seed():
    exp_a = _make_exp(seed=12345)
    exp_b = _make_exp(seed=12345)
    times = [1_000.0 + k * 1800 for k in range(16)]
    assert [E.excitation_fraction(exp_a, t) for t in times] == [
        E.excitation_fraction(exp_b, t) for t in times
    ]


def test_prbs_uses_high_and_low_amplitudes_only():
    exp = _make_exp(amplitude_high=0.8, amplitude_low=0.2, seed=7)
    vals = {E.excitation_fraction(exp, 1_000.0 + k * 3600) for k in range(50)}
    assert vals <= {0.2, 0.8}
    # A non-trivial PRBS should exercise both levels over many steps.
    assert vals == {0.2, 0.8}


def test_step_signal_is_high_for_whole_window():
    exp = _make_exp(signal_type="step")
    for k in range(8):
        assert E.excitation_fraction(exp, 1_000.0 + k * 3600) == 1.0


def test_pulse_alternates_each_period():
    exp = _make_exp(signal_type="pulse")
    assert E.excitation_fraction(exp, 1_000.0 + 0 * 3600) == 1.0
    assert E.excitation_fraction(exp, 1_000.0 + 1 * 3600) == 0.0
    assert E.excitation_fraction(exp, 1_000.0 + 2 * 3600) == 1.0


def test_signal_is_low_outside_the_window():
    exp = _make_exp(signal_type="step", amplitude_low=0.1)
    assert E.excitation_fraction(exp, 999.0) == 0.1          # before start
    assert E.excitation_fraction(exp, exp.end_ts + 10) == 0.1  # after end


# --------------------------------------------------------------------------
# Safety bounds
# --------------------------------------------------------------------------

def test_safety_forces_on_below_min_temp():
    assert E.apply_safety_bounds(0.0, 8.0, 12.0, 26.0, 1.0) == 1.0


def test_safety_forces_off_above_max_temp():
    assert E.apply_safety_bounds(1.0, 27.0, 12.0, 26.0, 1.0) == 0.0


def test_safety_passes_through_inside_band():
    assert E.apply_safety_bounds(0.7, 20.0, 12.0, 26.0, 1.0) == 0.7


def test_safety_ignores_missing_measurement():
    assert E.apply_safety_bounds(0.5, None, 12.0, 26.0, 1.0) == 0.5


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_validate_rejects_unknown_signal():
    try:
        E.validate_signal_params("noise", 1.0, 0.0, 3600.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown signal type")


def test_validate_rejects_high_not_above_low():
    try:
        E.validate_signal_params("prbs", 0.5, 0.5, 3600.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError when high <= low")


# --------------------------------------------------------------------------
# Manager state machine
# --------------------------------------------------------------------------

def test_active_for_room_within_window():
    m = E.ExperimentManager()
    exp = _make_exp()
    m.add(exp)
    assert m.active_for_room("living_room", 1_000.0 + 3600) is exp
    assert m.active_for_room("living_room", 999.0) is None
    assert m.active_for_room("kitchen", 1_000.0 + 3600) is None


def test_advance_transitions_scheduled_to_running_to_completed():
    m = E.ExperimentManager()
    exp = _make_exp()
    m.add(exp)

    started = m.advance(1_000.0 + 60)
    assert exp.status == E.STATUS_RUNNING
    assert exp in started["started"]

    done = m.advance(exp.end_ts + 1)
    assert exp.status == E.STATUS_COMPLETED
    assert exp in done["completed"]


def test_cancel_marks_cancelled_and_stops_being_active():
    m = E.ExperimentManager()
    exp = _make_exp()
    m.add(exp)
    assert m.cancel(exp.id) is exp
    assert exp.status == E.STATUS_CANCELLED
    assert m.active_for_room("living_room", 1_000.0 + 3600) is None


def test_earliest_experiment_wins_when_overlapping():
    m = E.ExperimentManager()
    early = _make_exp(start_ts=1_000.0)
    late = _make_exp(start_ts=1_500.0)
    m.add(late)
    m.add(early)
    assert m.active_for_room("living_room", 2_000.0) is early


def test_roundtrip_serialisation():
    m = E.ExperimentManager()
    exp = _make_exp(name="overnight")
    m.add(exp)
    restored = E.ExperimentManager.from_list(m.to_list())
    assert len(restored.experiments) == 1
    r = restored.experiments[0]
    assert r.id == exp.id
    assert r.name == "overnight"
    assert r.seed == exp.seed


def test_from_dict_tolerates_unknown_keys():
    data = _make_exp().to_dict()
    data["future_field"] = "ignored"
    exp = E.Experiment.from_dict(data)
    assert exp.room_slug == "living_room"


def test_prune_terminal_keeps_active_and_recent():
    m = E.ExperimentManager()
    # 30 completed + 1 scheduled
    for i in range(30):
        e = _make_exp(start_ts=float(i), end_ts=float(i) + 1.0)
        m.add(e)
        m.advance(float(i) + 2.0)  # completes it
    scheduled = _make_exp(start_ts=1e9, end_ts=1e9 + 3600)
    m.add(scheduled)
    m.prune_terminal(keep=10)
    statuses = [e.status for e in m.experiments]
    assert statuses.count(E.STATUS_COMPLETED) == 10
    assert scheduled in m.experiments

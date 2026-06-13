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
        step_pct=1.0,
        period_s=3600.0,
        # PRBS/pulse signal tests below assume the whole window is excited;
        # the settle-buffer behaviour is covered explicitly in its own tests.
        settle_s=0.0,
        seed=42,
    )
    base.update(kw)
    return E.Experiment(**base)


# --------------------------------------------------------------------------
# Excitation signal — PRBS / pulse
# --------------------------------------------------------------------------

def test_prbs_is_deterministic_for_a_given_seed():
    exp_a = _make_exp(seed=12345)
    exp_b = _make_exp(seed=12345)
    times = [1_000.0 + k * 1800 for k in range(16)]
    assert [E.excitation_fraction(exp_a, t) for t in times] == [
        E.excitation_fraction(exp_b, t) for t in times
    ]


def test_prbs_uses_zero_and_step_pct_only():
    exp = _make_exp(signal_type="prbs", step_pct=0.8, seed=7)
    vals = {E.excitation_fraction(exp, 1_000.0 + k * 3600) for k in range(8)}
    assert vals == {0.0, 0.8}


def test_pulse_alternates_between_zero_and_step():
    exp = _make_exp(signal_type="pulse", step_pct=1.0)
    assert E.excitation_fraction(exp, 1_000.0 + 0 * 3600) == 1.0
    assert E.excitation_fraction(exp, 1_000.0 + 1 * 3600) == 0.0
    assert E.excitation_fraction(exp, 1_000.0 + 2 * 3600) == 1.0


def test_signal_is_zero_outside_the_window():
    exp = _make_exp(signal_type="step")
    assert E.excitation_fraction(exp, 999.0) == 0.0          # before start
    assert E.excitation_fraction(exp, exp.end_ts + 10) == 0.0  # after end


# --------------------------------------------------------------------------
# Excitation signal — multi-phase step test
# --------------------------------------------------------------------------

def test_step_heat_only_pattern_is_settle_heat_settle():
    # Heat-only (can_cool=False): 0 → +step → 0 over three equal phases.
    exp = _make_exp(signal_type="step", step_pct=0.75)
    phase = (exp.end_ts - exp.start_ts) / 3
    assert E.excitation_fraction(exp, exp.start_ts + 0.5 * phase) == 0.0
    assert E.excitation_fraction(exp, exp.start_ts + 1.5 * phase) == 0.75
    assert E.excitation_fraction(exp, exp.start_ts + 2.5 * phase) == 0.0


def test_step_heat_cool_pattern_includes_cool_phase():
    # Reversible (can_cool=True): 0 → +step → 0 → -step → 0 over five phases.
    exp = _make_exp(signal_type="step", step_pct=0.5)
    phase = (exp.end_ts - exp.start_ts) / 5
    expected = [0.0, 0.5, 0.0, -0.5, 0.0]
    for i, level in enumerate(expected):
        t = exp.start_ts + (i + 0.5) * phase
        assert E.excitation_fraction(exp, t, can_cool=True) == level


def test_step_heat_only_never_emits_cool():
    exp = _make_exp(signal_type="step", step_pct=1.0)
    dur = exp.end_ts - exp.start_ts
    vals = [
        E.excitation_fraction(exp, exp.start_ts + (k + 0.5) * dur / 12, can_cool=False)
        for k in range(12)
    ]
    assert min(vals) >= 0.0


def test_step_uses_full_window_ignoring_settle_buffer():
    # The step's interleaved zeros are its settles, so settle_s does not clip it.
    exp = _make_exp(signal_type="step", step_pct=1.0, settle_s=4 * 3600)
    phase = (exp.end_ts - exp.start_ts) / 3
    assert E.excitation_fraction(exp, exp.start_ts + 1.5 * phase) == 1.0


# --------------------------------------------------------------------------
# Settle / response buffer (PRBS / pulse)
# --------------------------------------------------------------------------

def test_settle_buffer_stops_excitation_before_window_end():
    # 8 h window, 2 h settle buffer → PRBS/pulse rest at 0 in the final 2 h.
    exp = _make_exp(signal_type="pulse", settle_s=2 * 3600, step_pct=1.0)
    assert exp.excitation_end_ts == exp.end_ts - 2 * 3600
    assert E.excitation_fraction(exp, exp.start_ts + 60) == 1.0          # exciting
    assert E.excitation_fraction(exp, exp.excitation_end_ts + 60) == 0.0  # settling
    assert E.excitation_fraction(exp, exp.end_ts - 60) == 0.0


def test_settle_phase_helpers():
    exp = _make_exp(settle_s=2 * 3600)
    t_excite = exp.start_ts + 3600
    t_settle = exp.end_ts - 60
    assert exp.is_exciting_at(t_excite) is True
    assert exp.is_settling_at(t_excite) is False
    assert exp.is_exciting_at(t_settle) is False
    assert exp.is_settling_at(t_settle) is True
    # The experiment still owns the heaters during the settle tail.
    assert exp.is_active_at(t_settle) is True


def test_settle_buffer_never_swallows_whole_window():
    # A buffer longer than the window is clamped so excitation_end == start.
    exp = _make_exp(settle_s=100 * 3600)
    assert exp.excitation_end_ts == exp.start_ts


def test_default_signal_type_is_step():
    from custom_components.heating_assistant.const import (
        DEFAULT_EXCITATION_TYPE,
        EXCITATION_STEP,
    )

    assert DEFAULT_EXCITATION_TYPE == EXCITATION_STEP
    exp = E.Experiment(
        room_name="r", room_slug="r", start_ts=0.0, end_ts=3600.0,
    )
    assert exp.signal_type == EXCITATION_STEP


# --------------------------------------------------------------------------
# Safety bounds
# --------------------------------------------------------------------------

def test_safety_forces_on_below_min_temp():
    assert E.apply_safety_bounds(0.0, 8.0, 12.0, 26.0, 0.75) == 0.75


def test_safety_forces_off_above_max_temp():
    assert E.apply_safety_bounds(1.0, 27.0, 12.0, 26.0, 1.0) == 0.0


def test_safety_passes_through_inside_band():
    assert E.apply_safety_bounds(-0.7, 20.0, 12.0, 26.0, 1.0) == -0.7


def test_safety_ignores_missing_measurement():
    assert E.apply_safety_bounds(0.5, None, 12.0, 26.0, 1.0) == 0.5


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_validate_rejects_unknown_signal():
    try:
        E.validate_signal_params("noise", 1.0, 3600.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown signal type")


def test_validate_rejects_step_pct_out_of_range():
    for bad in (0.0, -0.1, 1.5):
        try:
            E.validate_signal_params("step", bad, 3600.0)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for step_pct={bad}")


def test_validate_accepts_valid_params():
    E.validate_signal_params("step", 0.5, 3600.0)  # must not raise


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
    # An experiment persisted under the old min/max amplitude schema still loads.
    data["amplitude_high"] = 0.9
    exp = E.Experiment.from_dict(data)
    assert exp.room_slug == "living_room"
    assert not hasattr(exp, "amplitude_high")


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

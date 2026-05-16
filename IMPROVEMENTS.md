# Potential Improvements

A living menu of improvement targets for HeatingAssistant. Items are grouped by
theme and tagged with `Impact` and `Effort`. Check items off as they land and
link the implementing commit / PR.

Legend:
- `[ ]` open
- `[x]` shipped — annotate with commit SHA or PR number
- `[~]` in progress

---

## Performance & efficiency

- [x] **P1. Cache `heat_sources` by room.** ~~`sensor.py` filters
  `coordinator.heat_sources` by `room` on every attribute access at lines 467,
  780, 922, 934, 1112 (≈8 hot-path comprehensions). Build a
  `coordinator._sources_by_room` mapping once on init / on options reload and
  read from it in the sensors.~~ *Impact: medium · Effort: small · Strictly
  faster.* — _Coordinator now exposes `sources_for_room(room_name)` backed by
  a `_sources_by_room` cache that's rebuilt whenever `heat_sources` is
  reassigned. All seven per-room filter sites in `sensor.py` (incl. the
  O(N×M) loop in `SystemEfficiencySensor`) now read from the cache._

- [x] **P2. Reuse a single `datetime.now(tz=utc)` per update.** ~~Five
  forecast sensors (`sensor.py:409, 678, 771, 1096, 1179`) each call
  `datetime.now(...)` independently. Stash `self._now_utc` on the coordinator
  at the start of `_async_update_data` and read from sensors.~~ *Impact: low ·
  Effort: small · Also gives consistent timestamps across sensors.* —
  _Coordinator stamps `self.now_utc` once at the start of `_async_update_data`
  (and is initialised in `__init__` for safety before the first cycle).
  All five forecast-sensor sites — including the shared
  `_horizon_scalar_forecast_attrs` helper, `OutdoorTemperatureForecastSensor`,
  `TemperatureForecastSensor`, `HeatingPowerForecastSensor`, and
  `SolarGainForecastSensor` — read from `coordinator.now_utc` (with a tiny
  `getattr(...) or datetime.now(...)` fallback for SimpleNamespace test
  stubs)._

- [ ] **P3. Skip ConfigEntry write when estimated parameters are unchanged.**
  `coordinator.py` (~line 1787) writes the full snapshot to `entry.data`
  after every estimation. Hash-compare the snapshot and skip if equal.
  *Impact: medium (recorder/storage churn) · Effort: small.*

- [ ] **P4. Pre-compute constant parameter-Jacobian blocks in
  `parameter_estimator.py`.** `_kalman_log_likelihood` forward pass rebuilds
  `dF/dθ` blocks every step. Cache the linear (parameter-independent)
  structure once per solve, only refresh the parameter-varying entries inside
  the loop. *Impact: high (estimation runtime) · Effort: large · Strictly
  faster, but largest refactor.*

- [x] **P5. Merge the two forecast-sensor loops.** ~~`sensor.py:752–841`
  walks `predictions` twice (extract trajectory, then build dict list). Fuse
  into a single pass.~~ *Impact: low · Effort: small.* —
  _`TemperatureForecastSensor.extra_state_attributes` now walks `predictions`
  exactly once, populating `trajectory` and the per-step `forecast` entry in
  the same iteration. Per-coordinator-cycle work is halved for that sensor
  (which runs once per room per update)._

## Usability

- [ ] **U1. Tag diagnostic sensors with `EntityCategory.DIAGNOSTIC`.**
  `sensor.py` currently uses no `EntityCategory` anywhere. Sensors like
  `ModelFitQuality`, `ParameterConfidence`, `KalmanInnovation`, `ResidualACF`,
  prediction-error sensors should be diagnostic so they don't clutter the
  main device card. *Impact: medium UX win · Effort: small.*

- [ ] **U2. Add `suggested_display_precision` to numeric forecast/measurement
  sensors.** Temperature (1), power (0 or 1), energy (1). Tightens the HA
  UI. *Impact: low · Effort: small.*

- [ ] **U3. Log + surface weather-forecast fetch failures.**
  `coordinator._async_read_weather_forecast` returns `None` silently. Log at
  WARN with throttling and expose a "last weather error" attribute on a
  diagnostic sensor. *Impact: medium for debugging · Effort: small.*

- [ ] **U4. Split the options flow into a helper class.** `config_flow.py`
  has 15+ `async_step_*` methods for room/window CRUD. Extracting a
  `RoomFlowHelper` / `WindowFlowHelper` shrinks the file and makes individual
  flows unit-testable. *Impact: medium maintainability · Effort: large · Pure
  refactor, no runtime change.*

## Simplicity / code quality

- [ ] **S1. Inline the thin shim modules.** `optimal_control.py` (11 lines)
  and `state_estimator.py` (11 lines) are pure `from mbc...` re-exports.
  Delete them and import directly in `controller.py` /
  `parameter_estimator.py`. *Impact: low · Effort: small.*

- [ ] **S2. Extract YAML-merge logic out of `__init__.py`.** `__init__.py` is
  1133 lines and mixes setup, the `_MergedEntry` proxy, and YAML merging.
  Move merge logic to `yaml_merge.py`. *Impact: medium maintainability ·
  Effort: medium.*

- [ ] **S3. Modularise `coordinator.py` (1793 lines).** Split state-estimation,
  MPC dispatch, history-buffer, and weather-fetch helpers into sibling
  modules; keep `HeatingDataUpdateCoordinator` as the orchestrator.
  *Impact: medium maintainability · Effort: large · No runtime effect if done
  as a pure move.*

- [ ] **S4. Group coordinator `__init__` setup into helpers.** Coordinator
  constructor sets ~30 instance vars inline. Extract `_init_model()`,
  `_init_estimator()`, `_init_buffers()`. *Impact: low · Effort: small.*

---

## Rejected / not pursued

- ~~"Unused `import copy` in `parameter_estimator.py`"~~ — false positive;
  `copy.deepcopy` is used at lines 992 and 1060.
- ~~"Hardcoded sensor names need i18n"~~ — HA convention is to ship Python
  names and let users override per entity.

---

## Changelog

| Date       | Items shipped     | Notes                                                                                  |
|------------|-------------------|----------------------------------------------------------------------------------------|
| 2026-05-16 | P1, P2, P5        | First performance pass. 365 tests pass; the 14 pre-existing `mbc`-dependent failures (EKF stubs in the CI env) are unchanged. |

# Architecture: P tracks the original NMPC trajectory for 2 h

## Shape
- Lives: `heatingassistant/engine/controller/facade.py`
  (`set_accepted_path`, `_p_command_vector`, `_publish_plan_rollout`,
  `_forecast_T` / `_forecast_U` via `_pad_plan_tail`) and
  `heatingassistant/engine/nmpc_p.py` (`p_command` unchanged)
- Depends on: existing NMPC accept result (`u_star`, `t_ref`); EKF
  `x_hat` for the tracking error only
- Seams: `set_accepted_path` is the only writer of the P / Forecast
  reference; tests call it, then `compute()` / `rebuild_forecast_from_plan`,
  then `_p_command_vector` and `predictions`
- Will not add: a second reference trajectory type, a plot-to-P adapter,
  or a new control module

## Neighbourhood
- Opened modules/boundaries: `HeatingMPCController` slow-plan lock
  (`_nmpc_lock`) already owns `_nmpc_T_ref`, `_nmpc_U`, `_nmpc_k`
- Plot path `_publish_plan_rollout` / `rebuild_forecast_from_plan` reads
  leftover `T_ref` and leftover `U*` into `_predictions` /
  `_heating_schedule`. `_compute_nonlinear_predictions` stays the no-plan
  fallback and a Tuning helper
- Major refinement (or none): remaining-path pad is one helper for U and T

## Tracker
- Task: SWD-465
- Branch: `cursor/swd-465-p-frozen-tref-105a`

## Next
`/test SWD-465` — Dedicated testing phase, then restructure, then review

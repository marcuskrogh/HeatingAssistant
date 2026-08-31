# Architecture: P tracks the original NMPC trajectory for 2 h

## Shape
- Lives: `heatingassistant/engine/controller/facade.py`
  (`set_accepted_path`, `_p_command_vector`) and
  `heatingassistant/engine/nmpc_p.py` (`p_command` unchanged)
- Depends on: existing NMPC accept result (`u_star`, `t_ref`); EKF
  `x_hat` for the tracking error only
- Seams: `set_accepted_path` is the only writer of the P reference;
  tests call it, then `compute()` / `rebuild_forecast_from_plan`, then
  `_p_command_vector`
- Will not add: a second reference trajectory type, a plot-to-P adapter,
  or a new control module

## Neighbourhood
- Opened modules/boundaries: `HeatingMPCController` slow-plan lock
  (`_nmpc_lock`) already owns `_nmpc_T_ref`, `_nmpc_U`, `_nmpc_k`
- Plot path `_publish_plan_rollout` / `rebuild_forecast_from_plan` stays
  a reader of leftover `U*` and a writer of `_predictions` only
- Major refinement (or none): none — copy at the existing accept seam

## Tracker
- Task: SWD-465
- Branch: `cursor/swd-465-p-frozen-tref-105a`

## Next
`/implement SWD-465` — Build to this shape

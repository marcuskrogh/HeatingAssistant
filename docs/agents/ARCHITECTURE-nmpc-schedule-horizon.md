# Architecture: NMPC schedule horizon preheat

## Shape
- Lives: `heatingassistant/engine/schedule_control.py` (horizon arrays), `heatingassistant/engine/controller/facade.py` (OCP/QP bounds), `heatingassistant/app/runtime.py` (grid + `disabled_sources`), `heatingassistant/engine/control_engine_preview.py` (preview NLP kwargs).
- Depends on: `RoomSchedule.active` / `control_params_at`; NMPC timing (`n_fast`, `dt_s`).
- Seams: `ControlTrajectory`; `_comfort_bounds_fast`; `_apply_off_period_u_hold`; `off_step_holds_heater_off` / `schedule_off_zeros_live_actuation`.
- Will not add: extra planner, new MQTT topics, UI surfaces.

## Neighbourhood
- Opened modules/boundaries: schedule projection → controller bounds → live actuation mask.
- Major refinement (or none): none — same trajectory object; off steps change bound meaning only.

## Tracker
- Task: SWD-545
- Branch: `cursor/nmpc-schedule-horizon-605a`

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/683

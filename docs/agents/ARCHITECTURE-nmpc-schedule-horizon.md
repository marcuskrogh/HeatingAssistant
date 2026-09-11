# Architecture: NMPC schedule horizon preheat

## Shape
- Lives: `heatingassistant/engine/schedule_control.py` (horizon arrays), `heatingassistant/engine/controller/facade.py` (OCP/QP bounds), `heatingassistant/app/runtime.py` (grid + `disabled_sources`), `heatingassistant/engine/control_engine_preview.py` (preview NLP kwargs).
- Depends on: `RoomSchedule.active` / `control_params_at`; NMPC timing (`n_fast`, `dt_s`).
- Seams: `ControlTrajectory` (setpoints, offsets, `enabled_steps`, `frost_floors`); `_comfort_bounds_fast(traj, N)`; runtime `_schedule_control_context`.
- Will not add: extra planner, new MQTT topics, UI surfaces.

## Neighbourhood
- Opened modules/boundaries: schedule projection → controller bounds → live actuation mask.
- Major refinement (or none): none — same trajectory object; off steps change bound meaning only.

## Tracker
- Task: SWD-545
- Branch: `cursor/nmpc-schedule-horizon-605a`

## Next
`/implement SWD-545` — Build to this shape

# Architecture: Configurable NMPC max compute time

## Shape
- Lives: `heatingassistant/engine/const.py` (key + coerce), `nmpc_ocp.py` (default alias), `controller/facade.py` + `controller/factory.py` + `control_engine_build.py` (store and pass timeout), `control_engine_preview.py` (preview key), `app/runtime.py` (snapshot + options), `app/static/js/pages/tuning-controller.js` (Nonlinear live field)
- Depends on: existing controller config / `update_controller_tuning` persistence
- Seams: `coerce_nmpc_max_compute_s`, `HeatingMPCController._nmpc_timeout_s` (NMPC deadline + HiGHS `time_limit`), `controller_config()` JSON, Tuning `SHARED_LIVE_PARAM_DEFS`
- Will not add: new layers, services, or a second timeout store

## Neighbourhood
- Opened modules/boundaries: NMPC OCP timeout already on `solve_mean_ocp`; Tuning already splits Linear-only live knobs
- Major refinement (or none): none — reuse PE’s “max compute seconds” idea on Tuning, not Advanced

## Tracker
- Task: SWD-542
- Branch: `cursor/nmpc-max-compute-0f2f`

## Next
`/implement SWD-542` — Build to this shape

# Architecture: Fitted wall initial temperature for open-loop fit

## Shape
- Lives: `heatingassistant/engine/parameter_lifecycle.py` (snapshot fields + lookup), `heatingassistant/app/sysid_services.py` (last-PE-fit cache, resolve on EKF/open-loop), `heatingassistant/engine/simulation/sysid_helpers.py` (window-only Tw0 prior), PE Tw0 field in `identification/sysid-detail.js` + markup
- Depends on: existing `KalmanMLEstimator.estimate_wall_initial_only`, `estimated_t_wall_initial` / `estimated_t_wall_per_dataset` from `kalman_ml.py`
- Seams: `lookup_fitted_t_wall_initial`, `_resolve_simulation_t_wall`, `runtime._last_pe_fit`; tests monkeypatch the window optimiser
- Will not add: new packages, new HTTP resources, or a second estimator

## Neighbourhood
- Opened modules: parameter snapshot, sysid services, PE page Tw0 field
- Major refinement: none — add keys on the existing ML snapshot and a session cache

## Tracker
- Task: SWD-477
- Branch: `cursor/swd-477-wall-init-a761`

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/656 (`fc124b0`)

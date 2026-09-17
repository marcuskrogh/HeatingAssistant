# Architecture: 2R2C wall physics in estimation

## Shape
- Lives: `heatingassistant/engine/wall_physics.py` (SS, fusion, air–wall isolation); `controller/ekf.py` (zero \(P_{wa}\) then SS fusion); `sysid.py` and `initial_state_estimator.py` (same before replay updates); `controller/sde.py`; `estimation/` MAP and N-step residual
- Depends on: existing CD-EKF, `_ThetaLayout`, N-step sensitivities `sx`, `_theta_model_quantities`
- Seams: `wall_steady_state`, `fuse_wall_equilibrium`, `isolate_air_wall_covariance`, `accumulate_wall_ss_penalty`, `tw0_ss_means`
- Will not add: clip/envelope module, constrained-QP Kalman, extra thermal node, UI series

## Neighbourhood
- Opened modules: CD-EKF wrapper, HouseThermalSDE diffusion, grey-box PE
- Major refinement: shared physics helper; controller must not import `estimation`

## Tracker
- Task: SWD-564
- Branch: `cursor/swd-564-wall-envelope-a891`

## Next
`/implement SWD-564` — Build to this shape

# Architecture: Physical envelope for wall temperature

## Shape
- Lives: `heatingassistant/engine/wall_constraints.py` (envelope math); `controller/sde.py` (wall σ); `controller/ekf.py` (`_InnovationEKF.step` projection); `controller/facade.py` (initial wall \(P_0\)); `sysid.py` and `initial_state_estimator.py` (replay EKF projection); `estimation/kalman_ml.py` (Tw0 box); `estimation/nstep_pem.py` (path penalty + Tw0 clip)
- Depends on: existing CD-EKF, `_ThetaLayout`, N-step sensitivities `sx`
- Seams: `envelope_limits`, `project_wall_block`, `apply_t_wall_init_envelope_bounds`, `accumulate_wall_envelope_penalty` — unit-tested without a full house
- Will not add: new estimator class, constrained-QP Kalman, extra thermal node, UI series

## Neighbourhood
- Opened modules: CD-EKF wrapper, HouseThermalSDE diffusion, grey-box PE
- Major refinement: none — one shared constraint helper; controller must not import `estimation`

## Tracker
- Task: SWD-564
- Branch: `cursor/swd-564-wall-envelope-a891`

## Next
`/implement SWD-564` — Build to this shape

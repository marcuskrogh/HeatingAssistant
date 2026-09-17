# Architecture: 2R2C wall physics in estimation

## Shape
- Lives: `heatingassistant/engine/wall_physics.py` (SS, fusion, MAP/path helpers); `controller/sde.py` (wall σ, `wall_equilibrium` / `wall_observation`); `controller/ekf.py` (`_InnovationEKF.step` SS fusion); `controller/facade.py` (initial wall \(P_0=\sigma_{\mathrm{lag}}^2\)); `sysid.py` and `initial_state_estimator.py` (replay EKF fusion); `estimation/regularization.py` (Tw0 MAP toward \(T_w^{\mathrm{ss}}(\theta)\)); `estimation/warmstart.py` and `app/sysid_services.py` (RC mix seed); `estimation/nstep_pem.py` (path residual)
- Depends on: existing CD-EKF, `_ThetaLayout`, N-step sensitivities `sx`, `_theta_model_quantities`
- Seams: `wall_steady_state`, `fuse_wall_equilibrium`, `accumulate_wall_ss_penalty`, `tw0_ss_means` — unit-tested without a full house
- Will not add: clip/envelope module, constrained-QP Kalman, extra thermal node, UI series

## Neighbourhood
- Opened modules: CD-EKF wrapper, HouseThermalSDE diffusion, grey-box PE
- Major refinement: shared physics helper; controller must not import `estimation`

## Tracker
- Task: SWD-564
- Branch: `cursor/swd-564-wall-envelope-a891`

## Next
`/implement SWD-564` — Build to this shape

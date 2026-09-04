# Architecture: Horizon-matched N-step PE MLE

## Shape
- Lives: `heatingassistant/engine/estimation/nstep_pem.py` (new objective +
  deadline); `kalman_ml.py` / `nlp_eval.py` (wire NLP); `parameter_lifecycle.py`
  (timing + cap into the estimator); `app/static/js/config/` (Advanced landing
  page); `engine/const.py` + `app/runtime.py` `system_params`
- Depends on: existing `_dfdtheta_step`, parametric 2R2C, `nmpc_timing`,
  `update_config` options dump
- Seams: `KalmanMLEstimator(use_nstep_pem=..., n_horizon_steps=...,
  origin_stride=..., max_compute_s=...)`; `PeComputeTimeout`;
  `score_nstep_path_rmse` for the harness bar
- Will not add: new optimiser, new thermal plant, new HTTP stack, extra
  Advanced knobs

## Neighbourhood
- Opened: grey-box PE objective (`sensitivity.py` tiled OE stays for baseline /
  Tw0-only diagnostic); Ingress Configuration landing
- Major refinement: none — new file for the new objective, not a god-module merge

## Tracker
- Task: SWD-481
- Branch: `cursor/swd-481-pe-nstep-mle-dfe4`

## Next
`/implement SWD-481` — Finish until-bar N-step RMSE vs tiled OE

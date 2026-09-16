# Architecture: PE optimiser stall (SWD-558)

## Shape
- Lives: `heatingassistant/engine/nmpc_timing.py` (`pe_origin_stride`);
  `parameter_lifecycle.py` (wire stride, not `fast_substeps`);
  `estimation/nstep_pem.py` (`PeEtaPlateau`); `nlp_eval.py` /
  `kalman_ml.py` (best-η tracking, plateau abort as success);
  `app/static/js/identification/pe-progress.js` (hero = best RMS);
  `sysid-detail.js` (`waitForPeJob` follows `cap_s`, cancel on abandon)
- Depends on: existing N-step PEM, L-BFGS-B, PE job snapshot, cancel event
- Seams: `pe_origin_stride(dt_s)`; estimator `_pe_best_eta` / `_pe_best_theta`;
  progress fields `eta_best`, `rmse_c_best`; `PeEtaPlateau`
- Will not add: new optimiser, FD gradients, NMPC timing change, extra config knobs

## Neighbourhood
- Opened: PE NLP + Identification overlay (same as SWD-481 / SWD-504)
- Major refinement: none — helper for origin stride, not a new PE module

## Tracker
- Task: SWD-558
- Branch: `cursor/swd-558-pe-optimizer-stall-67bc`

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/687 (`4e5d51c5`)

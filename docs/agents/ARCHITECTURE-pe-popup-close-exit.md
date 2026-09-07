# Architecture: PE popup close and exit label

## Shape
- Lives: Identification overlay JS/CSS; `sysid_services` PE job; estimator NLP
  (`nstep_pem` cancel/timeout, `nlp_eval.solve_lbfgs` exit label, `kalman_ml.estimate`)
- Depends on: existing `pe_job` snapshot and `estimate_parameters_ml` worker
- Seams: `cancel_estimate_parameters_ml` + estimator `_pe_cancel_check`;
  `lbfgs_exit_label(res)` for copy; overlay `data-pe-close` click on the host
- Will not add: WebSocket push, new UI framework, backdrop-dismiss, sandbox tree

## Neighbourhood
- Opened modules/boundaries: `heatingassistant/app/static/js/identification/`,
  `sysid_services.py`, `engine/estimation/{nlp_eval,nstep_pem,kalman_ml}.py`,
  App `apply_service` / HA service name
- Major refinement (or none): none — reuse timeout exception pattern for cancel

## Tracker
- Task: SWD-504
- Branch: cursor/swd-504-pe-popup-close-cfe8

## Next
`/implement SWD-504` — Build to this shape

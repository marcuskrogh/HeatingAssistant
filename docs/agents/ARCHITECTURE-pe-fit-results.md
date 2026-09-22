# Architecture: Archive finished PE results below stored datasets

## Shape
- Lives: `heatingassistant/engine/parameter_lifecycle.py` (catalog persist:
  `pe_fit_results` in options) plus `heatingassistant/app/sysid_services.py`
  (archive after the PE worker; delete service) and Identification UI in
  `sysid-detail.js` / `ha-services.js`.
- Depends on: PE result `exit_label`, `rmse_c_best`, identified θ maps;
  `compute_model_fit_metrics` / equivalent R² on identification-window air
  temperatures; `save_config`; panel `controller_config` snapshot.
- Seams: `pe_fit_should_archive(result)`, `archive_pe_fit_result(options, result)`,
  `delete_pe_fit_result(options, result_id)` — no live-model restore. UI Load
  copies a catalog row into form fields only.
- Will not add: a second job queue, auto-apply, overlay-plot changes, a new
  WebSocket type, or replacement of applied `parameter_history`.

## Neighbourhood
- Opened modules: PE worker finish in `handle_estimate_parameters_ml` /
  `_run_pe_worker`; timeout path in `kalman_ml.estimate` (best θ, same unpack
  as η-plateau); `runtime.controller_config`; Identification page under Stored
  Datasets.
- Major refinement: none. Catalog is a bounded options list (cap 25), not a
  new persistence layer. Archive must call `save_config` only — not
  `_persist_runtime_config` / `restore_estimated_parameters`.
- `runAutoIdentification` waits for the job but does not populate room fields;
  operator uses Identification Results → Load → Apply Parameters.
- Time-limit finishes with a stored best θ are archiveable; cancelled and
  crashed jobs are not.

## Tracker
- Task: SWD-576
- Branch: cursor/swd-576-pe-fit-results-6f70

## Next
`/implement SWD-576` — Build to this shape

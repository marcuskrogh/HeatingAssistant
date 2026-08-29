# Adopt: Heating Assistant production tree

## Destination
Meet the structure catalog across the existing tree; executable behaviour unchanged.

## Tree
- Root: `heatingassistant/` (App runtime + engine + Ingress static) and `custom_components/heating_assistant/` (thin MQTT bridge)
- Out of scope: `heating_assistant/` packaging copy (regenerate with `scripts/sync-ha-app-package.sh`), `heatingassistant/app/static/vendor/`, generated output, lockfiles, `.agents/skills`, `sandbox/`, docs-only surfaces

## Inventory
| Area | Catalog rows | Concrete moves | Status |
|------|--------------|----------------|--------|
| engine/controller (`facade.py`) | Small type, SRP, Divergent Change; nested source dispatch in `f` | Split SDE / EKF / linearised / MPC files; extract `f`/`dfdu` helpers | done |
| app `HeatingRuntime` | Small type, SRP, Divergent Change; nested ticker / `hass_states` | Extract ticker, NMPC worker, HA state publisher, wiring, history sampler | done |
| engine `ControlEngine` | Small type, SRP; mixed build / live / preview | Extract construction and preview helpers | done |
| estimation + `sysid_services` | Small type, Divergent Change; nested NLP in `KalmanMLEstimator.estimate` | Lift nested MSE/L-BFGS helpers; keep PE HTTP and estimator entry points | frontier |
| Ingress panel JS | Small type, one level | Split remaining page-detail gods; keep HA classic-script IIFE | open |
| Remaining engine / MQTT / thin bridge | Re-scan after prior areas | Nested leftover or documented exception (heat-source polymorphism) | open |
| `heatingassistant/fusion/` | — | None — small averaging port | exception |

## Route
| Order | Area | Task | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | engine/controller | Split facade into SDE, EKF, linearised, MPC | — | done | SWD-441 |
| 2 | app runtime | Split HeatingRuntime collaborators | SWD-441 | done | SWD-442 |
| 3 | engine control_loop | Split ControlEngine build / live / preview | SWD-442 | done | SWD-443 |
| 4 | estimation + PE HTTP | Split estimation, diagnostics, sysid_services | SWD-443 | In Progress | SWD-444 |
| 5 | Ingress panel | Split remaining panel god modules | SWD-444 | To Do | SWD-445 |
| 6 | leftover | Remaining engine, MQTT, thin-bridge rows | SWD-445 | To Do | SWD-446 |

## Behaviour map
| Requirement | Current behaviour | Test path | Status |
|-------------|-------------------|-----------|--------|
| `KalmanMLEstimator` is the PE entry point | re-exported from `parameter_estimator` and `estimation` | `tests/test_swd444_estimation_seams.py` `test_kalman_ml_estimator_reexported_from_compat_module`; `tests/test_parameter_estimator.py` | locked |
| Estimator public methods stay on the class | estimate, estimate_wall_initial_only, log-likelihood slices | `tests/test_swd444_estimation_seams.py` `test_kalman_ml_estimator_public_methods_exist`; `tests/test_estimation_internals.py`; `tests/test_estimator_2r2c.py` | locked |
| App PE HTTP handlers stay on `sysid_services` | create/delete dataset, estimate, store, simulate, coverage | `tests/test_swd444_estimation_seams.py` `test_sysid_services_public_handlers_exist`; `tests/test_swd289_sysid_services.py` | locked |
| Runtime `apply_service` dispatches PE names to those handlers | `estimate_parameters_ml` / dataset / store / open-loop | `tests/test_swd444_estimation_seams.py` `test_runtime_maps_pe_services_to_sysid_handlers` | locked |
| Diagnostics public API unchanged | fit metrics, residuals, validate, warnings, open-loop predict | `tests/test_swd444_estimation_seams.py` `test_model_diagnostics_public_api_exists`; `tests/test_model_diagnostics.py` | locked |
| Parameter lifecycle persist/restore | store / apply / restore estimated params | `tests/test_swd444_estimation_seams.py` `test_parameter_lifecycle_public_api_exists`; `tests/test_persist_estimated_params.py` | locked |

## Preserve behaviour
- Required — CONCEPT_STRUCTURE Lock before restructure + Proof is the gate
- Lock-suite commands: `python3 -m pytest tests/test_swd444_estimation_seams.py tests/test_swd289_sysid_services.py tests/test_sysid_param_overrides.py tests/test_sysid_initial_state.py tests/test_sysid_cache_consistency.py tests/test_estimation_internals.py tests/test_parameter_estimator.py tests/test_model_diagnostics.py tests/test_persist_estimated_params.py tests/test_estimator_2r2c.py tests/test_initial_state_estimator.py tests/test_no_online_gain_estimation.py -m "not slow and not ondemand" -q`
- Characterize result: green — 68 passed, 5 skipped, 6 deselected (2026-08-29, current estimation/sysid tree); after NLP extract 69 passed, 5 skipped, 6 deselected (same commands + `tests/test_swd444_estimation_modules.py`)
- Verification: same tests, same requirements after every code-editing step; `test.mode=dedicated`

## Frontier
- Area: estimation + PE HTTP (`kalman_ml.py`, `sysid_services.py`, diagnostics, lifecycle)
- Packages: lift nested MSE/L-BFGS helpers out of `KalmanMLEstimator.estimate`; keep public PE/sysid HTTP and estimator entry points. Remaining `sysid_services` / diagnostics module splits if still over the bar after that extract.

## Workflow
- Template: structure-safe
- Unit chain: characterize → implement → test → harden → review-fix → ship
- Route: inventory → characterize → unit chain remainder per area in Order until Done
- Verify: non-regression; test.mode=dedicated; lock suite from characterize

## Tracker
- Story: SWD-440
- Task: SWD-444
- Branch: `cursor/swd-444-adopt-estimation-1253`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/641

## Next
`/implement SWD-444` — remaining `sysid_services` / diagnostics splits on PR #641; `/adopt` continues after ship

# Implementation plan: Publish identification fit KPIs in App

## Summary
- Overview **MODEL FIT** and System Identification index cards show empty goodness-of-fit / ID KPIs (`—` / **NO DATA**) even when API + MQTT are healthy and other system KPIs populate.
- Root cause: App `hass_states()` never synthesizes `sensor.heating_assistant_<slug>_model_fit_quality` or `…_parameter_confidence`. Those HA Core diagnostic sensors were deleted in SWD-262 and only open-loop / sysid-simulation sensors were restored in SWD-289.
- Engine `model_diagnostics.py` currently exposes only `compute_open_loop_predictions`; fit helpers needed for R² / confidence were left in the deleted HA module.

## Scope / Decisions / Constraints

**In**
- Port hass-free fit helpers into `heatingassistant/engine/model_diagnostics.py` (from pre-SWD-262 `custom_components/.../model_diagnostics.py`): at least `compute_model_fit_metrics`, `validate_parameters`, `build_identification_warnings`, and supporting dataclasses/constants used by those APIs.
- Publish per-room synthetic sensors in `HeatingRuntime.hass_states()` with the same contracts the panel already expects:
  - `*_model_fit_quality` — state = R²; attrs include `rmse`, `mae`, `bias`, `n_samples`, etc.
  - `*_parameter_confidence` — state = confidence score 0–100; attrs include `is_estimated`, `estimated_at`, `card_warnings`, thermal validation fields.
- Compute closed-loop fit from App `history_buffer` aligned `y` / `y_pred` (same rule as old `_closed_loop_fit_for_room`: need ≥2 aligned samples).
- Read estimation provenance from `estimated_params_snapshot(options)` (already used elsewhere in App).
- Regression: `hass_states()` exposes both entity IDs with non-empty fit when history has aligned predictions; insufficient history → `unknown` / insufficient-data attrs (panel keeps `—` / NO DATA).
- Version bump to **2.0.31** + App package sync.

**Out**
- Other diagnostic sensors not shown on Overview / sysid index (`*_prediction_error`, `*_kalman_innovation`, `*_residual_acf`, house `estimated_parameters_status`) unless implement discovers the panel hard-depends on them for these surfaces.
- Restoring fat HA Core diagnostic entities / Recorder rebuild path.
- `analyze_model_fit` / `validate_parameters` / `controller_performance_report` service handlers (still deferred from SWD-289 unless shared helpers are required).
- Redesigning KPI thresholds, badge copy, or identification UI layout.
- Requiring a fresh Automatic Identification run — closed-loop R² should come from live history once sensors exist.

**Decisions**
- Port helper logic from `git show ef816f8^:custom_components/heating_assistant/model_diagnostics.py` (+ `_closed_loop_fit_for_room` / `_room_estimation_provenance` patterns from `sensor/base.py`) rather than inventing new attribute shapes — panel `sysid-index.js` / `kpi-engine.js` stay unchanged.
- Keep skipping `*_model_fit_quality` from plot-history sampling (runtime already does this); do not flood plot history with R².
- Prefer computing fit/confidence on `hass_states()` read (cheap over buffer) matching old CoordinatorEntity property behaviour; cache only if profiling shows a problem.

**Constraints**
- Thin MQTT bridge remains I/O-only; KPI compute stays in the App.
- Shared version lock: App `config.yaml` ≡ integration `manifest.json` ≡ package metadata.
- Cloud delivery branch uses `cursor/…-3a87`; maps to workspace pattern `swd-299-identification-kpis`.

## Classification
- Class: bug
- Confidence: high
- Why: Expected KPI population is known from panel contracts and pre-SWD-262 sensors; App simply never publishes the entities those pages read.

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: Contained App sensor publication + port of existing fit helpers; blast radius limited to synthetic diagnostics and panel consumers already wired.

## Inputs
- Research: none
- Model: none
- Prior: SWD-289 restored sysid compute + `*_open_loop_rmse` / `*_sysid_simulation` but deferred broader diagnostics; SWD-262 deleted fat HA `ModelFitQualitySensor` / `ParameterConfidenceSensor`
- Recoverable reference:
  - `git show ef816f8^:custom_components/heating_assistant/sensor/diagnostics.py`
  - `git show ef816f8^:custom_components/heating_assistant/model_diagnostics.py`
  - `git show ef816f8^:custom_components/heating_assistant/sensor/base.py` (`_closed_loop_fit_for_room`, `_room_estimation_provenance`)
- User report: Overview MODEL FIT `—`; System Identification Living Room **NO DATA** with R²/RMSE/Estimated dashes; API connected · MQTT ok

## Acceptance criteria
1. [x] With ≥2 aligned `y`/`y_pred` samples for a room in App ID history, `hass_states()[sensor.heating_assistant_<slug>_model_fit_quality]` has a numeric R² state and `rmse` attribute.
2. [x] Overview **MODEL FIT** gauge shows GOOD / ACCEPTABLE / POOR (not `—`) when any room has valid fit.
3. [x] System Identification index card shows numeric **R²** and **RMSE**, fit badge (not **NO DATA**), and **Estimated** Yes/No from `parameter_confidence.is_estimated`.
4. [x] With insufficient aligned history, sensors stay `unknown` / insufficient-data and the panel correctly keeps empty placeholders (no crash).
5. [x] Regression tests cover sensor publication + fit helper port; version **2.0.31** + App package synced.

## Work packages
1. **Publish identification fit KPIs** — port fit/confidence helpers into engine; synthesize `*_model_fit_quality` + `*_parameter_confidence` in `hass_states()`; App regressions; version **2.0.31**.

## Open items
- Whether room-detail MODEL FIT (same entity) is enough coverage without also publishing `*_prediction_error` — treat as same entity path; no extra scope unless UI still blank after fix.
- Exact subset of `model_diagnostics` symbols to port — only what fit/confidence sensors need; leave controller-performance / innovation report helpers deferred unless tests require them.

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-299](https://marcusknielsen.atlassian.net/browse/SWD-299)
- Sub-tasks: — (single package)
- Relates: [SWD-289](https://marcusknielsen.atlassian.net/browse/SWD-289), [SWD-262](https://marcusknielsen.atlassian.net/browse/SWD-262)
- Branch: `cursor/swd-299-identification-kpis-3a87` (maps to `swd-299-identification-kpis`)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/593
- Classification: bug
- Workflow: fix-fast

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/593
- Version: **2.0.31**
- review-fix: CLEAN

## Next
Done — rebuild App on HAOS to v2.0.31; confirm Overview MODEL FIT and System Identification R²/RMSE/Estimated populate when ID history has aligned predictions.

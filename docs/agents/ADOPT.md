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
| estimation + `sysid_services` | Small type, Divergent Change; nested NLP in `KalmanMLEstimator.estimate` | Lift nested MSE/L-BFGS helpers; keep PE HTTP and estimator entry points | done |
| Ingress panel JS | Small type, one level | Split remaining page-detail gods; keep HA classic-script IIFE | frontier |
| Remaining engine / MQTT / thin bridge | Re-scan after prior areas | Nested leftover or documented exception (heat-source polymorphism) | open |
| `heatingassistant/fusion/` | — | None — small averaging port | exception |

## Route
| Order | Area | Task | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | engine/controller | Split facade into SDE, EKF, linearised, MPC | — | done | SWD-441 |
| 2 | app runtime | Split HeatingRuntime collaborators | SWD-441 | done | SWD-442 |
| 3 | engine control_loop | Split ControlEngine build / live / preview | SWD-442 | done | SWD-443 |
| 4 | estimation + PE HTTP | Split estimation, diagnostics, sysid_services | SWD-443 | done | SWD-444 |
| 5 | Ingress panel | Split remaining panel god modules | SWD-444 | In Progress | SWD-445 |
| 6 | leftover | Remaining engine, MQTT, thin-bridge rows | SWD-445 | To Do | SWD-446 |

## Behaviour map
| Requirement | Current behaviour | Test path | Status |
|-------------|-------------------|-----------|--------|
| Dashboard boot is a classic-script IIFE | wrapped in `(() => {`; no `import.meta.url`; defines `ha-industrial-panel` | `tests/test_swd445_panel_seams.py` `test_industrial_dashboard_is_classic_script_iife`; `tests/test_app_ingress_panel.py` | locked |
| Dashboard dynamically imports page modules with `?v=` | `room-detail`, PE, schedules, overview, tuning, system-status, config | `tests/test_swd445_panel_seams.py` `test_industrial_dashboard_dynamically_imports_page_modules` | locked |
| Page-detail public renders stay on named modules | `renderRoomDetail`, `renderIdentificationDetail`, `renderScheduleDetail`; thin page wrappers import them | `tests/test_swd445_panel_seams.py` `test_page_detail_entry_exports_exist` | locked |
| Ingress index cache-busts the dashboard entry | `industrial-dashboard.js?v=` in `index.html` | `tests/test_swd445_panel_seams.py` `test_index_html_cache_busts_dashboard_entry`; `tests/test_swd434_disturbance_history_lines.py` | locked |
| Room-detail live chart helpers stay callable by name | `updateChartsFromState` extends live history before `mpcForecastStamp` early return | `tests/test_swd445_panel_seams.py` `test_room_detail_keeps_live_chart_update_helpers`; `tests/test_swd414_nmpc_trajectory.py` | locked |
| PE detail keeps window/aux/tw0 identifiers | `getPeInputs`, `t_wall_locked`, `applySimulatedTw0` in `sysid-detail.js` | `tests/test_swd344_pe_sim_aux_tw0.py` `test_pe_guides_are_plain_dataset_requirements` | locked |

## Preserve behaviour
- Required — CONCEPT_STRUCTURE Lock before restructure + Proof is the gate
- Lock-suite commands: `python3 -m pytest tests/test_swd445_panel_seams.py tests/test_app_ingress_panel.py tests/test_panel_setup.py tests/test_swd414_nmpc_trajectory.py tests/test_swd434_disturbance_history_lines.py tests/test_swd430_timer_loading.py tests/test_swd344_pe_sim_aux_tw0.py tests/test_pe_coverage.py tests/test_swd426_nmpc_p_grid.py tests/test_swd400_nmpc_countdown.py -m "not slow and not ondemand" -q`
- Characterize result: green — 62 passed, 1 skipped (2026-08-29, current Ingress panel tree); after room-detail history extract 63 passed, 1 skipped
- Verification: same tests, same requirements after every code-editing step; `test.mode=dedicated`

## Frontier
- Area: Ingress panel JS (page-detail gods; HA classic-script IIFE exception)
- Packages: split `sysid-detail.js`, `schedules-detail.js`, `room-detail.js` along neighbour module seams; keep `?v=` cache-bust and IIFE boot shell (`industrial-dashboard.js` exception).

## Workflow
- Template: structure-safe
- Unit chain: characterize → implement → test → harden → review-fix → ship
- Route: inventory → characterize → unit chain remainder per area in Order until Done
- Verify: non-regression; test.mode=dedicated; lock suite from characterize

## Tracker
- Story: SWD-440
- Task: SWD-445
- Branch: `cursor/swd-445-adopt-panel-1253`
- PR: (draft after characterize)

## Next
`/implement SWD-445` — split page-detail gods on the SWD-445 delivery head

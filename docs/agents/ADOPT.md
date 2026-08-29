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
| app `HeatingRuntime` | Small type, SRP, Divergent Change; nested ticker / `hass_states` | Extract ticker, NMPC worker, HA state publisher, wiring, history sampler | frontier |
| engine `ControlEngine` | Small type, SRP; mixed build / live / preview | Extract construction and preview helpers | open |
| estimation + `sysid_services` | Small type, Divergent Change | Split remaining god modules along existing seams | open |
| Ingress panel JS | Small type, one level | Split remaining page-detail gods; keep HA classic-script IIFE | open |
| Remaining engine / MQTT / thin bridge | Re-scan after prior areas | Nested leftover or documented exception (heat-source polymorphism) | open |
| `heatingassistant/fusion/` | — | None — small averaging port | exception |

## Route
| Order | Area | Task | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | engine/controller | Split facade into SDE, EKF, linearised, MPC | — | done | SWD-441 |
| 2 | app runtime | Split HeatingRuntime collaborators | SWD-441 | In Progress | SWD-442 |
| 3 | engine control_loop | Split ControlEngine build / live / preview | SWD-442 | To Do | SWD-443 |
| 4 | estimation + PE HTTP | Split estimation, diagnostics, sysid_services | SWD-443 | To Do | SWD-444 |
| 5 | Ingress panel | Split remaining panel god modules | SWD-444 | To Do | SWD-445 |
| 6 | leftover | Remaining engine, MQTT, thin-bridge rows | SWD-445 | To Do | SWD-446 |

## Behaviour map
| Requirement | Current behaviour | Test path | Status |
|-------------|-------------------|-----------|--------|
| Ingress HTTP composition root stays `HeatingRuntime` | `__main__` calls status/config/history/forecasts/update_* | `tests/test_swd442_runtime_seams.py` `test_http_composition_root_methods_exist`; `tests/test_app_http.py` | locked |
| Package export is the runtime class | `from heatingassistant.app import HeatingRuntime` | `tests/test_swd442_runtime_seams.py` `test_app_package_exports_heating_runtime` | locked |
| Panel `hass_states` includes controller, summary, MPC, room climate | entity ids + `nmpc_computing` attrs | `tests/test_swd442_runtime_seams.py` `test_hass_states_exposes_panel_entities`; `tests/test_swd300_system_health.py` | locked |
| Start survives MQTT publish failure | Ingress comes up; metadata retries on connect | `tests/test_runtime_mqtt_start.py` | locked |
| Entity wiring derives tags from HA entity IDs | temp_tags / bindings from temp_sensors | `tests/test_app_entity_wiring.py` | locked |
| Wall-clock ticker records history and runs control when tags are quiet | background thread; skip if control just ran | `tests/test_swd276_wall_clock_ticker.py` | locked |
| NMPC/P share Start epoch; countdown does not restamp on NLP finish | `_last_nmpc_ts` origin; worker thread | `tests/test_swd418_nmpc_timer_drift.py`, `tests/test_swd400_nmpc_countdown.py`, `tests/test_swd426_nmpc_p_grid.py` | locked |
| NMPC worker applies plan and P command without an extra EKF tick | `_schedule_nmpc_worker` / `_nmpc_worker_thread` | `tests/test_swd395_nmpc_p.py` | locked |
| Window override holds heaters off | state machine + timers | `tests/test_window_override.py` | locked |
| Plot/ID history cadence and persistence | interval gate + JSONL restore | `tests/test_swd318_id_sample_plot_cadence.py`, `tests/test_swd281_history_persistence.py` | locked |
| Climate actuation write path | heater_entity climate payloads | `tests/test_swd280_climate_actuation.py` | locked |
| Tag quality catalog overlay | inbound GOOD vs stale BAD | `tests/test_swd385_tag_quality.py` | locked |
| Config/options persist and reconfigure | data dir + options path | `tests/test_app_persistence.py`, `tests/test_app_options_path.py`, `tests/test_runtime_reconfig.py` | locked |

## Preserve behaviour
- Required — CONCEPT_STRUCTURE Lock before restructure + Proof is the gate
- Lock-suite commands: `python3 -m pytest tests/test_swd442_runtime_seams.py tests/test_swd442_runtime_modules.py tests/test_runtime_mqtt_start.py tests/test_runtime_reconfig.py tests/test_app_http.py tests/test_app_entity_wiring.py tests/test_app_persistence.py tests/test_app_options_path.py tests/test_swd276_wall_clock_ticker.py tests/test_swd418_nmpc_timer_drift.py tests/test_swd426_nmpc_p_grid.py tests/test_swd400_nmpc_countdown.py tests/test_window_override.py tests/test_swd318_id_sample_plot_cadence.py tests/test_swd281_history_persistence.py tests/test_swd280_climate_actuation.py tests/test_swd385_tag_quality.py tests/test_swd300_system_health.py tests/test_swd395_nmpc_p.py -m "not slow and not ondemand" -q`
- Characterize result: green — 131 passed, 1 skipped (2026-08-29, current `runtime.py`); after split 132 passed, 1 skipped (same commands + `tests/test_swd442_runtime_modules.py`)
- Verification: same tests, same requirements after every code-editing step; `test.mode=dedicated`

## Frontier
- Area: app runtime (`HeatingRuntime`)
- Packages: extract ticker, NMPC worker, HA state publisher, wiring, history sampler

## Workflow
- Template: structure-safe
- Unit chain: characterize → implement → test → harden → review-fix → ship
- Route: inventory → characterize → unit chain remainder per area in Order until Done
- Verify: non-regression; test.mode=dedicated; lock suite from characterize

## Tracker
- Story: SWD-440
- Task: SWD-442
- Branch: `cursor/swd-442-adopt-runtime-1253`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/639

## Next
`/review-fix SWD-442` then `/ship SWD-442`. `/adopt` continues with SWD-443 after this Task ships

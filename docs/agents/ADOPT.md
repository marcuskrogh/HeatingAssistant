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
| Ingress panel JS | Small type, one level | Split remaining page-detail gods; keep HA classic-script IIFE | done |
| Remaining engine / MQTT / thin bridge | Small type, SRP; `heat_sources.py` packs eight types; thin `__init__.py` owns `_BridgeManager` | Package heat-source types with re-exports; extract `_BridgeManager`; document remaining exceptions | frontier |
| `heatingassistant/fusion/` | — | None — small averaging port | exception |

## Route
| Order | Area | Task | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | engine/controller | Split facade into SDE, EKF, linearised, MPC | — | done | SWD-441 |
| 2 | app runtime | Split HeatingRuntime collaborators | SWD-441 | done | SWD-442 |
| 3 | engine control_loop | Split ControlEngine build / live / preview | SWD-442 | done | SWD-443 |
| 4 | estimation + PE HTTP | Split estimation, diagnostics, sysid_services | SWD-443 | done | SWD-444 |
| 5 | Ingress panel | Split remaining panel god modules | SWD-444 | done | SWD-445 |
| 6 | leftover | Remaining engine, MQTT, thin-bridge rows | SWD-445 | In Progress | SWD-446 |

## Behaviour map
| Requirement | Current behaviour | Test path | Status |
|-------------|-------------------|-----------|--------|
| Heat-source types stay importable from `heat_sources` | ABC plus eight concrete types; `_soft_ceiling` / `_cop_at_temp` / `_SOFT_CEIL_K` stay on that import path | `tests/test_swd446_leftover_seams.py`; `tests/test_heat_sources.py` | locked |
| Schedule build and resolve stay public | `build_schedule([])` yields empty periods; `resolve_effective_control_params` / `next_transition` callable | `tests/test_swd446_leftover_seams.py`; `tests/test_schedule.py` | locked |
| MQTT bus factory and supervisor stay public | `create_mqtt_bus` and `apply_supervisor_mqtt_discovery` callable | `tests/test_swd446_leftover_seams.py`; `tests/test_swd270_mqtt_discovery.py` | locked |
| Thin integration still exposes `_BridgeManager` | `_BridgeManager`, setup/unload, `climate_attributes_for_publish`, `_truthy` on `custom_components/heating_assistant` | `tests/test_swd446_leftover_seams.py`; `tests/test_swd280_climate_actuation.py` | locked |
| Dashboard boot is a classic-script IIFE | wrapped in `(() => {`; defines `ha-industrial-panel` | `tests/test_swd445_panel_seams.py` | locked |

## Preserve behaviour
- Required — CONCEPT_STRUCTURE Lock before restructure + Proof is the gate
- Lock-suite commands: `python3 -m pytest tests/test_swd446_leftover_seams.py tests/test_swd446_leftover_modules.py tests/test_heat_sources.py tests/test_schedule.py tests/test_swd270_mqtt_discovery.py tests/test_swd273_mqtt_discovery_retry.py tests/test_swd385_tag_quality.py tests/test_swd280_climate_actuation.py -m "not slow and not ondemand" -q`
- Characterize result: green — 250 passed (2026-08-30, leftover seams on current tree)
- After splits: 265 passed (heat-source package re-exports + `_BridgeManager` extract; includes extra thin-bridge tests)
- Verification: same tests, same requirements after every code-editing step; `test.mode=dedicated`

## Frontier
- Area: leftover engine / MQTT / thin bridge
- Packages: split `heat_sources.py` into a re-exporting package; extract `_BridgeManager` from thin `__init__.py`. Document exceptions: heat-source ABC polymorphism; MQTT already split (`topics` / `supervisor` / `paho_bus` / `bridge`); `const.py` named-constants module; remaining `render*` closures and `KalmanMLEstimator.estimate` public entry points.

## Workflow
- Template: structure-safe
- Unit chain: characterize → implement → test → harden → review-fix → ship
- Route: inventory → characterize → unit chain remainder per area in Order until Done
- Verify: non-regression; test.mode=dedicated; lock suite from characterize

## Tracker
- Story: SWD-440
- Task: SWD-446
- Branch: `cursor/swd-446-adopt-leftover-1253`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/643

## Next
`/test SWD-446` — dedicated lock-suite pass on PR #643; then harden / review-fix / ship

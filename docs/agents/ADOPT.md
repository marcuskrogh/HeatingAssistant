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
| engine `ControlEngine` | Small type, SRP; mixed build / live / preview | Extract construction and preview helpers | frontier |
| estimation + `sysid_services` | Small type, Divergent Change | Split remaining god modules along existing seams | open |
| Ingress panel JS | Small type, one level | Split remaining page-detail gods; keep HA classic-script IIFE | open |
| Remaining engine / MQTT / thin bridge | Re-scan after prior areas | Nested leftover or documented exception (heat-source polymorphism) | open |
| `heatingassistant/fusion/` | — | None — small averaging port | exception |

## Route
| Order | Area | Task | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | engine/controller | Split facade into SDE, EKF, linearised, MPC | — | done | SWD-441 |
| 2 | app runtime | Split HeatingRuntime collaborators | SWD-441 | done | SWD-442 |
| 3 | engine control_loop | Split ControlEngine build / live / preview | SWD-442 | In Progress | SWD-443 |
| 4 | estimation + PE HTTP | Split estimation, diagnostics, sysid_services | SWD-443 | To Do | SWD-444 |
| 5 | Ingress panel | Split remaining panel god modules | SWD-444 | To Do | SWD-445 |
| 6 | leftover | Remaining engine, MQTT, thin-bridge rows | SWD-445 | To Do | SWD-446 |

## Behaviour map
| Requirement | Current behaviour | Test path | Status |
|-------------|-------------------|-----------|--------|
| Runtime imports `ControlEngine` from `control_loop` | `from heatingassistant.engine.control_loop import ControlEngine` | `tests/test_swd443_control_engine_seams.py` `test_runtime_imports_control_engine_from_control_loop` | locked |
| Public engine methods stay on `ControlEngine` | update_config, step, compute_actions, forecast, NMPC apply, preview, room_power_meta, build helpers | `tests/test_swd443_control_engine_seams.py` `test_control_engine_public_methods_exist` | locked |
| Module helpers stay importable from `control_loop` | house/heat-source builders, snapshot, P-gating reject, preview key sets | `tests/test_swd443_control_engine_seams.py` `test_module_helpers_remain_on_control_loop`; `tests/test_swd282_solar_exposure_aperture.py` | locked |
| House model is built from rooms config | `ControlEngine({"rooms": [...]})` → `HouseModel`; proportional when no sources | `tests/test_swd443_control_engine_seams.py` `test_control_engine_builds_house_from_rooms_config`; `tests/test_engine_averaging_control.py` | locked |
| Proportional fallback without MPC | room `output_tags` get clamped error/3; `step` delegates | `tests/test_swd443_control_engine_seams.py` `test_compute_actions_proportional_fallback_without_controller`; `tests/test_engine_averaging_control.py` | locked |
| Preview without a controller is unavailable | no heat sources → `{"error": "controller_unavailable"}`; `_preview_matches_live` is false | `tests/test_swd443_control_engine_seams.py`; `tests/test_swd285_tuning_preview.py` | locked |
| Draft preview does not mutate live forecast cache | one-off solve; live snapshot unchanged | `tests/test_swd285_tuning_preview.py`; `tests/test_swd431_preview_room_parity.py` | locked |
| Negative P-gating knobs rejected at engine construct | `ValueError` on negative `p_deadband` / `u_ref_gate` | `tests/test_swd443_control_engine_seams.py` `test_reject_negative_p_gating_knobs_raises`; `tests/test_swd437_p_deadband.py` | locked |
| NMPC due / apply / P command | live loop on `ControlEngine` / `HeatingMPCController` | `tests/test_controller.py`; `tests/test_nmpc_input_bias.py`; `tests/test_swd395_nmpc_p.py`; `tests/integration/test_controller_factory.py` | locked |

## Preserve behaviour
- Required — CONCEPT_STRUCTURE Lock before restructure + Proof is the gate
- Lock-suite commands: `python3 -m pytest tests/test_swd443_control_engine_seams.py tests/test_engine_averaging_control.py tests/test_swd285_tuning_preview.py tests/test_swd431_preview_room_parity.py tests/test_swd437_p_deadband.py tests/test_controller.py tests/test_nmpc_input_bias.py tests/test_swd395_nmpc_p.py tests/integration/test_controller_factory.py -m "not slow and not ondemand" -q`
- Characterize result: green — 221 passed, 5 skipped (2026-08-29, current `control_loop.py`); after split 223 passed, 5 skipped (same commands + `tests/test_swd443_control_engine_modules.py`)
- Verification: same tests, same requirements after every code-editing step; `test.mode=dedicated`

## Frontier
- Area: engine `ControlEngine` (`control_loop.py`)
- Packages: extract `_try_build_controller` / `_build_controller_from_config` and preview (`_preview_matches_live`, `preview_tuning_forecast`) into mixins; keep live `compute_actions` / NMPC apply on `ControlEngine`

## Workflow
- Template: structure-safe
- Unit chain: characterize → implement → test → harden → review-fix → ship
- Route: inventory → characterize → unit chain remainder per area in Order until Done
- Verify: non-regression; test.mode=dedicated; lock suite from characterize

## Tracker
- Story: SWD-440
- Task: SWD-443
- Branch: `cursor/swd-443-adopt-control-engine-1253`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/640

## Next
`/ship SWD-443` — merge PR #640 after CI; `/adopt` continues with SWD-444

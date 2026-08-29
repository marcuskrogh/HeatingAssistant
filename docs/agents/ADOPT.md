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
| app `HeatingRuntime` | Small type, SRP, Divergent Change; nested ticker / `hass_states` | Extract ticker, NMPC worker, HA state publisher, wiring, history sampler | open |
| engine `ControlEngine` | Small type, SRP; mixed build / live / preview | Extract construction and preview helpers | open |
| estimation + `sysid_services` | Small type, Divergent Change | Split remaining god modules along existing seams | open |
| Ingress panel JS | Small type, one level | Split remaining page-detail gods; keep HA classic-script IIFE | open |
| Remaining engine / MQTT / thin bridge | Re-scan after prior areas | Nested leftover or documented exception (heat-source polymorphism) | open |
| `heatingassistant/fusion/` | — | None — small averaging port | exception |

## Route
| Order | Area | Task | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | engine/controller | Split facade into SDE, EKF, linearised, MPC | — | done | SWD-441 |
| 2 | app runtime | Split HeatingRuntime collaborators | SWD-441 | To Do | SWD-442 |
| 3 | engine control_loop | Split ControlEngine build / live / preview | SWD-442 | To Do | SWD-443 |
| 4 | estimation + PE HTTP | Split estimation, diagnostics, sysid_services | SWD-443 | To Do | SWD-444 |
| 5 | Ingress panel | Split remaining panel god modules | SWD-444 | To Do | SWD-445 |
| 6 | leftover | Remaining engine, MQTT, thin-bridge rows | SWD-445 | To Do | SWD-446 |

## Behaviour map
| Requirement | Current behaviour | Test path | Status |
|-------------|-------------------|-----------|--------|
| HouseThermalSDE is a CD-SDE with 2R2C dimensions | nx=3n, nu=sources, nd=1+2n | `tests/test_controller.py` `TestHouseThermalSDE` | locked |
| Drift: heat raises air node; no-heat + cold outdoor cools | sign of `f[:n]` | `tests/test_controller.py` `test_drift_heating_increases_temperature` / `test_drift_no_heat_cold_outside` | locked |
| Analytic Jacobians match finite differences | `dfdx` / `dfdu` | `tests/test_controller.py` Jacobian tests | locked |
| Cooling-capable sources use signed u | heat-pump / smooth power | `tests/test_controller.py` + `tests/test_emitter_filter.py` | locked |
| Infiltration overlay on air node | Sherman–Grimsrud when wind set | `tests/test_infiltration.py` | locked |
| HeatingMPCController.compute runs EKF + P (no inline NLP when plan installed) | `_seed_path` then `compute` | `tests/test_controller.py` `TestHeatingMPCController` | locked |
| NMPC accept / `u_ref` step / forecast path | apply plan, remaining-U* resim | `tests/test_nmpc_input_bias.py`, `tests/test_swd395_nmpc_p.py`, `tests/test_swd417_nmpc_forecast_path.py`, `tests/test_swd421_room_plot_grid.py` | locked |
| GHI None uses cloud/clear not ghi_now | per-step fallback | `tests/test_swd432_ghi_none_analytical.py` | locked |
| Package re-exports HouseThermalSDE, HeatingMPCController, HeatingLinearisedMPC | `from heatingassistant.engine.controller import …` | import sites in tests above | locked |
| No online internal-gain estimation | gain estimation off | `tests/test_no_online_gain_estimation.py` | locked |

## Preserve behaviour
- Required — CONCEPT_STRUCTURE Lock before restructure + Proof is the gate
- Lock-suite commands: `python3 -m pytest tests/test_controller.py tests/test_nmpc_input_bias.py tests/test_swd395_nmpc_p.py tests/test_swd417_nmpc_forecast_path.py tests/test_swd421_room_plot_grid.py tests/test_swd432_ghi_none_analytical.py tests/test_emitter_filter.py tests/test_infiltration.py tests/test_no_online_gain_estimation.py tests/integration/test_controller_factory.py tests/test_swd418_nmpc_timer_drift.py -m "not slow and not ondemand" -q`
- Characterize result: green — 225 passed, 6 skipped (2026-08-29, current `facade.py`)
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
- Task: SWD-441 (this PR) then SWD-442
- Branch: `cursor/swd-441-adopt-controller-1253`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/638

## Next
`/adopt SWD-442` — characterize HeatingRuntime after this PR merges

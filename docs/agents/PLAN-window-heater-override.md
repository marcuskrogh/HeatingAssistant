# Implementation plan: Restore door/window heater override in App

## Summary
- Configured room door/window contact sensors (`window_sensors`) do not turn heaters off after `window_open_debounce` (default 60 s).
- Config/UI and MQTT `window_tags` bindings still exist; the App never runs the override state machine or clamps heat sources.
- Root cause: `coordinator/window.py` and call sites were deleted in SWD-262 and never ported to the App (same port-gap class as other thin-cutover bugs).

## Scope / Decisions / Constraints

**In**
- Port the per-room window state machine (`closed → pending_open → open → pending_closed → closed`) into the App (e.g. `heatingassistant/app/window_override.py`), driven by MQTT `window_tags` + App timers.
- On debounce expiry: clamp that room’s heat-source commands to 0 and publish actuators immediately (do **not** wait for the next MPC tick alone).
- Fold active override (`open` / `pending_closed`) into `disabled_sources` for scheduled control cycles; restore `window_open_q_inflation` via `set_room_process_noise_covariance_scales`.
- On settle expiry: clear override and resume from MPC shadow actions (`mpc_actions`) when available.
- Record `window_open` in ID/plot history when override is active; expose per-room `window_state` diagnostic if the panel/discovery already expects it.
- Revive/adapt `tests/test_window_override.py` off the SWD-262 skip for App-side behaviour.
- Version bump to **2.0.30** + App package sync.

**Out**
- Per-window air-exchange / plant-model change (ROADMAP deferred).
- Redesigning debounce defaults, UI copy, or room editor layout.
- Reintroducing fat HA `async_track_state_change_event` listeners in Core.

**Decisions**
- Port pre-SWD-262 behaviour from `git show ef816f8^:…/coordinator/window.py` rather than inventing a new contract.
- Window tag updates must **not** trigger a full `run_control_cycle()` (same thrash concern as SWD-280 heater feedback). Advance the state machine + timers; push actuators only via an override path mirroring `async_push_window_override`.
- Multi-sensor rooms: logical OR; rooms with no `window_sensors` / `window_tags` unchanged.
- Override active for `open` and `pending_closed` only (not `pending_open`).

**Constraints**
- Thin MQTT bridge stays I/O-only; timers and clamp logic live in the App.
- Shared version lock: App ≡ integration `manifest.json` ≡ package metadata.
- Cloud branch `cursor/swd-298-window-heater-override-1125` maps to workspace `swd-298-window-heater-override`.

## Classification
- Class: bug
- Confidence: high
- Why: Documented W1 behaviour is wrong after SWD-262; expected shutoff after debounce is known from ROADMAP/config/UI and the deleted coordinator.

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
- Rationale: Contained App port of a deleted module; blast radius is window override + `disabled_sources` / actuator publish; revive existing tests.

## Inputs
- Research: none
- Model: none
- Prior: SWD-262 deleted fat HA `coordinator/window.py`; ROADMAP Phase 3 W1 still unchecked; config keys + UI already present
- Recoverable reference: `git show ef816f8^:custom_components/heating_assistant/coordinator/window.py`

## Acceptance criteria
1. Room window sensor held `on` past `window_open_debounce` → all heat sources for that room command 0 W within debounce timing (event-driven push, not MPC-tick-only).
2. Opens shorter than debounce do not clamp heaters; closing before debounce cancels the pending open.
3. After all sensors `off` for `window_open_close_settle`, override clears and heaters may resume from shadow MPC actions.
4. Multi-sensor OR; rooms without window sensors unchanged.
5. Control cycles include override rooms in `disabled_sources` and apply `window_open_q_inflation`; history records `window_open` when active.
6. Regression tests cover debounce, settle, bounce/OR, and actuator push; version **2.0.30** + App package synced.

## Work packages
1. **Port window override into App** — state machine + timers + tag-driven transitions; exclude window tags from full control-cycle thrash; immediate actuator clamp/publish; wire `disabled_sources` + Q inflation + history; revive tests; version **2.0.30**.

## Open items
- Whether `window_state` diagnostic sensors must be republished on Ingress for this fix, or only internal state is required for shutoff — prefer publishing if discovery/panel already lists `_window_state`.

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-298](https://marcusknielsen.atlassian.net/browse/SWD-298)
- Sub-tasks: — (single package)
- Relates: [SWD-262](https://marcusknielsen.atlassian.net/browse/SWD-262)
- Branch: `cursor/swd-298-window-heater-override-1125` (maps to `swd-298-window-heater-override`)
- PR: _(set after draft open)_
- Classification: bug
- Workflow: fix-fast

## Next
`/implement SWD-298` — Build per this plan (same branch/PR)

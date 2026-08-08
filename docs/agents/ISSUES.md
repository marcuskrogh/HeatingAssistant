# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-268 | Task | [Iterate] Ingress 502 Bad Gateway after MQTT update — App not ready | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/554 |
| SWD-267 | Task | [Iterate] Ingress entity picker only shows App sensors — cannot wire HA room temperatures | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/551 |
| SWD-266 | Task | [Iterate] Ingress panel LOAD ERROR — bare module path fails dynamic import | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/550 |
| SWD-265 | Task | [Iterate] Ingress UI stuck on Loading App API | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/549 |
| SWD-264 | Task | [Iterate] Ingress UI 404 — static assets missing from pip install | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/548 |
| SWD-263 | Task | [Iterate] App rejects --options-path and crash-loops on start | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/547 |
| SWD-262 | Task | [Iterate] Finish HAOS App: Ingress parity, thin-only tree, port clash | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/546 |
| SWD-255 | Task | HAOS App + thin MQTT integration (compute isolation) | Done | — | docs/agents/PLAN-haos-app-mqtt.md | Done |
| SWD-258 | Sub-task | App packaging skeleton (sync + version lock) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-259 | Sub-task | Thin MQTT integration (entity↔tag bridge) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-260 | Sub-task | MQTT contract (topics, bindings, status) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-261 | Sub-task | Move compute into App (parity + persistence) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-257 | Sub-task | Port dashboard to App Ingress | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-256 | Sub-task | E2E hardening (load isolation + regressions) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-254 | Bug | [Bug] Remove dual-mode nonlinear MPC (revert to pre-SWD-240) — HA hang | Done | — | docs/agents/BUG-swd-254-remove-nonlinear-mpc.md | Done |
| SWD-238 | Story | Dual-mode MPC (linear / non-linear) | To Do | — | — | Dual-mode removed from main by SWD-254; optional Story closeout |
| SWD-248 | Task | [Bug] stop NMPC hang (executor, timeout, SciPy horizon cap) | Done | — | — | Done — superseded by SWD-254; PR #542 closed |

## Log

- 2026-08-08 — shipped SWD-268 via PR #554: non-blocking MQTT so Ingress binds without Mosquitto; v2.0.8.
- 2026-08-08 — SWD-268 review-fix CLEAN (focused); should-fix message-dispatch deadlock fixed forward.
- 2026-08-08 — SWD-268 In Review + PR #554: non-blocking MQTT so Ingress binds without Mosquitto; v2.0.8; Next `/review-fix SWD-268`.
- 2026-08-08 — `/iterate` SWD-268: Ingress 502 after v2.0.7 MQTT ship (PR #553); Paho connect raised before HTTP bind; v2.0.8; branch `cursor/swd-268-mqtt-ingress-502-72da`.
- 2026-08-08 — shipped KPI MQTT live sync via PR #553 (v2.0.7): App connects to Mosquitto + panel live state sync; KPIs/room temps populate. (Mirror previously labeled this SWD-268 before the Ingress-502 iterate claimed that key in Jira.)
- 2026-08-08 — shipped SWD-267 via PR #551: typed HA entity IDs + auto MQTT bindings; v2.0.6.
- 2026-08-08 — SWD-267 PR #551: free-text HA entity IDs + auto MQTT bindings; v2.0.6; In Review.
- 2026-08-08 — `/iterate` SWD-267: Ingress entity picker only shows App sensors; free-text entity IDs + auto MQTT bindings; v2.0.6; branch `cursor/swd-267-ha-entity-wiring-5d31`.
- 2026-08-08 — shipped SWD-266 via PR #550: Ingress BASE_PATH from script URL fixes dynamic import LOAD ERROR; v2.0.5.
- 2026-08-08 — `/iterate` SWD-266 from SWD-265: bare `BASE_PATH` breaks dynamic `import()` under Ingress; v2.0.5; PR #550.
- 2026-08-08 — shipped SWD-265 via PR #549: Ingress base href + relative asset/API paths fix loading stall; v2.0.4.
- 2026-08-07 — `/iterate` SWD-262 from SWD-255: port 8100 (PLC clash), Ingress industrial panel App shim, thin-only custom_components; branch `cursor/swd-262-finish-haos-app-01f0`.
- 2026-08-07 — shipped SWD-255 via PR #545 (merge `973a2c5`): HAOS App + thin MQTT integration; Sub-tasks SWD-258/259/260/261/257/256 Done.
- 2026-08-07 — `/ship` SWD-255 implement: App packaging, MQTT bridge, engine compute, Ingress shell, hardening tests (39 passed). Moving to review-fix.
- 2026-08-07 — `/define` SWD-255: HAOS App + thin MQTT integration plan approved; Sub-tasks SWD-258/259/260/261/257/256; branch `cursor/swd-255-haos-app-mqtt-01f0`; Next `/implement SWD-255`.
- 2026-08-06 — shipped SWD-254 via PR #544 (merge `5fa0ac6`): product tree restored to `30814c4`; dual-mode nonlinear MPC removed from main.
- 2026-08-06 — SWD-254 implement: product tree matches `30814c4` (excl. SWD-254 docs); pytest 1750 passed / 6 skipped. PR #544.
- 2026-08-06 — SWD-254: restore tree to `30814c4` (pre PR #539 / SWD-240) to remove dual-mode nonlinear MPC after HA Core hang. Branch `cursor/swd-254-remove-nonlinear-mpc-2550`. Dual-mode artifacts (SWD-239/240/246/247/253 docs) removed with the revert.

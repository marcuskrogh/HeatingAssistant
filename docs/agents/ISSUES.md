# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
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

- 2026-08-07 — `/ship` SWD-255 implement: App packaging, MQTT bridge, engine compute, Ingress shell, hardening tests (39 passed). Moving to review-fix.
- 2026-08-07 — `/define` SWD-255: HAOS App + thin MQTT integration plan approved; Sub-tasks SWD-258/259/260/261/257/256; branch `cursor/swd-255-haos-app-mqtt-01f0`; Next `/implement SWD-255`.
- 2026-08-06 — shipped SWD-254 via PR #544 (merge `5fa0ac6`): product tree restored to `30814c4`; dual-mode nonlinear MPC removed from main.
- 2026-08-06 — SWD-254 implement: product tree matches `30814c4` (excl. SWD-254 docs); pytest 1750 passed / 6 skipped. PR #544.
- 2026-08-06 — SWD-254: restore tree to `30814c4` (pre PR #539 / SWD-240) to remove dual-mode nonlinear MPC after HA Core hang. Branch `cursor/swd-254-remove-nonlinear-mpc-2550`. Dual-mode artifacts (SWD-239/240/246/247/253 docs) removed with the revert.

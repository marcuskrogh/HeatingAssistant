# Implementation plan: System Status page + health indicator + Parameter Estimation rename

## Summary
- Move connection/ops diagnostics off the floating status pill onto a dedicated **System Status** nav page.
- Introduce a backend **quality enum** (`healthy` / `warning` / `error`) from module healths; drive the top-bar indicator (dot + **HEALTHY** / **WARNING** / **ERROR**).
- Restructure Overview: compact system strip (**overall health** + **MPC load**), then **controller KPIs** (remaining gauges + countdown).
- Rename product term **System Identification** → **Parameter Estimation** everywhere user/API-visible (hard cut); keep internal `sysid` module/symbol names.

## Scope / Decisions / Constraints

**In**
- New `#system-status` page in PAGES nav labeled **System Status**.
- Remove floating `API connected - N entities · MQTT ok` pill.
- Top indicator = health only (green/yellow/red + HEALTHY/WARNING/ERROR). Running state = top-bar colour/animation + power button only (no LIVE/STOPPED text).
- Backend quality enum aggregating module healths: red if MQTT down or API unreachable; yellow for warnings (e.g. bad temp-sensor quality, MPC convergence issues); green when clear. Publish overall quality + per-module detail + top issue summary when possible.
- System Status page: top issue summary; MQTT health; API/uptime; entity catalog count; bindings/module health; MPC operational detail.
- Overview: **SYSTEM STATUS** = health + MPC load only; **CONTROLLER KPIs** = comfort, power, COP, energy, tracking error, model fit, countdown (and any other current gauges except health/load).
- Hard-cut rename of **system identification** → **parameter estimation** in UI copy, hashes/routes, HA entity IDs, services, MQTT/discovery names, and user-facing docs. No compatibility aliases.
- Tests + shared version bump to **2.0.32** + App package sync.

**Out**
- Renaming Python `sysid` modules/symbols/test filenames.
- Clicking the health indicator to navigate (nav item only).
- Browsable full entity catalog on System Status (count is enough for v1).
- Redesigning gauge visuals beyond section split/move.

**Decisions**
- Quality: `healthy` | `warning` | `error` from backend; UI maps to green / yellow / red and HEALTHY / WARNING / ERROR.
- Overview system strip fields: overall health + MPC load only.
- Parameter Estimation is the going-forward product term; hard cut on IDs.

**Constraints**
- Thin MQTT bridge stays I/O-only; health aggregation in App.
- Shared version lock: App `config.yaml` ≡ integration `manifest.json` ≡ package metadata.
- Cloud delivery branch uses `cursor/swd-300-system-status-c2e7`; maps to workspace pattern `swd-300-system-status`.

## Classification
- Class: feature
- Confidence: high
- Why: New product surface + Overview behaviour change + health model + breaking rename as one slice.

## Workflow
- Template: feature-heavy
- Parameters:
  - implement.mode: multiagent
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: multiagent
  - review.depth: full
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: Cross-cutting UI + backend health API + breaking entity/service rename → wide blast radius override.

## Inputs
- Research: none
- Model: none
- Prior: SWD-271 entity catalog; App `status()` / `hass_states()`; floating pill in `app-hass-shim.js`; Overview SYSTEM STATUS in `overview.js`

## Acceptance criteria
1. Floating status pill is gone on Ingress.
2. **System Status** nav page shows issue summary, MQTT, API/uptime, entity count, bindings/module health, MPC detail.
3. Top indicator shows HEALTHY/WARNING/ERROR with matching colours from backend quality; LIVE/STOPPED text is gone; run state still via top-bar style + power button.
4. Overview shows health + MPC load under system status; other former system gauges (incl. countdown) under controller KPIs.
5. No user-facing “System Identification” / `identification` product language remains where Parameter Estimation applies; old entity IDs/services hard-cut; `sysid` code names unchanged.
6. Tests cover quality aggregation + page/indicator wiring + rename fallout; version **2.0.32** and packages synced.

## Work packages
1. **Backend quality enum + status payload** — `healthy`/`warning`/`error`, module healths, uptime, issue summary, entity count.
2. **System Status page + health indicator + remove pill** — nav `#system-status`, indicator labels/colours, drop floating pill.
3. **Overview system strip + controller KPIs split** — health + MPC load vs remaining gauges + countdown.
4. **Hard-cut Parameter Estimation rename** — UI/routes/IDs/services/MQTT/docs; keep `sysid` modules.
5. **Tests + version bump + package sync** — regressions; version **2.0.32**.

## Open items
- Exact module list in the quality enum beyond MQTT, API, sensor-tag quality, MPC/control — finalize during implement from existing runtime signals.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-300
- Sub-tasks: SWD-304, SWD-302, SWD-303, SWD-301, SWD-305
- Branch: `cursor/swd-300-system-status-c2e7`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/595
- Classification: feature
- Workflow: feature-heavy
- Version: **2.0.32**

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/595

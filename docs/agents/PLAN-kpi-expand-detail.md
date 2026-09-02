# Implementation plan: Expandable KPI detail cards

## Summary
- Overview and room-view KPI gauges (and the countdown rings in those grids)
  stay visually the same when collapsed.
- Clicking a KPI moves it to the top of its KPI section and expands it: the
  live gauge or countdown (including computing overlays) sits on top; a
  slightly darker panel below holds a short description plus absolute or
  specific values.
- The trigger was MPC LOAD: the collapsed card is a percent of a 2 s
  budget derived from the last P-cycle duration, which does not show NMPC
  wall-clock time. The expanded card must show those times explicitly.

## Scope / Decisions / Constraints

**In**
- Overview **SYSTEM STATUS** gauges: OVERALL HEALTH, MPC LOAD.
- Overview **CONTROLLER KPIS** gauges: COMFORT, HEATING POWER, SYSTEM COP,
  DAILY ENERGY, TRACKING ERROR, MODEL FIT.
- Overview countdown rings in that same grid: NEXT CONTROL, NEXT NMPC.
- Room view KPI grid: TIME IN RANGE, POWER, ENERGY PRICE, SOLAR GAIN,
  HEAT LOSS, MODEL FIT, NEXT CONTROL, NEXT NMPC.
- One expanded card per section at a time. Click another card in the same
  section: the previous card returns to its original order; the new one
  expands at the top. Click the expanded card again: it collapses and
  original order is restored.
- Escape collapses the expanded card in the focused section.
- Expansion is session-only (not stored). Room view and Overview are
  independent.
- Collapsed chrome stays `.card.gauge` / `.card.countdown` (label, mono
  value, severity bar or ring). Expanded: full grid width (`grid-column: 1 / -1`),
  slightly larger type and bar/ring, same tokens.
- Detail panel uses `--bg-primary` (darker than `--bg-card`) with existing
  border, uppercase dim labels, mono values, `--text-secondary` body copy.
  No new accent palette.
- Publish `last_nmpc_duration_s` on `sensor.heating_assistant_mpc_performance`
  (wall-clock around `solve_nmpc_blocking`). Keep the existing native state
  as last P duration. Collapsed MPC LOAD formula stays as today unless
  implement proves it already uses the wrong clock; do not change the
  percent definition in this Task except by documenting it on the card.
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Tests, CalVer, changelog, App package sync.

**Out**
- System Status page (already lists last solve, intervals, timestamps).
- Identification / Parameter Estimation KPI grids.
- Climate card, room tiles, charts, Tuning preview.
- Redesigning collapsed gauges or countdown artwork.
- Changing NMPC/P periods, accept/reject, or the 2 s `MAX_SOLVE_TIME_S`
  constant used for the percent fill.
- Persisting which KPI is expanded across reloads.

**Decisions**
- Class is **feature**: new interaction, layout, copy, and extra NMPC
  duration attribute as one product slice.
- Countdown rings in the KPI grids expand the same way (they already carry
  the computing animation).
- Hidden gauges (`display: none`) are not clickable and cannot expand.
- Keyboard: the card is a toggle (`role="button"`, `aria-expanded`).
- Motion: short layout shift using existing `--transition` (about 200 ms);
  no bounce, no new illustration.
- Architect writes `ARCHITECTURE.md` for the host + catalog shape before
  the sandbox inspects look.

**Constraints**
- Product copy must not include tracker keys.
- Prefer existing `mpc_performance` / room entity attributes; add attributes
  only when the detail rows cannot be computed from live state.
- Dual-tree sync after implement.

### KPI detail catalog (copy + rows)

Collapsed labels stay as shipped. Detail titles may spell the name in
ordinary English.

| Card | Description (sense) | Absolute / specific rows |
|------|----------------------|---------------------------|
| OVERALL HEALTH | Combined house quality from MQTT, tags, and ID history. | Quality (`healthy` / `warning` / `error`); issue summary or “No active issues”; MQTT connected |
| MPC LOAD | Share of the 2 s load budget used by the last P-cycle duration. The percent is not NMPC wall-clock time. | Last P cycle (s, native `mpc_performance` state); last NMPC solve (`last_nmpc_duration_s`); NMPC computing; P computing; control interval `dt_s`; NMPC period `nmpc_period_s`; last NMPC result time |
| COMFORT | Share of active rooms whose temperature is inside the comfort band. | Percent; count in band / eligible; names of rooms out of band when known |
| HEATING POWER | Sum of measured heater power. | Live kW; gauge fill vs scale; rated or scale max when known |
| SYSTEM COP | Effective system COP when published. | COP; hide row set when the gauge is hidden |
| DAILY ENERGY | Heat delivered since local midnight. | kWh today; — until the midnight baseline is ready |
| TRACKING ERROR | Mean absolute temperature error vs setpoint across active rooms. | Mean °C; per-room errors when already on state |
| MODEL FIT (house) | Aggregate model fit label from room R². | Label; numeric score 0–1 |
| TIME IN RANGE | 24 h time inside this room’s comfort band. | Integer %; band lower/upper °C; current temperature |
| POWER (room) | Measured heater power for this room. | W and kW; gauge min/max (capacity when known) |
| ENERGY PRICE | Current electricity price. | Value + unit; forecast min/max used for the bar when present |
| SOLAR GAIN | Applied/measured solar gain for this room. | W; gauge max |
| HEAT LOSS | Instantaneous heat loss for this room. | W; gauge max |
| NEXT CONTROL | Time until the next P tick on the shared Start epoch. | Remaining; `dt_s`; `control_computing`; last control ran |
| NEXT NMPC | Time until the next NMPC slot on the shared Start epoch. | Remaining; `nmpc_period_s`; `nmpc_computing`; last NMPC ts |

Missing values render as `—`. Hidden gauges stay omitted from the grid.

## Classification
- Class: feature
- Confidence: high
- Why: new Overview/room interaction, layout, copy, and an extra duration
  attribute — a product slice, not a defect or a one-line tweak

## Workflow
- Template: feature-standard
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: none
  - sandbox: inject
- Chain: architect → sandbox → implement → test → restructure → review → ship
- Rationale: contained Ingress UI plus one attribute; inspect look in
  sandbox before production wiring; test and harden stay on; review stays
  focused (no new public API or auth)

## Inputs
- Research: none
- Model: none
- Sandbox: `docs/agents/SANDBOX-kpi-expand.md` (`sandbox/kpi-expand/inspect/`)
- Architecture: `docs/agents/ARCHITECTURE-kpi-expand-detail.md`
- Prior: SWD-430 countdown computing overlay; SWD-300 Overview KPI split;
  `heatingassistant/app/static/js/components/gauge.js`;
  `heatingassistant/app/static/js/pages/overview.js`;
  `heatingassistant/app/static/js/pages/room-detail.js`

## Pass criteria
- Clicking a visible Overview SYSTEM STATUS KPI moves that card to the first
  cell of that section, spans the grid width, and shows the live gauge plus
  the darkened description panel with the catalog rows for that KPI.
- Clicking a visible Overview CONTROLLER KPIS gauge or countdown does the
  same inside CONTROLLER KPIS without changing SYSTEM STATUS expansion.
- Clicking a visible room-view KPI or countdown does the same in the room KPI
  grid.
- Clicking a second card in the same section expands that card at the top and
  restores the previous card to collapsed original order.
- Clicking the expanded card, or Escape while it is focused, collapses it
  and restores original order.
- Collapsed cards keep the current gauge/countdown look (tokens, severity
  bar, computing overlay on the rings).
- Expanded MPC LOAD lists last P duration in seconds and last NMPC duration
  in seconds (not only the percent).
- Collapsed MPC LOAD percent still matches today’s
  `mpcLoadPercent` / `MAX_SOLVE_TIME_S` fill.
- Climate card, System Status page, and identification KPIs are unchanged.
- Fast pytest covering expand/collapse order and published
  `last_nmpc_duration_s` passes.

## Work packages
1. Shared expand host + CSS (wrap existing gauge/countdown; one-open-per-section; keyboard).
2. Per-KPI description catalog + `last_nmpc_duration_s` on `mpc_performance`.
3. Wire Overview (two sections) and room view; cache-bust panel assets.
4. Tests, CalVer, changelog, App package sync.

## Open items
- Exact wording of each description sentence may be tightened in sandbox
  stills; meaning stays as the catalog table.
- If last NMPC duration cannot be measured without a larger engine change,
  record that gap on the card as `—` and keep P duration; do not invent a
  number from the 2 s budget.

## Tracker
- Provider: jira (`SWD`)
- Story: —
- Task: [SWD-469](https://marcusknielsen.atlassian.net/browse/SWD-469)
- Sub-tasks: SWD-470, SWD-471, SWD-472, SWD-473
- Branch: `cursor/swd-469-kpi-expand-e3f0`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/652
- Classification: feature
- Workflow: feature-standard (`sandbox=inject`)

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/652 (`cdd42be`)

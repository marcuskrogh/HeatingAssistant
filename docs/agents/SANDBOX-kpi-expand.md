# Sandbox: expandable KPI detail cards

## Element
Click-to-expand KPI cards on Overview SYSTEM STATUS and CONTROLLER KPIS:
live gauge or countdown on top, darker description panel with absolute values
below. MPC LOAD lists last P and last NMPC durations.

## Kind
visual

## Isolation
- Path: `sandbox/kpi-expand/`
- Harness: `python3 sandbox/kpi-expand/harness.py --tag 01`
- Inspectables: `sandbox/kpi-expand/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `createGauge` / `createCountdown` / `bindKpiExpandSection`
    in a shadow root so `:host` tokens apply, as in the Ingress panel.
  - **Data** — `mpc_performance` fixture with P duration 0.18 s, last NMPC
    24.7 s, computing flags, intervals, and timestamps.
  - **Neighbours** — dark industrial theme, two `.grid-kpi` sections, live
    HEATING POWER neighbour plus both countdown rings.
  - **Path** — production expand host and catalog modules via `/ha-industrial-panel/`.
  - **Baseline** — collapsed still uses the shipped gauge/countdown chrome.
- How reproduced: HTTP harness maps `/ha-industrial-panel/` to production
  static files; sandbox only hosts the page shell.
- Gaps:
  - **Room view density** — named. Overview two-section layout is the
    inspect target; room grid uses the same host.
  - **Live 5 s Ingress poll** — named. Fixture is static for stills.

## Bar
omit (visual)

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/components/kpi-expand.js`
  - `heatingassistant/app/static/js/kpi-detail-catalog.js`
  - `heatingassistant/app/static/css/industrial.css`
  - Overview / room pages
  - `last_nmpc_duration_s` on `mpc_performance`
- Copy notes: candidate is already the production modules; dual-tree sync
  after implement. Do not ship sandbox `index.html`.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Collapsed vs MPC LOAD expanded vs NEXT NMPC computing | sandbox/kpi-expand/inspect/01_* | accept (ship) |

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production source.

## Tracker
- Task: [SWD-469](https://marcusknielsen.atlassian.net/browse/SWD-469)
- Artifact: docs/agents/SANDBOX-kpi-expand.md
- Branch: `cursor/swd-469-kpi-expand-e3f0`
- PR: — (sandbox never opens a PR)

## Next
`/implement SWD-469` — Promote accepted expand look into production (already wired on this branch after accept).

# Sandbox: KPI expand motion and split load cards

## Element
Click-to-expand KPI cards with split load KPIs:
- Overview SYSTEM STATUS: NMPC Load (last NMPC solve vs 10% of the NMPC
  period, clamped at 100%).
- Room view: Regulator Load (last P-cycle vs 2 s). Expand shows regulator
  description only, then Regulator and NMPC row groups.
- Nested inset, subsection titles, row separators between detail rows, and
  an explicit “Description” topic inside the expanded card. Outer card
  still moves to the top of its section and the viewport follows.

## Kind
visual

## Isolation
- Path: `sandbox/kpi-expand/`
- Harness: `python3 sandbox/kpi-expand/harness.py --tag 05`
- Inspectables: `sandbox/kpi-expand/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `createGauge` / `createCountdown` in a shadow
    root so `:host` tokens apply. Candidate host is
    `sandbox/kpi-expand/kpi-expand.js`; load copy is
    `sandbox/kpi-expand/load-catalog.js`.
  - **Data** — `mpc_performance` fixture with P duration 0.18 s, last NMPC
    24.7 s, `nmpc_period_s` 7200 s (NMPC load ≈ 3%; regulator load 9%).
  - **Neighbours** — Overview SYSTEM STATUS (health + NMPC Load) and a
    room KPI grid (Regulator Load, time in range, power, countdown rings).
  - **Path** — production gauge/countdown via `/ha-industrial-panel/`;
    sandbox overlay `expand.css`.
  - **Baseline** — collapsed chrome is still the shipped gauge/countdown.
- How reproduced: HTTP harness maps `/ha-industrial-panel/` to production
  static files; sandbox hosts the page shell, candidate host, load catalog,
  and overlay CSS.
- Gaps:
  - **Live 5 s Ingress poll** — named. Fixture is static for stills.
  - **Working formula** — NMPC 100% = 10% of period, bar clamped at 100%,
    until the operator changes it.

## Bar
omit (visual)

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/components/kpi-expand.js` (sections + row separators + Description topic)
  - `heatingassistant/app/static/js/kpi-detail-catalog.js` / `kpi-engine.js`
  - `heatingassistant/app/static/js/pages/overview.js` (NMPC Load)
  - `heatingassistant/app/static/js/pages/room-detail.js` (Regulator Load)
  - `heatingassistant/app/static/css/industrial.css`
- Copy notes: do not ship sandbox `index.html` or SPARE fillers.
  Keep `open(key)` without motion for screenshot modes.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | FLIP move + same-card height grow + viewport follow | sandbox/kpi-expand/inspect/02_* | delta: nested detail card |
| 2 | Description + values in a nested inset inside the outer frame | sandbox/kpi-expand/inspect/03_* | delta: split load KPIs |
| 3 | NMPC vs Regulator Load, subsections, row separators and Description topic | sandbox/kpi-expand/inspect/05_* | accepted |

## Role in pipeline
Post-merge inspect-loop after SWD-474. Promotion input for `/implement SWD-475`.
Supportive isolation — not production source. No sandbox PR.

## Tracker
- Task: [SWD-475](https://marcusknielsen.atlassian.net/browse/SWD-475)
- Relates: [SWD-474](https://marcusknielsen.atlassian.net/browse/SWD-474)
- Artifact: docs/agents/SANDBOX-kpi-expand-motion.md
- Branch: `cursor/swd-475-kpi-expand-motion-e3f0`
- PR: — (sandbox never opens a PR)

## Next
`/test SWD-475` — Dedicated testing phase, then restructure, then review

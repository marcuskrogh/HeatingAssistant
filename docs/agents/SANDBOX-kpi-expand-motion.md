# Sandbox: KPI expand motion and viewport follow

## Element
Click-to-expand KPI cards: the open card moves to the top of its section
while the outer frame grows. Description and value rows sit in a nested
detail card inside that frame. The viewport follows so the click does
not leave the card off-screen.

## Kind
visual

## Isolation
- Path: `sandbox/kpi-expand/`
- Harness: `python3 sandbox/kpi-expand/harness.py --tag 03`
- Inspectables: `sandbox/kpi-expand/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `createGauge` / `createCountdown` in a shadow
    root so `:host` tokens apply, as in the Ingress panel. Candidate host is
    `sandbox/kpi-expand/kpi-expand.js` (FLIP + follow); production host is
    not edited this turn.
  - **Data** — `mpc_performance` fixture with P duration 0.18 s, last NMPC
    24.7 s, computing flags, intervals, and timestamps.
  - **Neighbours** — dark industrial theme, two `.grid-kpi` sections, live
    HEATING POWER neighbour plus both countdown rings. Eight SPARE gauges
    in CONTROLLER KPIS so a click can start below the fold.
  - **Path** — production gauge/countdown/catalog via `/ha-industrial-panel/`;
    sandbox overlay `expand.css` for one-card chrome and height grow.
  - **Baseline** — collapsed still uses the shipped gauge/countdown chrome;
    screenshot `open(key)` stays instant (no motion) for stills.
- How reproduced: HTTP harness maps `/ha-industrial-panel/` to production
  static files; sandbox hosts the page shell, candidate host, and overlay CSS.
- Gaps:
  - **Room view density** — named. Overview two-section layout is the
    inspect target; room grid would use the same host on promote.
  - **Live 5 s Ingress poll** — named. Fixture is static for stills.

## Bar
omit (visual)

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/components/kpi-expand.js`
  - `heatingassistant/app/static/css/industrial.css` (outer KPI frame
    grows; nested inset holds description + value rows)
  - Overview / room pages only if cache-bust is required
- Copy notes: copy sandbox `kpi-expand.js` and overlay rules into production;
  dual-tree sync after implement. Do not ship sandbox `index.html` or SPARE
  fillers. Keep `open(key)` without motion for screenshot modes.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | FLIP move + same-card height grow + viewport follow | sandbox/kpi-expand/inspect/02_* | delta: nested detail card |
| 2 | Description + values in a nested inset inside the outer frame | sandbox/kpi-expand/inspect/03_* | pending operator |

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
`/sandbox SWD-475` — name a delta, or `/implement SWD-475` to promote

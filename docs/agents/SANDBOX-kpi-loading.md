# Sandbox: compute timer loading overlay

## Element
Loading animation on the NEXT CONTROL and NEXT NMPC countdown rings while
that solver is busy (`control_computing` / `nmpc_computing`). Live KPI
cards (HEATING POWER, ENERGY PRICE, and the rest) keep showing the actual
applied values — they are not loading.

## Kind
visual

## Isolation
- Path: `sandbox/kpi-loading/`
- Harness: `python3 sandbox/kpi-loading/harness.py --tag 02`
- Inspectables: `sandbox/kpi-loading/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `createCountdown` / `tick` on
    `sensor.heating_assistant_mpc_performance` (`dt_s`, `nmpc_period_s`,
    `last_nmpc_ts`). Candidate toggles `countdown--computing` per flag.
    Host is a shadow root so `:host` tokens apply, as in the Ingress panel.
  - **Data** — Overview CONTROLLER KPIS neighbours (HEATING POWER, ENERGY
    PRICE) plus both rings. Fixture `last_nmpc_ts` is 450 s into the
    shared epoch so remaining times match a mid-period tick. Flags match
    `hass_states()`.
  - **Neighbours** — dark industrial theme, `.grid-kpi` card layout,
    `.card.countdown` chrome, live gauges in the same row.
  - **Path** — same countdown DOM the Overview/room pages use; no toy
    markup. Shipped path still calls `setGaugeComputing` on KPIs (the
    wrong target). Candidate does not.
  - **Baseline** — shipped: KPI 8% white sweep; timers have no computing
    class. Candidate: spinning teal track + ring pulse + ` · computing`
    on the busy ring only; countdown value stays readable.
- How reproduced: HTTP harness maps `/ha-industrial-panel/` to production
  static files; candidate CSS lives only under `sandbox/kpi-loading/`.
- Gaps:
  - **5 s Ingress poll** — named. This turn inspects overlay look with
    the flag already true.
  - **P-tick brevity** — named. Control compute is milliseconds; the
    CONTROL overlay is pinned in `?mode=control`. Live P loading will
    rarely be visible. NMPC overlay is the one operators will see.

## Bar
omit (visual)

## Promote map
- Production targets (only after accept):
  - `heatingassistant/app/static/css/industrial.css` (`.countdown--computing`;
    remove KPI `.gauge--computing` use)
  - `heatingassistant/app/static/js/components/countdown.js`
    (`setCountdownComputing`)
  - Overview / room pages: `nmpc_computing` → NEXT NMPC ring,
    `control_computing` → NEXT CONTROL ring; stop calling
    `setGaugeComputing` on live KPI cards
- Copy notes: take `sandbox/kpi-loading/overlay.css` into `industrial.css`;
  do not ship `capture.css`. Dual-tree sync after implement.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Extract shipped overlay vs teal sweep + “computing” label on KPI gauges | sandbox/kpi-loading/inspect/01_computing.png | delta: overlay belongs on countdown rings, not live KPIs |
| 2 | Move overlay to NEXT CONTROL / NEXT NMPC; KPIs stay live; independent flags | sandbox/kpi-loading/inspect/02_nmpc.png | waiting: accept / delta / end |

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production source.
Post-merge inspect-loop instead of `/iterate` when each turn needs inspection.

## Tracker
- Task: [SWD-430](https://marcusknielsen.atlassian.net/browse/SWD-430)
- Relates: [SWD-426](https://marcusknielsen.atlassian.net/browse/SWD-426)
- Artifact: docs/agents/SANDBOX-kpi-loading.md
- Branch: `cursor/swd-430-kpi-loading-sandbox-7e18`
- PR: — (sandbox never opens a PR)

## Next
`/sandbox SWD-430` — next inspect turn, or `/implement SWD-430` if this overlay is accepted

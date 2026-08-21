# Sandbox: compute KPI loading overlay

## Element
Loading overlay on room-view compute KPI gauges (POWER, ENERGY PRICE,
SOLAR GAIN, HEAT LOSS, MODEL FIT) while `nmpc_computing` or
`control_computing` is true.

## Kind
visual

## Isolation
- Path: `sandbox/kpi-loading/`
- Harness: `python3 sandbox/kpi-loading/harness.py --tag 01`
- Inspectables: `sandbox/kpi-loading/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `createGauge` / `setGaugeComputing` /
    `isComputeInProgress` reading `sensor.heating_assistant_mpc_performance`
    attributes. Overlay CSS is the shipped `gauge--computing` rule in
    `industrial.css`. Host is a shadow root so `:host` tokens apply, as
    in the Ingress panel.
  - **Data** — room-view compute KPI labels and typical values (kW, price,
    W, model-fit GOOD). Fixture state sets `nmpc_computing` true/false
    exactly as `hass_states()` publishes.
  - **Neighbours** — dark industrial theme (`--bg-card`, `--accent` teal),
    `.grid-kpi` card layout, `.card.gauge` chrome.
  - **Path** — same class toggle the Overview/room pages use; no toy markup.
  - **Baseline** — shipped overlay (8% white sweep, label/value opacity 0.4).
- How reproduced: HTTP harness maps `/ha-industrial-panel/` to production
  static files; candidate CSS lives only under `sandbox/kpi-loading/`.
- Gaps:
  - **5 s Ingress poll** — named. This turn inspects overlay look with the
    flag already true; poll miss cannot hide a sweep this faint anyway.
  - **Live NLP duration / P-tick brevity** — named. Control compute is
    milliseconds; NMPC is seconds. Harness pins the NMPC flag.

## Bar
omit (visual)

## Promote map
- Production targets (only after accept):
  - `heatingassistant/app/static/css/industrial.css` (`.gauge--computing`)
  - keep `setGaugeComputing` / `isComputeInProgress` wiring
- Copy notes: take `sandbox/kpi-loading/overlay.css` into `industrial.css`;
  do not ship `capture.css`. Dual-tree sync after implement.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Extract shipped overlay vs teal sweep + “computing” label | sandbox/kpi-loading/inspect/01_computing.png | waiting: accept / delta / end |

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

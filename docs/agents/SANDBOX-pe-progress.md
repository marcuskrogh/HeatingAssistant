# Sandbox: PE live optimisation popup

## Element
Popup on Identification while a background PE job runs: central countdown of
remaining compute time, live fit-error (J) plot toward zero with a target
line at 0, so a 5-minute / 5-day fit is not a silent wait.

## Kind
visual

## Isolation
- Path: `sandbox/pe-progress/`
- Command: `python3 sandbox/pe-progress/harness.py --tag 02`
- Live: `python3 sandbox/pe-progress/harness.py --serve-only` then `/?live=1`
- Inspectables: `sandbox/pe-progress/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — Identification detail (`sysid-detail.js`) while
    `pe_job.status === running`; industrial theme tokens from
    `industrial.css` in a shadow host.
  - **Data** — `pe_job`-shaped snapshot: `cap_s` 300 (5 min Advanced cap),
    `f`, `nfev`, `f_hist` of J, `phase` tiled OE then N-step PEM.
    Fixture J decays but does not reach 0 in the cap (5-day / 5-min case).
  - **Neighbours** — dimmed Identification stub (back link, room header,
    “Running parameter estimation…”). Overlay uses the same card/modal
    language as Configuration `ha-modal`.
  - **Path** — future poll of `/api/pe_job` (1 s in production); sandbox
    replays a canned 5-day-fit-that-never-hits-zero trajectory.
  - **Baseline** — shipped wait is status text only (no overlay).
- How reproduced: harness maps `/ha-industrial-panel/` to production static
  CSS; sandbox owns overlay CSS/JS and the fixture.
- Gaps:
  - **Real SciPy callback stream** — named. Fixture replays a plausible J
    path; production must publish `f_hist` from the NLP.
  - **Live Ingress poll / 5-day wall time** — named. Replay is sped up.
  - **ftol vs J=0** — SciPy L-BFGS-B stops on relative change in J
    (`ftol`), not J reaching 0. Iteration 2 plots J toward zero because
    that is what the operator asked to watch. The stop rule is unchanged
    on promote.
  - These gaps do not move the visual verdict of layout, countdown, or plot.

## Bar
omit (visual)

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/identification/` overlay + poll
  - `heatingassistant/app/sysid_services.py` / `kalman_ml.py` /
    `nlp_eval.py` — publish progress on `pe_job`
  - CSS with other Identification / modal rules
- Copy notes: do not ship `index.html` or the fixture replay. Wire real
  `pe_job` fields (`f`, `f_hist`, `remaining_s`, `phase`). Keep timeout:
  do not apply θ when the cap hits. User-facing copy stays plain
  (fit error, time remaining); do not surface ftol jargon in the popup.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Identification overlay: central 5 min countdown, J, relative-step plot vs ftol | sandbox/pe-progress/inspect/01_*.png | delta: plot J toward 0; larger clock; hide jargon |
| 2 | Plot J (linear) with dashed target at 0; 80px countdown first; timeout still; no ftol footer | sandbox/pe-progress/inspect/02_*.png | waiting operator |

## Role in pipeline
Post-merge inspect-loop instead of `/iterate` after SWD-481. Promotion
input for `/implement`. Supportive isolation — not production source.

## Tracker
- Task: [SWD-486](https://marcusknielsen.atlassian.net/browse/SWD-486)
- Relates: [SWD-481](https://marcusknielsen.atlassian.net/browse/SWD-481)
- Artifact: `docs/agents/SANDBOX-pe-progress.md`
- Branch: `cursor/swd-486-pe-progress-popup-dfe4`
- PR: — (sandbox never opens a PR)

## Next
`/sandbox SWD-486` — name a delta, or accept and `/implement SWD-486`

# Sandbox: PE live optimisation popup

## Element
Popup on Identification while a background PE job runs: central countdown of
remaining compute time, live fit-error (J) plot toward zero with a target
line at 0. Plus a scale bench of N-step `J+grad` wall time vs window length.

## Kind
visual (popup) and measure (window-runtime bench)

## Isolation
- Path: `sandbox/pe-progress/`
- Command: `python3 sandbox/pe-progress/harness.py --tag 02`
- Live: `python3 sandbox/pe-progress/harness.py --serve-only` then `/?live=1`
- Bench: `python3 sandbox/pe-progress/bench_window.py`
- Inspectables: `sandbox/pe-progress/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — Identification detail (`sysid-detail.js`) while
    `pe_job.status === running`; industrial theme tokens from
    `industrial.css` in a shadow host. Bench uses production
    `nstep_pem_and_grad` / tiled OE `_simulation_mse_and_grad` on this
    cloud VM (not HAOS).
  - **Data** — popup: `pe_job`-shaped snapshot, `cap_s` 300. Bench:
    one-room excited history at live NMPC grid (`dt=900 s`, `N=144`,
    stride 8). Windows 6 h … 5 d (480 steps = `HISTORY_BUFFER_SIZE` /
    15 min ticks).
  - **Neighbours** — dimmed Identification stub for the popup.
  - **Path** — production receding N-step PEM + tiled OE warm-start
    objective; JSONL history is one record per coordinator tick
    (typically 15 min), same step count as the bench.
  - **Baseline** — shipped wait is status text only. Bench baseline is
    tiled OE eval time on the same windows.
- How reproduced: harness maps `/ha-industrial-panel/` to production static
  CSS; bench imports production estimator modules; fixture/history generators
  live only in the sandbox tree.
- Gaps:
  - **Real SciPy callback stream** — named. Popup fixture replays J.
  - **Live Ingress poll / 5-day wall time** — named. Replay is sped up.
  - **ftol vs J=0** — SciPy stops on relative change in J, not J=0.
  - **HAOS / Pi wall time** — named. This host is faster; order of
    magnitude vs window still holds.
  - **Multi-room / multi-dataset** — named. Bench is one room, one segment.
    Extra rooms grow EKF `P` Jacobians.
  - These gaps do not move the visual popup verdict. They do mean the
    seconds/eval numbers are a lower bound vs a loaded household on a Pi.

## Bar
- Measure: order-of-magnitude seconds per N-step `J+grad` vs window hours
  on this host; implied L-BFGS evaluations inside 60 s and 300 s caps.
- Scenario: production NMPC grid, one-room excited history, windows above.

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/identification/` overlay + poll
  - `heatingassistant/app/sysid_services.py` / `kalman_ml.py` /
    `nlp_eval.py` — publish progress on `pe_job`
  - CSS with other Identification / modal rules
- Copy notes: do not ship `index.html`, fixture replay, or the timing
  bench unless a later product ask wants scale copy in the popup. Wire
  real `pe_job` fields. Keep timeout: do not apply θ when the cap hits.
  User-facing copy stays plain (fit error, time remaining).

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Identification overlay: central 5 min countdown, J, relative-step plot vs ftol | sandbox/pe-progress/inspect/01_*.png | delta: plot J toward 0; larger clock; hide jargon |
| 2 | Plot J (linear) with dashed target at 0; 80px countdown first; timeout still; no ftol footer | sandbox/pe-progress/inspect/02_*.png | delta: approximate eval runtime vs window size |
| 3 | Bench N-step vs tiled-OE seconds/eval for 6 h–5 d; implied nfev in 1 min / 5 min caps | sandbox/pe-progress/inspect/03_window_runtime.* | waiting operator |

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

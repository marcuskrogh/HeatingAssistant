# Sandbox: PE live optimisation popup

## Element
Popup on Identification while a background PE job runs: fit error and
evaluation count as the central KPIs, log-scale J plot against SciPy
`ftol`, time remaining visible but secondary. Plus a Jacobian /
convergence measure on production N-step and tiled-OE gradients.

## Kind
visual (popup) and measure (Jacobian + L-BFGS J trace)

## Isolation
- Path: `sandbox/pe-progress/`
- Command: `python3 sandbox/pe-progress/harness.py --tag 04`
- Live: `python3 sandbox/pe-progress/harness.py --serve-only` then `/?live=1`
- Bench: `PYTHONPATH=. python3 sandbox/pe-progress/bench_jacobian.py`
- Inspectables: `sandbox/pe-progress/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — Identification detail while `pe_job.status === running`;
    industrial theme tokens from `industrial.css` in a shadow host.
    Jacobian bench uses production `nstep_pem_and_grad` /
    `_simulation_mse_and_grad` / `HouseThermalSDE.dfdx` on this cloud VM
    (not HAOS).
  - **Data** — popup: `pe_job`-shaped snapshot, `cap_s` 300, `ftol` 1e-12,
    J series with tiled-OE then N-step magnitudes and line-search spikes
    (same class as production `RegularizedMseCache` recorder). Bench:
    one-room excited history, 32 steps at live NMPC grid (`dt=900 s`).
  - **Neighbours** — dimmed Identification stub for the popup.
  - **Path** — production receding N-step PEM + tiled OE warm-start;
    progress records every unique `theta` eval (fun/jac cache), including
    L-BFGS line-search points.
  - **Baseline** — shipped popup (SWD-486) uses a linear J axis with a
    target at 0 and a central countdown. Jacobian baseline is analytic
    vs central differences at the prior.
- How reproduced: harness maps `/ha-industrial-panel/` to production
  static CSS; bench imports production estimator modules; fixture lives
  only in the sandbox tree.
- Gaps:
  - **Live Ingress poll / household 5-day window** — named. Replay is
    sped up; Jacobian window is 32 steps.
  - **HAOS / Pi wall time** — named.
  - **Multi-room P Jacobians** — named. Bench is one room.
  - These gaps do not move the visual KPI/log-plot verdict. They do mean
    the recorded J ratio is a lower bound vs a long household window.

## Bar
- Measure: analytic θ-gradient vs central differences; max relative
  error ≤ 5e-2 for N-step / tiled OE; `dfdx` ≤ 5e-4. L-BFGS J trace
  reports min/max/ratio (no pass/fail — characterizes jumps).
- Scenario: production NMPC grid, one-room excited history, 32 steps.

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/identification/pe-progress.js` + CSS
  - `pe_job` snapshot already publishes `f`, `nfev`, `f_hist`, `ftol`
  - Jacobian: no production change unless a later delta fails the bar
- Copy notes: keep timeout: do not apply θ when the cap hits. Time
  remaining stays visible, not the hero. Log y-axis includes `ftol`.
  Do not ship `index.html`, fixture replay, or benches unless a later
  product ask wants scale copy.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Identification overlay: central 5 min countdown, J, relative-step plot vs ftol | sandbox/pe-progress/inspect/01_*.png | delta: plot J toward 0; larger clock; hide jargon |
| 2 | Plot J (linear) with dashed target at 0; 80px countdown first; timeout still; no ftol footer | sandbox/pe-progress/inspect/02_*.png | delta: approximate eval runtime vs window size |
| 3 | Bench N-step vs tiled-OE seconds/eval for 6 h–5 d; implied nfev in 1 min / 5 min caps | sandbox/pe-progress/inspect/03_window_runtime.* | accept: promote popup; bench stays isolation-only |
| 4 | Hero KPIs = fit error + evaluations; time in a thin footer; log J vs ftol; FD Jacobian check | sandbox/pe-progress/inspect/04_*.png / 04_jacobian.md / 04_convergence.png | — |

## Role in pipeline
Post-merge inspect-loop instead of `/iterate` after SWD-486. Promotion
input for `/implement`. Supportive isolation — not production source.

## Tracker
- Task: [SWD-497](https://marcusknielsen.atlassian.net/browse/SWD-497)
- Relates: [SWD-486](https://marcusknielsen.atlassian.net/browse/SWD-486)
- Artifact: `docs/agents/SANDBOX-pe-progress.md`
- Branch: `cursor/swd-497-pe-popup-log-e770`
- PR: —

## Next
`/sandbox SWD-497` — next inspect turn after operator verdict (accept and promote, name a delta, or end sandbox-only)

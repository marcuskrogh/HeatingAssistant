# Sandbox: PE live optimisation popup

## Element
Popup on Identification while a background PE job runs: RMS error (°C) and
evaluation count as the central KPIs, log-scale **normalised RMS**
η = RMSE / σ_R against a reachable tolerance η ≤ 2 (1 °C). Time remaining
visible but secondary. Jacobian check from iteration 4 still stands.

## Kind
visual (popup) and measure (η on production J traces)

## Isolation
- Path: `sandbox/pe-progress/`
- Command: `python3 sandbox/pe-progress/harness.py --tag 05`
- Live: `python3 sandbox/pe-progress/harness.py --serve-only` then `/?live=1`
- Bench: `PYTHONPATH=. python3 sandbox/pe-progress/bench_proxy.py`
- Inspectables: `sandbox/pe-progress/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — Identification detail while `pe_job.status === running`;
    industrial theme tokens from `industrial.css` in a shadow host.
    Proxy bench uses production `nstep_pem_and_grad` /
    `_simulation_mse_and_grad` J plus the same n_obs counting as those
    loops.
  - **Data** — popup: `pe_job`-shaped snapshot with `eta`, `n_obs`,
    `eta_tol`, `r_var`. Replay η series with line-search spikes.
    Bench: 32-step one-room excited history at live NMPC grid, plus a
    5-day n_obs map of the operator's J≈54608 still.
  - **Neighbours** — dimmed Identification stub for the popup.
  - **Path** — production summed SSE/(n R_var); n_obs is residual-step
    count (tiled OE windows vs receding N-step horizons).
  - **Baseline** — iteration 4 plotted raw J vs SciPy ftol=1e-12 (not a
    fit-quality line). Jacobian FD bar from iteration 4.
- How reproduced: harness maps `/ha-industrial-panel/` to production
  static CSS; `proxy.py` / `proxy.js` share η = sqrt(J / n_obs);
  fixture lives only in the sandbox tree.
- Gaps:
  - **Regularisation in recorded J** — named. Promote η uses data MSE,
    not MSE+regularisation.
  - **Viewport on phone** — overlay must sit on the shadow root as a
    sibling of `.shell` (`position: fixed`); otherwise Estimate is
    below the overlay and the operator has to scroll up.
  - **Live Ingress poll / HAOS / multi-room** — named as before.
  - These gaps do not move the visual η/tolerance verdict.

## Bar
- Measure: after a short L-BFGS on the 32-step bench, last N-step η ≤ 2.
  Operator screenshot J mapped with 5-day n_obs is reported (no pass/fail).
- Scenario: production NMPC grid, one-room excited history.

## Promote map
- Production targets:
  - `heatingassistant/app/static/js/identification/pe-progress.js` + CSS
  - `kalman_ml._record_pe_progress` / `RegularizedMseCache`: publish
    `n_obs` (residual steps for that eval's objective), `r_var`,
    `eta = sqrt(data_mse / n_obs)`, `eta_tol` (default 2)
  - `nstep_pem_and_grad` / `_simulation_mse_and_grad` already count
    `n_steps_used` internally — set `est._pe_n_obs`
- Copy notes: keep timeout: do not apply θ when the cap hits. Plot η,
  not raw J and not SciPy ftol. η_tol = 2 ⇔ RMS ≤ 1 °C at R_var=0.25.
  Overlay on the shadow root (`position: fixed`), not inside the page
  scroller; dialog `overflow-y: auto`. Keep `liveClock`. Do not ship
  fixture replay or benches.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Identification overlay: central 5 min countdown, J, relative-step plot vs ftol | sandbox/pe-progress/inspect/01_*.png | delta: plot J toward 0; larger clock; hide jargon |
| 2 | Plot J (linear) with dashed target at 0; 80px countdown first; timeout still; no ftol footer | sandbox/pe-progress/inspect/02_*.png | delta: approximate eval runtime vs window size |
| 3 | Bench N-step vs tiled-OE seconds/eval for 6 h–5 d; implied nfev in 1 min / 5 min caps | sandbox/pe-progress/inspect/03_window_runtime.* | accept: promote popup; bench stays isolation-only |
| 4 | Hero KPIs = fit error + evaluations; time in a thin footer; log J vs ftol; FD Jacobian check | sandbox/pe-progress/inspect/04_*.png / 04_jacobian.md | delta: reachable normalised proxy + realistic tol |
| 5 | η = RMSE/σ_R; plot vs η_tol=2 (1 °C); KPI is RMS °C | sandbox/pe-progress/inspect/05_*.png / 05_proxy.md | accept |

## Role in pipeline
Post-merge inspect-loop instead of `/iterate` after SWD-486. Promotion
input for `/implement`. Supportive isolation — not production source.

## Tracker
- Task: [SWD-497](https://marcusknielsen.atlassian.net/browse/SWD-497)
- Relates: [SWD-486](https://marcusknielsen.atlassian.net/browse/SWD-486)
- Artifact: `docs/agents/SANDBOX-pe-progress.md`
- Branch: `cursor/swd-497-pe-popup-log-e770`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/663

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/663

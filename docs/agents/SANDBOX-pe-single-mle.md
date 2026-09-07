# Sandbox: Single-start N-step MLE vs production PE multistart

## Element
One L-BFGS on the production receding N-step PEM (MLE) objective from the
configured prior — no tiled-OE warm-start, no physics-informed start, no
extra prior restart.

## Kind
measure

## Isolation
- Path: `sandbox/pe-single-mle/`
- Command: `MPLBACKEND=Agg PYTHONPATH=. python3 sandbox/pe-single-mle/harness.py`
- Inspectables: `sandbox/pe-single-mle/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `KalmanMLEstimator.estimate()` and
    `nstep_pem_and_grad` / L-BFGS-B (`ScipyNLPBackend`). Candidate only
    monkeypatches `_multistart_joint_nlp` in-process.
  - **Data** — 24 h one-room excited history on the live NMPC grid
    (`dt=900 s`, `N=144`, origin stride 8). Estimator rooms are a misspecified
    prior (`C=12 MJ/K`, `R=0.04`) vs truth (`C=8 MJ/K`, `R=0.025`).
  - **Neighbours** — same identifiability gates, MAP regularisation, and
    packed θ as production.
  - **Path** — same `RegularizedMseCache` + `solve_lbfgs` seam.
  - **Baseline** — current `_multistart_joint_nlp` (tiled OE then several
    N-step starts).
- How reproduced: harness builds history with PRBS heater + diurnal outdoor;
  baseline calls `estimate()` unmodified; candidate replaces only the start
  list.
- Gaps:
  - **HAOS household / 30 min cap / multi-room** — named. This VM is not the
    operator box; extra rooms grow `nx` and θ. The overlay spikes the operator
    saw are the same independent `minimize` calls measured here.
  - **C/α ridge** — both methods land on the same degenerate θ (`α≈0.49`,
    C low, R high). Extra starts do not fix identifiability. Does not move
    the *multistart vs single MLE* verdict.
  - These gaps do not change that extra starts spent 4× evaluations to
    reconverge to the same point.

## Bar
- Metrics: final η (data RMS / σ_R), nfev, wall time, C and R relative error
  vs truth.
- Tolerance: candidate is significantly worse if it misses η ≤ 2 while
  baseline meets it, or η > 1.5× baseline when baseline is already above
  the bar.
- Scenario: the representative map above (not a 32-step toy window).

## Promote map
- Production targets: `heatingassistant/engine/estimation/kalman_ml.py`
  `_solve_joint_nlp` (was `_multistart_joint_nlp`) — one L-BFGS from
  `theta_prior` on the active objective (drop tiled OE, physics start, and
  extra prior start).
- Copy notes: keep timeout / no-apply-on-cap. Overlay `f_hist` will lose the
  ~35-eval reset spikes. Tests that assume OE-then-PEM phase order need a
  look. Do not treat C/R recovery as part of this promote.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | Single N-step MLE from prior vs production multistart | sandbox/pe-single-mle/inspect/01_* | accept: same η and θ, 99 vs 432 evals |

## Role in pipeline
Post-merge inspect-loop instead of `/iterate` after SWD-481. Promotion
input for `/implement`. Supportive isolation — not production source.

## Tracker
- Task: [SWD-503](https://marcusknielsen.atlassian.net/browse/SWD-503)
- Relates: [SWD-481](https://marcusknielsen.atlassian.net/browse/SWD-481)
- Artifact: `docs/agents/SANDBOX-pe-single-mle.md`
- Branch: `cursor/swd-503-pe-single-mle-3539`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/665

## Next
`/review SWD-503` — lasers then code review

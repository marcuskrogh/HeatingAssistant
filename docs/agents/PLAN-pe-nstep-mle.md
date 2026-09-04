# Implementation plan: Horizon-matched N-step PE MLE

## Summary
- Production PE today minimises **tiled ~12 h open-loop OE**, not the NMPC prediction. Long datasets do not match the configured control horizon; interior windows reseed the wall at steady state.
- Replace that objective with a **receding N-step prediction-error MLE**: \(N\) and \(dt\) from the **live NMPC config**; score the full fast-grid air path over the look-ahead; **EKF** on every fast step; **Jacobians through both** the filter and the open-loop. Fit \(T_w(t_0)\) at each dataset start as today.
- Dataset length is the **user’s pick**. A **wall-clock cap** (default **1 minute**, configurable) aborts without applying \(\theta\) and tells the user to shorten the selection or raise the cap.
- New **Configuration → Advanced** page ships that cap (home for later knobs).
- **Improvement bar:** on the household PE harness, beat today’s tiled OE on **N-step (horizon-matched) air RMSE**. Iterate until that bar holds.

## Scope / Decisions / Constraints
**In**
- `KalmanMLEstimator.estimate()`: receding multi-step Gaussian PEM on the configured NMPC grid (`nmpc_period`, `nmpc_fast_substeps`, `nmpc_horizon_h` → \(dt\), \(N=n_{\mathrm{fast}}\)).
- At each stored-dataset start: \(T_w(t_0)\) as a PE decision (same blocks as today); air from the first measurement.
- EKF predict+update on **every fast step** with \(\theta\) in the filter; N-step open-loop **cost launches on each slow NMPC period**; residual is the full fast-grid air trajectory on \([1,N]\).
- Analytic (or otherwise exact) sensitivities through **EKF and open-loop** — do not treat filtered \(x_0\) as independent of \(\theta\).
- Dashboard `log_likelihood` becomes the real N-step Gaussian NLL at the optimum (MAP prior still added in the NLP), not negative tiled OE.
- `pe_max_compute_s` (default 60). UI on Advanced as minutes (default 1). On expiry: stop the NLP, **do not apply** \(\theta\), error that the selected data is too large for the cap, to use a shorter window or fewer datasets, or to raise **Configuration → Advanced → PE max compute time**.
- Configuration landing card **Advanced** (`#config/advanced`); this slice’s only control is that cap.
- Household harness: N-step scorer using the same \(N,dt\) rule; comparative vs tiled OE; implement **until** new < old mean N-step RMSE (no give-up on first miss).
- Tests for timeout/abort, Advanced persist, estimator uses NMPC timing; CalVer; App package sync.

**Out**
- One-step innovation PED as the production objective (CD-EKF NLL stays diagnostic-only).
- Locking dataset length to a week (or any fixed duration).
- Extra Advanced knobs beyond the time cap.
- Changing NMPC / P / live EKF control laws (PE and config only).
- Reworking Parameter Estimation Simulate plots to the receding N-step display (later iterate if still misleading).
- Day-gated occupancy, 1R1C, envelope lock.

**Decisions**
- Criterion is **N-step path error** (MPC-relevant quasi-MLE), not one-step innovations. “Real MLE” means full \(\theta\)-dependence and Jacobians through EKF + free-run.
- Cost bound is **time**, not truncation of user-selected data.
- Timeout never installs a partial iterate.
- Improvement is required; first harness miss → another implement pass, not ship.

**Constraints**
- Dual tree: `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Do not commit unrelated `.agents/skills` dirty files.
- Background PE job (SWD-453) must honour the same cap and error payload.

## Classification
- Class: feature
- Confidence: high
- Why: new PE criterion plus a new Advanced config surface; not a defect with already-known correct \(\theta\)

## Workflow
- Template: feature-standard
- Parameters:
  - implement.mode: single
  - implement.verify: comparative
  - implement.iteration: until-bar
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: model
  - sandbox: none
- Chain: model → architect → implement → test → restructure → review → ship
- Rationale: formulation must be persisted before build; comparative until-bar because the user required N-step RMSE improvement and further passes if the first miss; test/harden floor; focused review (estimator + one config page).

## Inputs
- Research: `docs/agents/RESEARCH-pe-effectiveness.md`
- Model: `docs/agents/MODEL-pe-nstep-mle.md` (to write)
- Prior: `docs/agents/PLAN-pe-robust-open-loop.md`, `docs/agents/MODEL-pe-hidden-tw.md`, SWD-335 tiled OE, SWD-453 background PE
- Code: `heatingassistant/engine/estimation/sensitivity.py` (`_simulation_mse_and_grad`, `_cd_ped_neg_ll_and_grad`), `kalman_ml.py`, `nmpc_timing.py`

## Pass criteria
- On the SWD-329/332 household harness, mean **N-step horizon-matched** indoor-air RMSE of the new estimator is **strictly lower** than tiled OE on the same traces (iterate until this holds).
- Automatic PE uses configured NMPC \(dt\) and look-ahead for that N-step loss; changing those config values changes the PE grid without a code edit.
- Timeout at the configured cap leaves applied \(\theta\) unchanged and returns the shortening / Advanced-cap message.
- Configuration → Advanced shows and persists PE max compute time (default 1 minute).
- Existing closed-window UA / exclusion behaviour is unchanged except as implied by the new receding state (open-contact still modelled when \(UA_{\mathrm{open}}\) is identified).

## Work packages
1. Formulate N-step PEM + EKF Jacobians (`MODEL-pe-nstep-mle.md`) — SWD-482
2. Production receding N-step MLE estimator — SWD-483
3. PE time cap + Configuration Advanced page — SWD-484
4. N-step harness bar, tests, CalVer, App sync — SWD-485

## Open items
- Exact N-step Gaussian covariance (share \(R\) with the EKF vs a separate path-noise scale) — settle in `/model`.
- Whether multi-dataset joint fit concatenates EKF segments with a new \(T_w(t_0)\) at each dataset start only (expected yes).

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-481](https://marcusknielsen.atlassian.net/browse/SWD-481)
- Sub-tasks: [SWD-482](https://marcusknielsen.atlassian.net/browse/SWD-482), [SWD-483](https://marcusknielsen.atlassian.net/browse/SWD-483), [SWD-484](https://marcusknielsen.atlassian.net/browse/SWD-484), [SWD-485](https://marcusknielsen.atlassian.net/browse/SWD-485)
- Branch: `cursor/swd-481-pe-nstep-mle-dfe4`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/657
- Classification: feature
- Workflow: feature-standard (comparative, until-bar, side_paths=model)

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/657

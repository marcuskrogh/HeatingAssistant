# Implementation plan: Offline PE validation open-loop prediction accuracy

## Summary
- Extend the SWD-329 offline household PE harness with a **time-separated
  validation window**.
- After each fit, score **open-loop** indoor-air prediction accuracy on that
  hold-out (RMSE, MAE, R²). Open-loop free-run is the metric that matches
  model-predictive control (MPC) use: the identified 2R2C is rolled forward
  from a measured start with known inputs, without Kalman corrections.
- Physical θ recovery stays in the report as a secondary table. Goodness of
  fit on validation is the primary score. **No product winner is declared.**

## Scope / Decisions / Constraints
**In**
- Same test-only harness (`tests/test_swd329_pe_robustness.py`).
- Simulate 48 h at 15 min (192 steps): first 24 h = **train** (fits unchanged);
  next 24 h = **validation** (never passed to `estimate()`).
- Same occupancy / window extras continue on the val slice (same schedules,
  continuing outdoor/solar phase).
- Predictor is the **fitted 2R2C** (C, R, solar scale, α, splits,
  `internal_gain`). Plant occupancy watts are **not** injected. Procedures
  that used the assumed-UA channel apply the same assumed UA × contact on val
  (contacts are known, as they would be to MPC).
- Val free-run initial state: air = last train measurement at the val
  seam; wall = last wall temperature from rolling the fitted model
  through train (MPC-style continuation). Fallback if the roll fails:
  `(T_a + T_out) / 2`.
- Report columns: val open-loop RMSE / MAE / R², plus existing relative
  |error| vs true θ.
- Helper unit tests (no optimiser): split does not leak val into fit;
  truth θ on val beats prior θ; RMSE near sensor noise when extras are none
  and θ is truth.
- Full grid remains `pytest.mark.ondemand`.

**Out**
- App / production PE / UI.
- Changing the fit objective (production stays open-loop MSE).
- One-step Kalman validation score (user asked for open-loop).
- Declaring a product winner.
- Estimator-family bake-off (SWD-331).

**Decisions**
- Goodness of fit on hold-out open-loop air temperature is the evaluation
  the operator asked for; θ error remains visible so correlation can be
  judged, not as the ranking key.
- One continuous 24 h val free-run (not segmented N-step). That is the
  stricter MPC-relevant check (a day-ahead open-loop).
- Train/val is time-separated on one plant trajectory, not a second
  independent seed.

**Constraints**
- Do not import the harness from App / production engine.
- Do not change `sysid_services` or the PE UI.
- `pytest-fast` stays `-m "not slow and not ondemand"`.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional delta to an existing offline harness; not a product
  defect and not a new product slice

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: localized test/report change; cheapest binding that covers risk

## Inputs
- Research: `docs/agents/RESEARCH-pe-methods.md` (OE / simulation-error is
  the MPC-relevant criterion; supportive only)
- Prior harness: `docs/agents/PLAN-pe-robustness-household.md`,
  `docs/agents/REPORT-pe-robustness-household.md`

## Acceptance criteria
- Each bake-off row includes val open-loop RMSE, MAE, and R² of indoor air.
- Fits see only the train slice; helpers assert that.
- Report regenerated from the on-demand grid; still declares no winner.
- Fast-shard helpers pass without running the 108-fit grid.

## Work packages
1. Train/val split + open-loop scorer + report columns + helper tests +
   regenerate on-demand report.

## Open items
- None. Segmented 4 h / 12 h RMSE can be added later if a day-long free-run
  is too harsh.

## Tracker
- Provider: jira (`SWD`)
- Story: [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Task: [SWD-332](https://marcusknielsen.atlassian.net/browse/SWD-332)
- Sub-tasks: [SWD-333](https://marcusknielsen.atlassian.net/browse/SWD-333)
- Relates: [SWD-329](https://marcusknielsen.atlassian.net/browse/SWD-329)
- Branch: `cursor/swd-332-pe-val-acc-747e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/613
- Classification: tweak
- Workflow: delta-fast

## Next
`/review-fix SWD-332` — review the val-accuracy harness and report (same PR)

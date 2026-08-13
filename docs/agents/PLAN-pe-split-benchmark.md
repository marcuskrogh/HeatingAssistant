# Implementation plan: Offline PE combined vs separated/staged benchmark

## Summary
- Offline synthetic 2R2C bake-off of **combined joint** vs **auto-separated
  joint** vs **auto-separated + staged locking**, scored against known true
  \(\theta\). Completely separate from the running app (tests + report only).
- You judge from the report whether separation/staging is worth shipping.
  Production Parameter Estimation, UI, and guidance are unchanged.

## Scope / Decisions / Constraints
**In**
- New test-only harness under `tests/` that simulates a known 2R2C room,
  excites it with separable solar and heater, and re-estimates with the
  existing open-loop `KalmanMLEstimator` (no production PE/UI edits).
- Three procedures: (1) combined joint (today’s concatenation); (2)
  auto-separated fragments, shared \(\theta\), per-fragment \(T_w(t_0)\);
  (3) best-effort staged locking (solar-off → envelope \(C,R\); heater-off
  with solar → solar scale; then remaining, including heater scale).
- \(T_w(t_0)\) is a decision on each time-separated fragment; 24 h
  indoor/outdoor box \(\pm 25\%\) width is applied in the harness when
  clipping/reporting (estimator still uses its own bounds internally).
- A few representative scenarios (strong separable, weaker, mixed/overlap).
- Markdown report of \(\theta\)-recovery; **no automatic winner**.
- Tests assert the bake-off ran and the report exists — not which procedure
  is best.

**Out**
- App Parameter Estimation, in-app guidance, shipping a split procedure.
- Kalman/PED production path.
- Other model families, extra sensors, MPC.

**Decisions**
- Offline only; existing estimator is *called*, not rewritten.
- Staging recipe is best-effort and documented in the report.
- Winner is your call after reading the report.

**Constraints**
- Do not import the harness from App / production engine modules.
- Do not change `sysid_services` or the PE UI in this Task.

## Classification
- Class: feature
- Confidence: high
- Why: new eval slice with acceptance of its own; not a product defect and
  not a behaviour-preserving refactor

## Workflow
- Template: feature-standard
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: localized test+report; research and model already done; no new
  production layers

## Inputs
- Research: `docs/agents/RESEARCH-pe-effectiveness.md` (SWD-324)
- Model: `docs/agents/MODEL-pe-hidden-tw.md` (SWD-325)

## Acceptance criteria
1. Tests under `tests/` generate synthetic 2R2C data with known \(\theta\) and
   separable solar/heater excitation.
2. Combined joint, separated joint, and separated+staged all run; \(\theta\)
   error vs truth is recorded per scenario.
3. Report `docs/agents/REPORT-pe-dataset-separation.md` presents the numbers
   and does not declare a product winner.
4. App / production PE code paths are unchanged.
5. You decide next from the report (explore/define the product procedure).

## Work packages
1. Offline harness, scenarios, pytest, report writer.

## Open items
- Whether separation/staging ships in PE (your evaluation of the report).
- Numerical \(\Delta t_{\min}\), in-app guidance, open-loop vs Kalman in the
  product (deferred).

## Tracker
- Provider: jira (`SWD`)
- Story: [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Task: [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326)
- Sub-tasks: [SWD-327](https://marcusknielsen.atlassian.net/browse/SWD-327)
- Branch: `cursor/swd-326-pe-effectiveness-747e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/611
- Classification: feature
- Workflow: feature-standard

## Next
`/review-fix SWD-326` — focused review on the offline bake-off PR.

# Implementation plan: Offline PE robustness on household-like traces

## Summary
- Offline synthetic 2R2C plant + 2R2C estimator bake-off under household-like
  occupancy heat and open window/door extra outdoor exchange.
- Completely separate from the running App. Occupancy disturbance and extra
  UA live in the harness only (best-effort; documented in the report — same
  spirit as SWD-326 staged locking). No `/model` Task.
- Score relative |error| vs known true θ. **No product winner is declared.**

## Scope / Decisions / Constraints
**In**
- Test-only harness under `tests/` that simulates one 2R2C room with
  **known** extras, then re-estimates with the existing
  `KalmanMLEstimator`.
- Plant extras (injected on the air node in the sim loop, not by editing
  production `HouseModel`):
  - Occupancy: bursty household schedule (not an office square wave).
  - Open window/door: extra outdoor exchange \(Q = UA(T_\mathrm{out}-T_a)\),
    with known on/off.
- Full factorial: occupancy {none, weak, strong} × openings {none, weak,
  strong} = 9 scenarios.
- Six procedures, each on **both** estimator paths (open-loop production
  `estimate()`, and a harness-only Kalman/PED wrap of existing CD-EKF
  APIs):
  1. Today’s PE — combined joint, constant `internal_gain`, exclude
     open-window samples (SWD-322 `window_open` mask).
  2. Time-varying occupancy disturbance (best-effort: night/empty clock
     fragments for \(C,R\) with `internal_gain` locked 0, then occupied
     hours with \(C,R\) locked).
  3. Extra window/door conductance when contact is open (best-effort:
     include open samples; inject assumed UA × on/off × \((T_\mathrm{out}-y)\)
     as known air-node heat via the disturbance channel).
  4. Both extras in the estimator.
  5. SWD-326 `separated_joint`.
  6. SWD-326 `separated_staged`.
- Occupancy **watts** are never given to the estimator except as an
  *estimated* disturbance. Window on/off may be used by UA-using
  candidates (product has contacts).
- Markdown report of θ-recovery. Tests assert the bake-off ran and the
  report exists — not which procedure is best.
- Full grid is **on-demand only** (`pytest.mark.ondemand`). Tiny helper
  unit tests (no optimiser) stay on the default/fast shard.

**Out**
- App / production PE / UI changes.
- Formulating occupancy-state / extra-UA math as a separate model Task.
- CI running the full factorial.
- Multi-room plant, extra wall sensors, MPC.

**Decisions**
- Occupancy + extra UA are harness best-effort, documented in the report
  (not a product model).
- Production `estimate()` stays open-loop MSE. Kalman/PED is harness-only.
- Winner is your call after reading the report.

**Constraints**
- Do not import the harness from App / production engine modules.
- Do not change `sysid_services` or the PE UI in this Task.
- `pytest-fast` must use `-m "not slow and not ondemand"` so unmarked-as-slow
  ondemand tests do not run on every PR. Do **not** also mark the full grid
  `slow` (slow shards would pick it up).
- Cap optimiser iterations in the on-demand grid; document runtime in the
  report.

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
- Rationale: localized test+report; research already done; no new
  production layers

## Inputs
- Research: `docs/agents/RESEARCH-pe-robustness-household.md` (SWD-328)
- Model: `docs/agents/MODEL-pe-hidden-tw.md` (SWD-325)
- Prior analysis: `docs/agents/PLAN-pe-split-benchmark.md`,
  `docs/agents/REPORT-pe-dataset-separation.md`

## Acceptance criteria
1. Tests under `tests/` generate synthetic 2R2C data with known θ and
   household-like occupancy + window/door extras.
2. All six procedures run on both estimator paths across the 9-scenario
   factorial; θ error vs truth is recorded.
3. Report `docs/agents/REPORT-pe-robustness-household.md` presents the
   numbers, documents best-effort extras, and does not declare a product
   winner.
4. App / production PE code paths are unchanged.
5. Full grid is `pytest.mark.ondemand` and excluded from pytest-fast.
   Helper unit tests (no optimiser) run in CI.
6. You decide next from the report.

## Work packages
1. Offline harness, factorial, pytest marker/CI exclude, helper tests,
   on-demand bake-off, report writer.

## Open items
- Whether any procedure/extra is worth shipping in Parameter Estimation
  (your evaluation of the report).
- Product estimator changes and in-app guidance (deferred).
- Open-loop vs Kalman in the **product** (deferred).

## Tracker
- Provider: jira (`SWD`)
- Story: [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Task: [SWD-329](https://marcusknielsen.atlassian.net/browse/SWD-329)
- Sub-tasks: [SWD-330](https://marcusknielsen.atlassian.net/browse/SWD-330)
- Branch: `cursor/swd-329-pe-robustness-747e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/612
- Classification: feature
- Workflow: feature-standard

## Next
`/review-fix SWD-329` — Review and auto-fix per Workflow binding

# Implementation plan: Robust open-loop PE with identified UA and data guidance

## Summary
- Ship the bake-off winner: **identified contact-gated extra UA** plus the
  existing 24 h `internal_gain`, open-loop output-error, no envelope lock.
- Keep 2R2C. Do **not** ship day-gated occupancy in this slice (unregularized
  day-\(q\) overfit weak occupancy on hold-out).
- Backend categorises each stored dataset. The Parameter Estimation room
  page shows a compact row of equal category tiles (grey until a selected
  set covers that category, teal when supplied). Using a dataset lights
  the categories that set already covers. When every required category is
  supplied, the user can run the recommended estimate.

## Scope / Decisions / Constraints
**In**
- Production `KalmanMLEstimator.estimate()`: one extra parameter
  \(UA_{\mathrm{open}}\ge 0\) per room. Air-node heat
  \(UA_{\mathrm{open}}\,c(t)\,(T_{\mathrm{out}}-T_{\mathrm{a}})\) while the
  room’s window/door override contact is open.
- If the estimation window has fewer than \(N_{\min}\) open-contact samples
  (existing segment minimum), fix \(UA_{\mathrm{open}}=0\) and **keep
  SWD-322 exclusion** of those samples.
- If \(UA_{\mathrm{open}}\) is identified, **include** open-contact samples
  in the open-loop objective (they are no longer unmodelled).
- Fit linearisation may use measured air in the UA term; the free-run /
  MPC predictor uses simulated air. Occupancy stays 24 h `internal_gain`.
- \(T_{\mathrm{w}}(t_{0})\) unchanged (SWD-325). \(C,R\) stay free.
- Backend coverage **per stored dataset** (that dataset’s own records),
  per room:
  1. **Closed-window envelope** — recommend 12 h closed (hint: 24 h is
     better). The dataset covers this category when closed duration ≥ 12 h.
  2. **Heater excitation** — covers when the existing duty-cycle gate
     would treat the room’s heater scale / splits as identifiable.
  3. **Solar variation** — covers when the existing solar-scale gate
     would treat solar as identifiable.
  4. **Open-contact (extra UA)** — recommend 30 min open. Covers when
     open duration ≥ 30 min. If the room has no window/door entity, show
     **N/A** (not a failure; not required for recommended).
- Dataset list metadata includes those category tags. Each stored-dataset
  summary shows the categories that set covers.
- Parameter Estimation **room detail** (Stored Datasets): compact
  **four-up** read-only category tiles (Envelope, Heater, Solar, Open UA).
  **Use** on a dataset lights every category that set covers. Un-Use
  clears those lights unless another selected set still covers them.
- When every required category has at least one selected covering set,
  the primary action is **Run recommended estimation**. Incomplete
  selection can still run automatic PE (not labelled recommended).
  Tiles are not a second exclude-from-fit UI.
- Tests: helper coverage categoriser; production UA include/exclude
  behaviour; on-demand household grid must beat assumed-UA mean 0.83 °C
  with this procedure and not regress vs `today_combined` on
  window-closed (0.45 / 0.46 / 0.99 °C). Version bump + App package sync.

**Out**
- Day-gated \(q_{\mathrm{day}}\) / occupancy state / night-lock of \(C,R\).
- 1R1C estimator.
- Kalman/PED as the fit objective.
- User-toggled category filters that change which samples enter the fit.
- In-app PE “how to run an experiment” essays beyond this checklist.
- Home recordings as a new data source.
- MPC / controller behaviour besides using fitted \(UA_{\mathrm{open}}\)
  when contact is open (predictor must apply the same UA term).

**Decisions**
- Winner from tests: identified UA + 24 h \(q_{\mathrm{int}}\) (mean val
  0.64 °C, closed-window tie). Day-gated occupancy is a later iterate if
  still needed after this ships.
- Product, not harness-only.
- Coverage is computed **per stored dataset**; the compact row is the
  union of **Use**-selected sets (not the merged live window).
- A dataset may cover more than one category.
- Open-contact category is optional when the room has no contact entity.

**Constraints**
- Open-loop simulation MSE stays the objective.
- 2R2C structure unchanged.
- Cheap indoor climate + existing contacts only.

## Classification
- Class: feature
- Confidence: high
- Why: product PE extras plus a new Parameter Estimation guidance surface

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
- Rationale: model already landed; localized estimator + one PE page list;
  no authz/schema migration. Efficiency-first: not feature-heavy.

## Inputs
- Research: `docs/agents/RESEARCH-pe-robustness-household.md`,
  `docs/agents/RESEARCH-pe-effectiveness.md`
- Model: `docs/agents/MODEL-pe-contact-ua-occupancy.md` (UA term; occupancy
  day-gate **deferred** this slice per bake-off closed-window bar)
- Reports: `docs/agents/REPORT-pe-robustness-household.md`
- Prior: SWD-322 open-sample exclusion; identifiability gates in
  `estimation/identifiability.py`

## Acceptance criteria
1. Automatic PE identifies \(UA_{\mathrm{open}}\) when the window has enough
   open-contact samples, jointly with existing \(\theta\), under open-loop OE.
2. With too few open samples, \(UA_{\mathrm{open}}=0\) and SWD-322 exclusion
   still holds.
3. With enough open samples, those points are included and the UA term is
   applied in the fit and in open-loop / MPC-style free-run.
4. Closed-window rooms are not worse than today’s combined OE on the
   SWD-329/332 hold-out (0.45 / 0.46 / 0.99 °C). Mean val RMSE beats
   assumed-UA 0.83 °C.
5. PE room page shows four equal category tiles on one line
   (N/A for open-contact when the room has no contact). Grey until
   supplied; teal when at least one **Use**-selected dataset covers
   that category.
6. Each stored-dataset summary shows the categories that set covers.
7. All required categories supplied → **Run recommended estimation**.
8. Focused tests cover categoriser, per-dataset tags, UA include/exclude;
   on-demand grid for the val bar; version bump + App sync.

## Work packages
1. Identified contact-gated UA in production open-loop PE (include open
   samples only when UA is identified).
2. Backend coverage categoriser per stored dataset (four categories,
   durations, N/A) plus union of selected sets.
3. Parameter Estimation room page: compact grey/teal category tiles, dataset
   category chips, recommended-estimation CTA.
4. Tests, on-demand val bar, version bump, App package sync.

## Open items
- Exact copy strings for category labels (implement may tune wording).
- Whether fitted \(UA_{\mathrm{open}}\) is shown as an editable room
  parameter next to \(C,R\) (yes if it already has a param slot; otherwise
  report-only in estimate result this slice).

## Tracker
- Provider: jira
- Story: [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Task: [SWD-335](https://marcusknielsen.atlassian.net/browse/SWD-335)
- Sub-tasks: [SWD-336](https://marcusknielsen.atlassian.net/browse/SWD-336), [SWD-337](https://marcusknielsen.atlassian.net/browse/SWD-337), [SWD-338](https://marcusknielsen.atlassian.net/browse/SWD-338), [SWD-339](https://marcusknielsen.atlassian.net/browse/SWD-339)
- Branch: `cursor/swd-335-robust-ol-pe-747e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/614
- Classification: feature
- Workflow: feature-standard

## Next
`/review-fix SWD-335` — Review and auto-fix per Workflow binding

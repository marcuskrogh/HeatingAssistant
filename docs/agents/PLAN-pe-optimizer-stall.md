# Implementation plan: PE optimiser floors; extra time does not deepen the fit

Evidence (do not copy): project store `docs/pe-optimizer-stall.md`. 2 h run is sufficient; **do not wait** on the overnight 24 h cap experiment.

## Summary
- PE N-step L-BFGS **floors almost immediately**. Raising the cap from 30 min to 2 h added evals (~63) but not a better fit.
- Overlay **RMS is the last eval** (2 h screenshot **8.01 °C** spike at the end; floor is near η=2).
- Origins fire **every sample** (`fast_substeps=1`) so each eval is ~2 min on household data.
- Fix: slow-period origin stride, **stop when data η plateaus**, KPI/progress = **best** RMS, wait loop follows the configured cap. Still **do not apply θ** on a hard time-cap miss.

## Scope / Decisions / Constraints
**In**
- `origin_stride` for N-step PEM: slow-period equivalent (8×15 min = 2 h at current grid), **not** `timing.fast_substeps` after single-rate coerce.
- NLP stop when **data η** has not improved for a small run of accepted iterates (plateau), in addition to SciPy `gtol` / time cap. Keep `maxiter` as a backstop.
- Progress snap + overlay hero: **best** data RMS / η; plot may still show every eval (spikes stay visible).
- `waitForPeJob` deadline = job `cap_s` (not hard-coded 30 min); poll timeout **cancels** the worker.
- Tests, CalVer, changelog, App package sync.

**Out**
- Waiting on / blocking for the overnight 24 h run.
- Changing 2R2C, NMPC, or the N-step **objective**.
- Finite-difference PE; Jacobian rewrite (unless architect finds a proven jac bug).
- Applying θ when the time cap hits without a plateau/success exit.
- Restoring tiled-OE / multistart as production.

**Decisions**
- Class **bug**: expected stop + truthful KPI + MODEL origin grid; 2 h evidence rules out “just raise the cap.”
- Plateau is on **data η**, not raw J (MAP can keep ticking).
- Time cap still aborts **without apply**. Plateau/success may apply as today.
- Overlay after finish stays until X (SWD-504).

**Constraints**
- Dual tree: `heatingassistant/` then `scripts/sync-ha-app-package.sh`.
- No tracker keys in product copy.

## Classification
- Class: bug
- Confidence: high
- Why: stall, last-eval KPI, and origin-stride coupling are wrong against known intended behaviour

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: contained estimator + overlay; 2 h evidence is enough; test/harden floor; no sandbox (FD bar already held)

## Inputs
- Findings: project store `docs/pe-optimizer-stall.md`
- Model: `docs/agents/MODEL-pe-nstep-mle.md` (slow-period origins)
- Sandbox: `docs/agents/SANDBOX-pe-progress.md`, `docs/agents/SANDBOX-pe-single-mle.md` (C/α ridge)
- Code: `nstep_pem.py`, `kalman_ml.py`, `nlp_eval.py`, `parameter_lifecycle.py`, `pe-progress.js`, `sysid-detail.js`

## Pass criteria
- Production PE `origin_stride` is the slow-period step count (8 on a 15 min / 2 h grid), not `1` from NMPC `fast_substeps`.
- After data η stops improving, the NLP exits before burning the remaining wall-clock (2 h trace must not be the happy path).
- Overlay RMS/η KPIs equal the **best** recorded data misfit for that job, not the last line-search spike.
- Identification wait follows `pe_max_compute_s`; abandoning the wait **cancels** PE.
- Time-cap abort still does not apply θ.
- Focused tests pass; CalVer; changelog; App package in sync.

## Work packages
1. [SWD-559](https://marcusknielsen.atlassian.net/browse/SWD-559) — Origin stride, plateau stop, best-RMS overlay, wait/cancel vs cap
2. [SWD-560](https://marcusknielsen.atlassian.net/browse/SWD-560) — Tests, CalVer, changelog, App sync

## Open items
- Overnight 24 h cap: parallel; ignore unless it **contradicts** the 2 h floor (then `/iterate`).
- Publishing last θ vs bounds on the overlay: later tweak if still needed.

## Tracker
- Provider: jira (`SWD`)
- Story: [SWD-557](https://marcusknielsen.atlassian.net/browse/SWD-557) (Relates; Jira parent hierarchy does not nest Task under Story)
- Task: [SWD-558](https://marcusknielsen.atlassian.net/browse/SWD-558)
- Sub-tasks: [SWD-559](https://marcusknielsen.atlassian.net/browse/SWD-559), [SWD-560](https://marcusknielsen.atlassian.net/browse/SWD-560)
- Branch: `cursor/swd-558-pe-optimizer-stall-67bc`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/687
- Classification: bug
- Workflow: fix-fast

## Next
`/test SWD-558` — Dedicated testing phase, then restructure, then review

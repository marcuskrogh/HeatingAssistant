# Implementation plan: Fitted wall initial temperature for open-loop fit

## Summary
- Open-loop / EKF reconstruction still starts the hidden wall node near measured air. Predicted wall is almost flat; predicted air is biased after heat pulses.
- Tw0 is already a PE decision. Persist it on the **currently applied parameter set** (and the session last-PE-fit for dry-runs), keyed by the datasets (or window) used for that estimate.
- Loading a stored dataset used to estimate that parameter set restores the fitted Tw0. Otherwise estimate Tw0 on the plotted window with structural parameters fixed — never assume Tw0 = T_air.
- The Tw0 field shows whether the value came from the parameter estimate or a diagnostic window fit.

## Scope / Decisions / Constraints
**In**
- Persist `dataset_ids`, `t_wall_initial`, `t_wall_initial_by_dataset`, and a structural fingerprint on the ML snapshot when parameters are applied.
- Keep `runtime._last_pe_fit` after every successful PE (including dry-run) so Simulate can reuse that IC while the form still holds those θ values.
- EKF reconstruction and open-loop Simulate: reuse fitted Tw0 when the plotted dataset/window and current θ match that fit; otherwise wall-only window optimisation with a weak prior (not air-seeded).
- Honor locked Tw0.
- Populate the Tw0 field after PE, on dataset load when the match holds, and after Simulate; show source in the hint.
- Tests, CalVer, changelog, App package sync.

**Out**
- Changing C/R/α/UA identification besides Tw0 MAP use on the diagnostic window-only path.
- Live EKF / NMPC wall state (only PE diagnostics).
- Redesigning stored-dataset cards.

**Decisions**
- “Current parameter set” means the θ being simulated (form overrides / applied snapshot), not any historical estimate.
- Joint PE stores one Tw0 block per dataset; loading one of those datasets uses that block.
- Diagnostic window-fit uses `prior_mean="midpoint"` and `min_lam=0` so data can move Tw0. PE joint MAP floor stays (`_T_WALL_MIN_LAM`) so θ does not run to the bounds.
- Class is **bug**: expected IC is known; current air seed produces a wrong diagnostic.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Do not commit unrelated `.agents/skills` dirty files.

## Classification
- Class: bug
- Confidence: high
- Why: diagnostic IC is wrong (Tw0 ≈ T_air) with known correct behaviour (PE-fitted or window-optimal Tw0)

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
- Rationale: localized PE persistence + simulate IC; test/harden are the floor

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Prior: SWD-344 / PLAN-pe-sim-aux-tw0.md (always re-optimised Tw0 with air prior)

## Pass criteria
- Applying an ML estimate stores Tw0 and the dataset ids (or window) on that snapshot.
- Loading a stored dataset used for the currently configured θ restores that fitted Tw0; predicted wall starts there, not at air.
- Simulating a window/dataset that was not used for the current θ estimates Tw0 by wall-only optimisation; it is not T_air unless that is the optimum.
- Locked Tw0 is unchanged.
- Tw0 hint states `parameter_set` vs `window_fit`.
- Focused tests pass; CalVer + changelog + App sync.

## Work packages
1. Persist fitted Tw0 on the current parameter set — [SWD-478](https://marcusknielsen.atlassian.net/browse/SWD-478)
2. Resolve Tw0 from fitted set or window fit — [SWD-479](https://marcusknielsen.atlassian.net/browse/SWD-479)
3. Tw0 source in UI, tests, CalVer — [SWD-480](https://marcusknielsen.atlassian.net/browse/SWD-480)

## Open items
- None — screenshots plus the reuse-vs-estimate rule settle expected behaviour.

## Tracker
- Provider: jira
- Task: [SWD-477](https://marcusknielsen.atlassian.net/browse/SWD-477)
- Sub-tasks: [SWD-478](https://marcusknielsen.atlassian.net/browse/SWD-478), [SWD-479](https://marcusknielsen.atlassian.net/browse/SWD-479), [SWD-480](https://marcusknielsen.atlassian.net/browse/SWD-480)
- Branch: `cursor/swd-477-wall-init-a761`
- PR: (draft)
- Classification: bug
- Workflow: fix-fast

## Next
`/architect SWD-477` — shape stamp then implement on this branch

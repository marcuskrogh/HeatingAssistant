# Implementation plan: Revert PE fake slow origin grid

Canonical copy: project store `docs/revert-pe-slow-origin-grid.md`. Findings: store `docs/pe-optimizer-stall.md`. **Do not wait** on the overnight 24 h PE run.

## Summary
- After two-layer NMPC was removed, production is **one sample grid** (`period = dt`, `fast_substeps = 1`), like L-NMPC.
- SWD-558 / PR 687 then invented `pe_origin_stride(dt)` ≈ 2 h / dt (stride **8** at 15 min) so N-step origins would not fire every sample.
- That reconstructs a **slow period that no longer exists**. Revert it entirely. There is no slow and fast PE grid — only the one NMPC grid.
- Keep the rest of SWD-558: η plateau stop, overlay **best** RMS, `waitForPeJob` follows `cap_s` and cancels on abandon, time-cap still does not apply θ.

## Why stride-8 was introduced (and why it is wrong)

After single-rate NMPC (SWD-532), `origin_stride = timing.fast_substeps` became **1**. Receding N-step PEM then placed an origin at **every sample**. With `N = n_fast` (144 on a 15 min / 36 h look-ahead) each NLP eval walked a dense overlapping window and household jobs sat near ~2 min/eval. SWD-558 treated that cost as a defect of the origin grid and restored a historical slow-period stride (`DEFAULT_PE_ORIGIN_PERIOD_S = 7200`, `round(7200/dt)` → 8 at 900 s).

That is the wrong product model. Two-layer NMPC is gone; PE must not pretend a 2 h hold still exists. Cost of every-sample N-step is real, but the fix is **not** a fake slow period. Plateau stop already bounds wasted wall-clock. Further cost work (horizon, window, or formulation) is a later Task if still needed — not this revert.

## Scope / Decisions / Constraints
**In**
- Delete `pe_origin_stride` and `DEFAULT_PE_ORIGIN_PERIOD_S`.
- Production lifecycle: `origin_stride=timing.fast_substeps` (1 after coerce).
- Tests that lock stride-8 / “two-hour grid” / `origin_stride != fast_substeps` — rewrite to the single grid.
- Changelog: drop “origins every two hours” wording; keep plateau / best-RMS / wait.
- Dual tree + App package sync; CalVer.

**Keep (do not revert)**
- η plateau stop (`PeEtaPlateau`, stale-eval cap on **data η**).
- Overlay hero = **best** RMS / η (`eta_best`, `rmse_c_best`); plot may still show every eval.
- `waitForPeJob` deadline = job `cap_s`; abandoning the wait **cancels** the worker.
- Hard time-cap still **does not apply θ**.

**Out**
- Waiting on / blocking for the overnight 24 h cap.
- Reintroducing two-layer NMPC or any fake 2 h / stride-8 “slow period.”
- Changing the N-step objective, 2R2C, or L-BFGS.
- Applying θ on a time-cap miss.
- “Thinning” origins by another invented period.

**Decisions**
- Class **iterate**: SWD-558 merged; the origin-grid part is wrong on a single-rate plant.
- Same delivery PR from define through ship (new branch off `main`, not #687).
- Plateau / overlay / wait stay as shipped.

**Constraints**
- Dual tree: `heatingassistant/` then `scripts/sync-ha-app-package.sh`.
- No tracker keys in product copy.
- Dirty `.agents/skills` on the checkout is unrelated — do not commit it.

## Classification
- Class: iterate
- Confidence: high
- Why: post-merge SWD-558; shipped fake slow-period stride is wrong against the single NMPC grid

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
- Rationale: contained revert of one helper + wiring; keep test/harden floor; no sandbox

## Inputs
- Findings: store `docs/pe-optimizer-stall.md`
- Prior plan: store `docs/pe-optimizer-stall-plan.md` (origin-stride rows only are overturned)
- Code: `nmpc_timing.py`, `parameter_lifecycle.py`, `tests/test_swd558_pe_stall.py`, `tests/test_swd481_nstep_pem.py`

## Pass criteria
- `pe_origin_stride` / `DEFAULT_PE_ORIGIN_PERIOD_S` are gone.
- Production PE `origin_stride` equals `timing.fast_substeps` (1 after single-rate coerce).
- No remaining lock that PE origins sit on a 2 h / stride-8 grid.
- Plateau stop, best-RMS overlay, wait/cancel vs cap, and no-θ-on-time-cap tests still pass.
- CalVer; changelog; App package in sync.

## Work packages
1. [SWD-562](https://marcusknielsen.atlassian.net/browse/SWD-562) — Remove fake slow stride; wire `origin_stride=timing.fast_substeps`
2. [SWD-563](https://marcusknielsen.atlassian.net/browse/SWD-563) — Tests, CalVer, changelog, App sync

## Open items
- Overnight 24 h cap: still parallel; ignore unless it **contradicts** keep/revert (then `/iterate`).
- If every-sample N-step is still too expensive after plateau stop: new Task — not a silent stride.

## Tracker
- Provider: jira (`SWD`)
- Story: [SWD-557](https://marcusknielsen.atlassian.net/browse/SWD-557) (Relates)
- Task: [SWD-561](https://marcusknielsen.atlassian.net/browse/SWD-561)
- Prior: [SWD-558](https://marcusknielsen.atlassian.net/browse/SWD-558) (Relates)
- Sub-tasks: [SWD-562](https://marcusknielsen.atlassian.net/browse/SWD-562), [SWD-563](https://marcusknielsen.atlassian.net/browse/SWD-563)
- Branch: `cursor/swd-561-revert-pe-slow-origin-9845`
- PR: (opened on this Task)
- Classification: iterate
- Workflow: fix-fast

## Next
`/architect SWD-561` — shape stamp, then implement → test → restructure → review → ship on the same PR

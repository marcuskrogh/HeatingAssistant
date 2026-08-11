# Implementation plan: Remove IPOPT — SciPy-only parameter estimation

## Summary
- Parameter estimation currently constructs `IpoptNLPBackend` first and falls back to SciPy L-BFGS-B when `cyipopt` is missing, spamming logs.
- Stop requesting IPOPT entirely; use `ScipyNLPBackend` (L-BFGS-B) as the sole optimiser.
- Remove IPOPT/cyipopt-oriented tests and docs guidance; sync the App package copy.

## Scope / Decisions / Constraints
**In**
- `heatingassistant/engine/estimation/kalman_ml.py` — SciPy-only NLP solve path
- Sync `heating_assistant/` via `scripts/sync-ha-app-package.sh`
- Remove/rewrite `tests/test_solver_backend.py` and IPOPT-availability helpers in performance tests
- Update `tests/README.md` solver-backend section and estimation docstrings/comments

**Out**
- No change to MPC (already QP; legacy `"ipopt"`/`"slsqp"` strings ignored)
- No algorithmic change to the estimation objective / multistart / regularisation
- No need to install or document `mbc[ipopt]` / `cyipopt`
- Historical research / `BENCHMARKS.md` narrative may retain past IPOPT labels; runtime and active tests must not request IPOPT

**Decisions**
- Sole estimation backend: SciPy `L-BFGS-B` via `ScipyNLPBackend` (same options as today's fallback)
- No silent IPOPT→SciPy fallback; no "IPOPT backend unavailable" warning
- Delete the cyipopt engagement probe test rather than rebranding it

## Classification
- Class: tweak
- Confidence: high
- Why: intentional small behaviour delta — stop requesting IPOPT; SciPy-only path matches current effective fallback

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
- Rationale: cheapest binding for a localized optimiser-selection tweak with existing estimation tests

## Inputs
- Research: none
- Model: none

## Acceptance criteria
- Estimation no longer imports or constructs `IpoptNLPBackend`
- No log about IPOPT unavailable / falling back to L-BFGS-B
- Estimation uses SciPy L-BFGS-B directly
- `tests/test_solver_backend.py` removed; tests README no longer documents IPOPT probe/skip
- Performance estimation records/labels use SciPy (not IPOPT-as-requested)
- App package copy stays in sync with root `heatingassistant/`
- Focused estimation / solver-related tests pass

## Work packages
1. SciPy-only estimation + test/docs cleanup + App sync — SWD-315

## Open items
- none

## Tracker
- Provider: jira
- Task: SWD-315
- Sub-tasks: —
- Branch: `cursor/swd-315-remove-ipopt-scipy-a072`
- PR: (pending)
- Classification: tweak
- Workflow: delta-fast

## Next
`/implement SWD-315` — Build SciPy-only estimation per PLAN.md

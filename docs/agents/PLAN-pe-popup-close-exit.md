# Implementation plan: PE popup stays open with close control

## Summary
- After automatic parameter estimation finishes, the Identification progress
  popup stays open so the operator can read the plot and a human-readable
  optimiser exit (for example “Maximum iterations reached”).
- An X in the upper right always closes the popup. While a fit is running, X
  also stops estimation and does not apply parameters.

## Scope / Decisions / Constraints
**In**
- Identification PE overlay (`pe-progress` + `sysid-detail` wait loop).
- Job snapshot fields for exit label / cancelled status.
- Cancel path from the overlay through App service → estimator NLP loop.
- Human-readable SciPy L-BFGS-B exit (and timeout / user-stop / failure).
- Tests, CalVer, changelog, App package sync.

**Out**
- Changing the PE objective, time cap, or apply-on-timeout policy.
- Closing on overlay backdrop click (X only).
- Reworking the sandbox `pe-progress` isolation tree.

**Decisions**
- Class is **tweak**: shipped popup works; this is a small intentional UX delta.
- Stay-open on success, error, timeout, and cancel until X (or leaving the page).
- X while `status === running` sets a cancel event; the worker finishes as
  `cancelled` and does not merge estimated parameters.
- Exit copy is a short operator sentence, not a raw SciPy status integer.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional overlay/job behaviour delta, not a defect or new slice

## Workflow
- Template: delta-fast
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
- Rationale: localized popup + job cancel; tests lock stay-open/exit/cancel; test/harden are the floor

## Inputs
- Research: none
- Model: none
- Sandbox: docs/agents/SANDBOX-pe-progress.md (prior popup)
- Prior: SWD-486, SWD-497

## Pass criteria
- After a successful PE job, the overlay remains visible until the close control
  is used; `waitForPeJob` does not hide it in `finally`.
- The overlay shows a human-readable exit line for converged cost reduction,
  small gradient, maximum iterations, maximum evaluations, time limit, user stop,
  and failure.
- The dialog has an upper-right close control (`data-pe-close`).
- Activating close while the job is running requests cancel; the job ends
  `cancelled` / not success; parameters are not applied.
- Close after a finished job only hides the overlay.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. SWD-505 — Popup close control, stay-open, human-readable exit, cancel path
2. SWD-506 — Tests, CalVer, changelog, App sync

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-504
- Sub-tasks: SWD-505, SWD-506
- Branch: cursor/swd-504-pe-popup-close-cfe8
- PR: (draft after first push)
- Classification: tweak
- Workflow: delta-fast

## Next
`/architect SWD-504` — Shape stamp for overlay/job cancel, then implement

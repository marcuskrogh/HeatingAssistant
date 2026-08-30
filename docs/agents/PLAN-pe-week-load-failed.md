# Implementation plan: Parameter estimation Load failed on one-week data

## Summary
- Run recommended estimation on a stored one-week dataset (168 h / 672
  points) shows **Error: Load failed** next to the action button.
- The Ingress POST to `api/services` waits for the full L-BFGS-B fit. A
  672-step estimate takes ~47 s on a fast x86 host and longer on HAOS.
  The proxy drops the open request; Safari reports `TypeError: Load failed`.
- Fix: start the fit on a background worker (same pattern as NMPC),
  return immediately, and poll job status from the Parameter Estimation
  page until the fields can be loaded.

## Scope / Decisions / Constraints
**In**
- `estimate_parameters_ml` from Ingress starts a background job and
  returns `{status: "running"}` without waiting for the optimiser.
- GET `/api/pe_job` (and `pe_job` on `/api/state`) reports
  `idle` / `running` / `success` / `error` plus a user-facing message.
- The PE page keeps **Running parameter estimation…** while the job is
  running, then loads fields on success. Failures show the job message,
  not **Load failed**.
- A second start while a job is running returns the current running job
  (no overlapping fits).
- HTTP POST catches unexpected exceptions and returns a JSON 5xx instead
  of dropping the connection.
- Tests, CalVer `2026.08.36`, changelog, cache-bust, App package sync.

**Out**
- Changing the PE algorithm, window length, or identifiability gates.
- Progress percent / iteration counts.
- Making EKF / open-loop simulate asynchronous.

**Decisions**
- Class is a **bug**: one week of stored data is supported (duration
  guidance already says several days is better); the UI error is wrong.
- Keep `handle_estimate_parameters_ml` as the fit body for unit tests.
  The HTTP / `apply_service` path starts the worker.
- Do not put the full θ result on the job snapshot; merge into
  `sysid_results` as today and refresh state after success.

## Classification
- Class: bug
- Confidence: high
- Why: expected behaviour is a completed estimate on a valid one-week
  dataset; Ingress drops the blocking POST and the UI shows Load failed

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
- Chain: implement → test → harden → review-fix → ship
- Rationale: contained App HTTP/PE-page fix; unit tests cover start/poll.
  Test and harden stay on (catalog floor). Localized, so focused single
  review.

## Inputs
- Screenshot: Parameter Estimation Stored Datasets, 1Week, Error: Load failed
- Timed 672-step `KalmanMLEstimator.estimate` ~47 s (this environment)

## Acceptance criteria
1. POST `estimate_parameters_ml` returns in well under a second with
   `status: running` even when the fit itself takes tens of seconds.
2. After the worker finishes successfully, polling `/api/pe_job` returns
   `status: success` and `sysid_results` contains the estimated params.
3. Worker exceptions and unsuccessful fits set `status: error` with a
   message; the PE page shows that message, not Load failed.
4. A second start while running does not launch a second optimiser.
5. Fast suite passes. CalVer 2026.08.36; App package synced.

## Work packages
1. Background PE job + Ingress poll + PE page wait (SWD-454)
2. Tests, CalVer, changelog, App sync (SWD-455)

## Open items
- None.

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-453](https://marcusknielsen.atlassian.net/browse/SWD-453)
- Sub-tasks: [SWD-454](https://marcusknielsen.atlassian.net/browse/SWD-454),
  [SWD-455](https://marcusknielsen.atlassian.net/browse/SWD-455)
- Branch: `cursor/swd-453-pe-week-load-failed-66f5`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/647
- Classification: bug
- Workflow: fix-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/647

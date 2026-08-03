# Iterate: Ship Ipopt deps + remove solver names from mode labels

## Prior work
- Task: SWD-240
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/539
- Spec context: `docs/agents/PLAN-swd-240-dual-mode-mpc.md`

## Problem
- Mode selector labels showed solver names next to the problem type (`Linear (HiGHS)`, `Non-linear (Ipopt)`).
- Non-linear mode stays greyed out on HA because official `cyipopt` has no usable wheels; Ipopt was never installed by the integration.

## Clarifications
- User: do not write the solver next to the problem type in the config.
- User: properly install dependencies or vendor compiled libraries for possible platforms.

## Acceptance criteria
- Mode option labels are **Linear** and **Non-linear** only.
- Config help for the mode stays general (compute ↔ fidelity); no solver name beside the mode choice.
- Integration declares and/or vendors a working Ipopt/`cyipopt` backend so the restart probe can pass on supported platforms (linux x86_64/aarch64 manylinux + musllinux; macOS arm64 where wheels exist).
- Before the Ipopt probe, setup ensures `cyipopt` is importable (manifest requirement + vendored wheel fallback).
- Tests cover UI labels and the packaging/install contract.

## Out of scope
- Solver policy changes (still HiGHS for linear, Ipopt for non-linear).
- Phase 7 / CasADi / acados.
- Huge Windows wheels (optional later).

## Work packages
1. Strip solver names from mode UI / strings.
2. Add `cyipopt-wheels` requirement + vendored platform wheels + ensure-install before probe.
3. Tests for labels and install contract.

## Tracker
- Task: SWD-246
- Relates: SWD-240

## Next
`/review-fix SWD-246` — Review and auto-fix until clean

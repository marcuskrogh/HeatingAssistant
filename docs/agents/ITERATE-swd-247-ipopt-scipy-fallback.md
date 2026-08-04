# Iterate: Ipopt install pass + SciPy NLP fallback; clean mode help

## Prior work
- Task: SWD-246
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/540
- Spec context: `docs/agents/ITERATE-swd-246-ipopt-deps.md`, `docs/agents/PLAN-swd-240-dual-mode-mpc.md`

## Problem
- Non-linear mode still not usable; Ipopt/`cyipopt` install is not succeeding on HA.
- Config UI surfaces install/wheel noise in the mode description/hint — violates the agreed short general compute↔fidelity copy.
- If Ipopt cannot be installed automatically, non-linear must still run via another NLP backend.

## Clarifications
- Keep mode help short/general; do not list wheels or install paths in the UI.
- One light final Ipopt install pass (not heavier packaging).
- Fallback NLP: SciPy (`ScipyNLPBackend`), already used for system ID when Ipopt is missing.

## Acceptance criteria
- Mode selector help/hint stays short and general; no wheel filenames or install dumps.
- Setup tries a light Ipopt install (vendor-first on musl/HAOS; binary-only; refresh import path/caches).
- If Ipopt probe fails: non-linear uses SciPy NLP; mode remains selectable.
- Coordinator/probe expose nonlinear availability (Ipopt preferred, SciPy fallback); logs record which backend is active.
- Tests cover UI copy, install preference, and SciPy fallback wiring.

## Out of scope
- Phase 7 / CasADi / acados
- User-facing solver picker
- Expanding the vendored wheel matrix beyond selection/install fixes

## Work packages
1. Clean mode help / hint (no install dumps).
2. Light Ipopt install hardening + nonlinear probe with SciPy fallback.
3. Wire facade/factory/validation to allow non-linear on SciPy when Ipopt missing.
4. Tests.

## Tracker
- Task: SWD-247
- Relates: SWD-246
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/541

## Shipped
- Light Ipopt install (vendor-first on musl; binary-only; import refresh).
- SciPy NLP fallback when Ipopt unavailable; mode gated on `nonlinear_available`.
- Mode UI hints stay short — no wheel/install dumps.
- Review-fix CLEAN; fake-coordinator CI stubs fixed for linear rebuild path.

## Next
Done

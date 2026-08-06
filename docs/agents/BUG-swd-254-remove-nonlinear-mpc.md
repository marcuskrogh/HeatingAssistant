# Bug: Remove dual-mode nonlinear MPC (HA hang)

## Summary
- Dual-mode / nonlinear MPC (SWD-240 and follow-ups) hangs Heating Assistant and blocks Home Assistant Core startup, taking down the automation system.
- Fix: completely remove that path by restoring the tree to the commit prior to its introduction.

## Repro
1. Run HA on `main` with dual-mode / NMPC present (post PR #539).
2. Start or reload Heating Assistant / HA Core.
3. Observe hang / Core failing to come up.

## Expected
- HA Core and Heating Assistant start normally on the pre-dual-mode QP/linear MPC path.

## Actual
- Nonlinear MPC path hangs the process and blocks Core startup.

## Impact
- Critical — entire automation system unavailable.

## Suspected area
- Dual-mode MPC introduction in PR #539 (`346cce7`, SWD-240) and follow-ups SWD-246 / SWD-247 / SWD-253 (Ipopt/cyipopt/SciPy NLP).
- Pre-introduction commit: `30814c41ccf118ec7b1aafc05cacc10fb4938227`.

## Acceptance criteria
- [x] Codebase tree matches `30814c4` for product code (dual-mode / NMPC / Ipopt / SciPy NLP mode UI removed).
- [x] `main` updated via PR that lands this restore.
- [x] Tests for the restored linear/QP path pass (1750 passed, 6 skipped).

## Out of scope
- Re-introducing NMPC with stronger computational isolation (future work).
- Keeping dual-mode config UI or NLP solver backends.

## Tracker
- Task: SWD-254
- Relates: SWD-238 (dual-mode story), SWD-248 (hang mitigations superseded)
- Branch: `cursor/swd-254-remove-nonlinear-mpc-2550`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/544

## Next
`/ship SWD-254` — merge PR #544 to restore pre-SWD-240 tree on main

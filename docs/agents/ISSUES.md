# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-254 | Bug | [Bug] Remove dual-mode nonlinear MPC (revert to pre-SWD-240) — HA hang | Done | — | docs/agents/BUG-swd-254-remove-nonlinear-mpc.md | Done |
| SWD-238 | Story | Dual-mode MPC (linear / non-linear) | To Do | — | — | Dual-mode removed from main by SWD-254; optional Story closeout |
| SWD-248 | Task | [Bug] stop NMPC hang (executor, timeout, SciPy horizon cap) | Done | — | — | Done — superseded by SWD-254; PR #542 closed |

## Log

- 2026-08-06 — shipped SWD-254 via PR #544 (merge `5fa0ac6`): product tree restored to `30814c4`; dual-mode nonlinear MPC removed from main.
- 2026-08-06 — SWD-254 implement: product tree matches `30814c4` (excl. SWD-254 docs); pytest 1750 passed / 6 skipped. PR #544.
- 2026-08-06 — SWD-254: restore tree to `30814c4` (pre PR #539 / SWD-240) to remove dual-mode nonlinear MPC after HA Core hang. Branch `cursor/swd-254-remove-nonlinear-mpc-2550`. Dual-mode artifacts (SWD-239/240/246/247/253 docs) removed with the revert.

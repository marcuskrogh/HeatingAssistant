# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-254 | Bug | [Bug] Remove dual-mode nonlinear MPC (revert to pre-SWD-240) — HA hang | To Do | — | docs/agents/BUG-swd-254-remove-nonlinear-mpc.md | `/implement SWD-254` |
| SWD-238 | Story | Dual-mode MPC (linear / non-linear) | To Do | — | — | Superseded on main by SWD-254 revert; optional closeout |
| SWD-248 | Task | [Bug] stop NMPC hang (executor, timeout, SciPy horizon cap) | To Do | — | — | Superseded by SWD-254 full revert |

## Log

- 2026-08-06 — SWD-254: restore tree to `30814c4` (pre PR #539 / SWD-240) to remove dual-mode nonlinear MPC after HA Core hang. Branch `cursor/swd-254-remove-nonlinear-mpc-2550`. Dual-mode artifacts (SWD-239/240/246/247/253 docs) removed with the revert.

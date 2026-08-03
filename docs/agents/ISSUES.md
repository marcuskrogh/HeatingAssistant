# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-238 | Story (Epic) | Dual-mode MPC (linear / non-linear) | To Do | — | docs/agents/ROADMAP-dual-mode-mpc.md | `/implement SWD-240` |
| SWD-239 | Task | Research: solver backends & mode-guidance evidence (Ipopt NMPC; HPIPM for linear) | To Do | SWD-238 | docs/agents/RESEARCH-swd-239-dual-mode-mpc.md | `/define SWD-240` done → implement on SWD-240 |
| SWD-240 | Task | Define: dual-mode MPC config (linear / non-linear) + guidance + internal solvers | In Review | SWD-238 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | `/review-fix SWD-240` |
| SWD-241 | Sub-task | Config/UI: MPC mode selector, help text, grey-out | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | `/review-fix SWD-240` |
| SWD-242 | Sub-task | Controller: linear=HiGHS, non-linear=Ipopt, shared weights | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | `/review-fix SWD-240` |
| SWD-243 | Sub-task | Ipopt capability probe on restart | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | `/review-fix SWD-240` |
| SWD-244 | Sub-task | Runtime NMPC failure: log + persistent notification | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | `/review-fix SWD-240` |
| SWD-245 | Sub-task | Tests and docs for dual-mode MPC | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | `/review-fix SWD-240` |

## Log

- 2026-08-03 — explore charted dual-mode MPC map: Epic SWD-238, research SWD-239, define SWD-240 (SWD-239 blocks SWD-240). Next: `/research SWD-239`.
- 2026-08-03 — research SWD-239 brief landed (`docs/agents/RESEARCH-swd-239-dual-mode-mpc.md`) on `cursor/swd-239-mpc-solver-research-d0ba`. Next: `/define SWD-240`.
- 2026-08-03 — define SWD-240 PLAN approved; Sub-tasks SWD-241–245; branch `cursor/swd-240-dual-mode-mpc-d0ba`. Next: `/implement SWD-240`.
- 2026-08-03 — implemented SWD-240 dual-mode MPC on PR #539; full pytest suite passed. Next: `/review-fix SWD-240`.
- 2026-08-03 — fix-forward addressed PR #539 must-fix findings for preview `mpc_mode`, IPOPT probe gating, and nonlinear price profiles. Next: `/review-fix SWD-240`.

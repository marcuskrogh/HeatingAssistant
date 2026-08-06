# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-253 | Bug | [Bug] HA Core UI freezes on startup when cyipopt wheel install hangs | In Progress | — | docs/agents/BUG-swd-253-cyipopt-startup-hang.md | `/review-fix SWD-253` |
| SWD-238 | Story (Epic) | Dual-mode MPC (linear / non-linear) | To Do | — | docs/agents/ROADMAP-dual-mode-mpc.md | Optional: close research **SWD-239** |
| SWD-239 | Task | Research: solver backends & mode-guidance evidence (Ipopt NMPC; HPIPM for linear) | To Do | SWD-238 | docs/agents/RESEARCH-swd-239-dual-mode-mpc.md | Optional closeout (brief landed) |
| SWD-240 | Task | Define: dual-mode MPC config (linear / non-linear) + guidance + internal solvers | Done | SWD-238 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | Done |
| SWD-246 | Task | [Iterate] SWD-240: ship Ipopt deps + remove solver names from mode labels | Done | SWD-238 | docs/agents/ITERATE-swd-246-ipopt-deps.md | Done |
| SWD-247 | Task | [Iterate] SWD-246: Ipopt install pass + SciPy NLP fallback; clean mode help | Done | SWD-238 | docs/agents/ITERATE-swd-247-ipopt-scipy-fallback.md | Done |
| SWD-241 | Sub-task | Config/UI: MPC mode selector, help text, grey-out | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | Done |
| SWD-242 | Sub-task | Controller: linear=HiGHS, non-linear=Ipopt, shared weights | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | Done |
| SWD-243 | Sub-task | Ipopt capability probe on restart | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | Done |
| SWD-244 | Sub-task | Runtime NMPC failure: log + persistent notification | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | Done |
| SWD-245 | Sub-task | Tests and docs for dual-mode MPC | Done | SWD-240 | docs/agents/PLAN-swd-240-dual-mode-mpc.md | Done |

## Log

- 2026-08-06 — bug SWD-253: HA Core freeze from cyipopt install on HAOS. Fix: remove Ipopt/cyipopt entirely; SciPy-only NLP. Branch `cursor/swd-253-cyipopt-startup-hang-af84`. Next: `/review-fix SWD-253`.
- 2026-08-03 — explore charted dual-mode MPC map: Epic SWD-238, research SWD-239, define SWD-240 (SWD-239 blocks SWD-240). Next: `/research SWD-239`.
- 2026-08-03 — research SWD-239 brief landed (`docs/agents/RESEARCH-swd-239-dual-mode-mpc.md`) on `cursor/swd-239-mpc-solver-research-d0ba`. Next: `/define SWD-240`.
- 2026-08-03 — define SWD-240 PLAN approved; Sub-tasks SWD-241–245; branch `cursor/swd-240-dual-mode-mpc-d0ba`. Next: `/implement SWD-240`.
- 2026-08-03 — implemented SWD-240 dual-mode MPC on PR #539; full pytest suite passed. Next: `/review-fix SWD-240`.
- 2026-08-03 — fix-forward addressed PR #539 must-fix findings for preview `mpc_mode`, IPOPT probe gating, and nonlinear price profiles. Next: `/review-fix SWD-240`.
- 2026-08-03 — review-fix CLEAN; shipped SWD-240 via PR #539. Story SWD-238 remains open (SWD-239 still To Do).
- 2026-08-03 — iterate SWD-246 opened: strip solver names from mode labels; vendor/install cyipopt-wheels so Ipopt probe can pass on HA.
- 2026-08-04 — review-fix CLEAN; shipped SWD-246 via PR #540 (Ipopt vendored wheels + mode label cleanup). Story SWD-238 remains open (SWD-239 still To Do).
- 2026-08-04 — iterate SWD-247: light Ipopt install pass + SciPy NLP fallback when Ipopt unavailable; remove wheel/install dumps from mode UI hints. Next: `/review-fix SWD-247`.
- 2026-08-04 — review-fix SWD-247 CLEAN on PR #541 (1 review + fix-forward + re-review). Next: `/ship SWD-247`.
- 2026-08-04 — shipped SWD-247 via PR #541 (SciPy NLP fallback + clean mode help). Story SWD-238 remains open (SWD-239 still To Do).

# Roadmap: Dual-mode MPC (linear / non-linear)

## Destination
Users can run the **current** OCP in either **linear** or **non-linear** mode: non-linear embeds the nonlinear dynamics in the MPC; linear keeps the linearized QP path. Modes are configurable with clear guidance; each mode binds to one internally chosen solver (no user-facing solver picker).

## Notes
- Stay on the current OCP — no Phase 7 / CasADi / acados rewrite.
- User-facing names: **linear** | **non-linear**.
- Solvers are internal defaults. Working assumption: **Ipopt** for non-linear; for linear, investigate **HPIPM** (efficiency / Riccati recursion fit) before locking.
- Beta / sole user — no multi-install migration work.
- Product `docs/ROADMAP.md` Phase 7 remains separate; this initiative is a thinner dual-backend slice.
- Map path is under `docs/agents/` because `docs/*` (outside agents) is gitignored.
- Jira map parent is an **Epic** (SWD hierarchy: Epic above Task); logical explore Story → SWD-238.

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Solver backends & mode-guidance evidence (Ipopt NMPC; HPIPM vs HiGHS/OSQP for linear / Riccati fit; when to recommend each mode) | research | — | To Do (brief landed) | [SWD-239](https://marcusknielsen.atlassian.net/browse/SWD-239) |
| 2 | Dual-mode MPC: config (linear / non-linear), guidance copy, internal solver binding, acceptance | define | SWD-239 | To Do | [SWD-240](https://marcusknielsen.atlassian.net/browse/SWD-240) |

## Cleared so far
- [Research: solver backends & mode-guidance](https://marcusknielsen.atlassian.net/browse/SWD-239) — Ipopt fits NMPC; HPIPM Riccati needs OCP structure HA does not use today (condensed QP); keep HiGHS/OSQP as linear evidence-default; guidance = fidelity vs convexity/deps. Brief: `docs/agents/RESEARCH-swd-239-dual-mode-mpc.md`

## Not yet specified
- Default mode for beta (`linear` vs `non-linear`) — define
- Whether to attempt HPIPM **dense** QP anyway (packaging cost) vs stay on HiGHS/OSQP — define
- Whether a **model** Task is needed (only if define chooses Riccati / stage-wise OCP reformulation)
- Exact guidance copy / UI placement — define (research listed decision factors)
- Failure / missing-dependency behaviour (e.g. cyipopt unavailable) — define

## Out of scope
- Phase 7 CasADi / acados / full numerics rewrite
- User-selectable concrete solvers (HiGHS vs OSQP vs Ipopt as options)
- Multi-user install migration / backwards-compat campaigns
- MILP / mixed-integer MPC

## Tracker
- Provider: jira
- Story (map): [SWD-238](https://marcusknielsen.atlassian.net/browse/SWD-238) (Epic)
- Tasks: [SWD-239](https://marcusknielsen.atlassian.net/browse/SWD-239), [SWD-240](https://marcusknielsen.atlassian.net/browse/SWD-240)

## Next
`/define SWD-240` — Dual-mode config + guidance + internal solvers; research brief is supportive only

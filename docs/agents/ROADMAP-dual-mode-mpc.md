# Roadmap: Dual-mode MPC (linear / non-linear)

## Destination
Users can run the **current** OCP in either **linear** or **non-linear** mode: non-linear embeds the nonlinear dynamics in the MPC; linear keeps the linearized QP path. Modes are configurable with clear guidance; each mode binds to one internally chosen solver (no user-facing solver picker).

## Notes
- Stay on the current OCP — no Phase 7 / CasADi / acados rewrite.
- User-facing names: **linear** | **non-linear**.
- Solvers are internal defaults: **HiGHS** (linear), **Ipopt** (non-linear). HPIPM out of this slice.
- Beta / sole user — no multi-install migration work.
- Product `docs/ROADMAP.md` Phase 7 remains separate; this initiative is a thinner dual-backend slice.
- Map path is under `docs/agents/` because `docs/*` (outside agents) is gitignored.
- Jira map parent is an **Epic** (SWD hierarchy: Epic above Task); logical explore Story → SWD-238.

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Solver backends & mode-guidance evidence (Ipopt NMPC; HPIPM vs HiGHS/OSQP for linear / Riccati fit; when to recommend each mode) | research | — | To Do (brief landed) | [SWD-239](https://marcusknielsen.atlassian.net/browse/SWD-239) |
| 2 | Dual-mode MPC: config (linear / non-linear), guidance copy, internal solver binding, acceptance | define | SWD-239 | To Do (PLAN ready) | [SWD-240](https://marcusknielsen.atlassian.net/browse/SWD-240) |

## Cleared so far
- [Research: solver backends & mode-guidance](https://marcusknielsen.atlassian.net/browse/SWD-239) — Ipopt fits NMPC; HPIPM Riccati needs OCP structure HA does not use today (condensed QP); keep HiGHS/OSQP as linear evidence-default; guidance = fidelity vs convexity/deps. Brief: `docs/agents/RESEARCH-swd-239-dual-mode-mpc.md`
- [Define: dual-mode MPC](https://marcusknielsen.atlassian.net/browse/SWD-240) — PLAN locked: linear default=HiGHS, non-linear=Ipopt, probe grey-out, persistent notification on NMPC fail, shared weights. Plan: `docs/agents/PLAN-swd-240-dual-mode-mpc.md`

## Not yet specified
- Exact notification / probe wording (implement drafts)
- Optional mode-gated fields (none required in v1)

## Out of scope
- Phase 7 CasADi / acados / full numerics rewrite
- User-selectable concrete solvers (HiGHS vs OSQP vs Ipopt as options)
- Multi-user install migration / backwards-compat campaigns
- MILP / mixed-integer MPC
- HPIPM (deferred; not in this slice)

## Tracker
- Provider: jira
- Story (map): [SWD-238](https://marcusknielsen.atlassian.net/browse/SWD-238) (Epic)
- Tasks: [SWD-239](https://marcusknielsen.atlassian.net/browse/SWD-239), [SWD-240](https://marcusknielsen.atlassian.net/browse/SWD-240)
- Sub-tasks (SWD-240): SWD-241, SWD-243, SWD-242, SWD-244, SWD-245

## Next
`/implement SWD-240` — Build dual-mode MPC per PLAN.md on `cursor/swd-240-dual-mode-mpc-d0ba`

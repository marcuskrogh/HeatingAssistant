# Implementation plan: Dual-mode MPC (linear / non-linear)

## Summary
- Config **MPC mode** at the top of the options UI: **linear** (default) | **non-linear**.
- Same OCP weights/costs/constraints; difference is linearized QP vs nonlinear dynamics in the OCP.
- Internal solvers: **HiGHS** (linear), **Ipopt** (non-linear). No alternate-solver fallback.
- Ipopt gated by a **restart capability probe**; non-linear greyed out until it passes.
- Mid-cycle NMPC failure: no silent linear switch — log + **persistent notification** telling the user to switch to linear.
- Help text: general compute ↔ fidelity/accuracy trade-off.

## Scope
**In**
- Mode selector + shared weights; UI can swap fields later if modes diverge (none required now).
- Wire linear → HiGHS; non-linear → Ipopt on current OCP.
- Startup Ipopt probe; grey-out when fail/unavailable.
- Runtime failure notification + log.
- Docs/config strings; tests for probe, grey-out, both modes, failure path.
- Replace/supersede legacy `mpc_solver` with mode selector.

**Out**
- Phase 7 / CasADi / acados / HPIPM.
- User-facing solver picker.
- Silent fallback to the other mode or another solver.
- Multi-user migration campaigns.
- Mandatory mode-specific option sets in v1.

## Decisions
| Topic | Decision |
|-------|----------|
| Modes | `linear` \| `non-linear` |
| Default | `linear` |
| Linear solver | HiGHS only |
| Non-linear solver | Ipopt only |
| OCP | Identical weights/costs/constraints; dynamics linearised vs nonlinear |
| Config UX | Mode selector at top; fields may depend on mode if needed later |
| Ipopt missing/broken | Restart probe; grey out non-linear |
| Mid-cycle NMPC fail | Fail clearly; log + persistent notification → switch to linear |
| Guidance | General compute vs fidelity/accuracy |

## Inputs (supportive — not substitutes for decisions above)
- Research: `docs/agents/RESEARCH-swd-239-dual-mode-mpc.md`
- Map: `docs/agents/ROADMAP-dual-mode-mpc.md` ([SWD-238](https://marcusknielsen.atlassian.net/browse/SWD-238))

## Acceptance criteria
- Default mode is linear; HiGHS path works as today for linear.
- With Ipopt probe pass: non-linear selectable; solve uses nonlinear dynamics + Ipopt.
- With probe fail: non-linear visible but disabled/greyed out.
- Mid-cycle NMPC failure: no silent mode switch; persistent notification + log instruct switch to linear.
- Help text states the general trade-off.
- Automated tests cover probe pass/fail, mode wiring, and failure notification path (as far as HA test harness allows).

## Work packages
1. [SWD-241](https://marcusknielsen.atlassian.net/browse/SWD-241) — Config/UI: MPC mode selector, help text, grey-out
2. [SWD-243](https://marcusknielsen.atlassian.net/browse/SWD-243) — Ipopt capability probe on restart
3. [SWD-242](https://marcusknielsen.atlassian.net/browse/SWD-242) — Controller: linear=HiGHS, non-linear=Ipopt, shared weights
4. [SWD-244](https://marcusknielsen.atlassian.net/browse/SWD-244) — Runtime NMPC failure: log + persistent notification
5. [SWD-245](https://marcusknielsen.atlassian.net/browse/SWD-245) — Tests and docs for dual-mode MPC

## Open items
- Exact notification wording (implement can draft; polish in review).
- Exact probe problem size (small fixed NLP).
- Whether any existing option is mode-gated in v1 (none required).

## Constraints
- Stay on current OCP — no Phase 7 rewrite.
- No silent solver or mode fallback.
- Prefer making Ipopt work consistently; grey-out is for genuine unavailability/probe failure.

## Tracker
- Provider: jira
- Story: [SWD-238](https://marcusknielsen.atlassian.net/browse/SWD-238)
- Task: [SWD-240](https://marcusknielsen.atlassian.net/browse/SWD-240)
- Sub-tasks: SWD-241, SWD-243, SWD-242, SWD-244, SWD-245
- Branch: `cursor/swd-240-dual-mode-mpc-d0ba`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/539
- SHA: *(update on push)*

## Next
`/implement SWD-240` — Build per this plan on the same delivery branch/PR

(Alternate: `/ship SWD-240` to finish remaining through Done.)

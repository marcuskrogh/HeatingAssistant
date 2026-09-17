# Implementation plan: 2R2C wall physics in estimation

## Summary
- Live CD-EKF wall/mass estimates can leave any physically plausible range (e.g. ~6 °C while outdoor is >10 °C and indoor ~22 °C).
- Teach the **estimator and system identification** the 2R2C wall energy balance: fuse algebraic steady state as a Kalman measurement of the wall, and use the same $T_w^{\mathrm{ss}}(\theta)$ as the PE MAP mean and open-loop path residual. No hardcoded lower bound and no post-clip.

## Scope / Decisions / Constraints
- Algebraic SS (no inter-room flow): $T_w^{\mathrm{ss}}=\rho T_a+(1-\rho)T_{\mathrm{out}}+Q_{\mathrm{wall}}/(g_{\mathrm{aw}}+g_{\mathrm{wout}})$, $\rho=g_{\mathrm{aw}}/(g_{\mathrm{aw}}+g_{\mathrm{wout}})$.
- Live path: wall diffusion is `0.1 ×` air `σ_w`; after each CD-EKF air update, Joseph-form wall measurement with $R=\sigma_{\mathrm{lag}}^2+(Q_{\mathrm{wall}}/g_{\mathrm{sum}})^2$.
- PE: Tw0 stays in the safety box −30…60 °C only; MAP mean is $T_w^{\mathrm{ss}}(\theta)$ at dataset-start air/outdoor/solar anchors (moves with splits/UA/solar). N-step penalty is $\|T_w-T_w^{\mathrm{ss}}(\theta)\|^2$ with gradient through `sx` and $\partial\mu/\partial\theta$.
- Same fusion on sysid replay EKF and leading-window initial-state EKF.
- Out of scope: extra RC nodes, measuring walls, changing the room-plot series style, retuning MPC weights, clipping.

## Classification
- Class: bug
- Confidence: high
- Why: reconstructed wall temperature is wrong; the 2R2C wall row already implies the mix.

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: model
  - sandbox: none
- Chain: model → architect → implement → test → restructure → review → ship
- Rationale: Defect in estimator/PE; formulation is recorded in MODEL.md; localized engine change.

## Inputs
- Research: none
- Model: docs/agents/MODEL-wall-envelope.md
- Sandbox: none

## Pass criteria
- After a live CD-EKF step that starts from an unphysical wall, each room’s wall estimate moves toward that step’s $T_w^{\mathrm{ss}}$ (not toward a hardcoded floor such as $\min(T_a,T_{\mathrm{out}})-1.5$).
- SDE wall diffusion equals `WALL_PROCESS_NOISE_FRACTION ×` air diffusion (same q-scale).
- PE `t_wall_init` MAP mean for a segment is $T_w^{\mathrm{ss}}(\theta)$ of that segment’s first air/outdoor/solar sample.
- N-step PE SSE increases when a simulated wall node leaves $T_w^{\mathrm{ss}}$; on-SS walls add no penalty.
- The overnight case Tw=6 °C, Ta=22 °C, Tout=11 °C is pulled toward the RC mix (not treated as feasible, not clipped to ~9.5 °C).
- `heatingassistant.engine.wall_constraints` is gone (no clip/envelope APIs).

## Work packages
1. EKF wall SS fusion and reduced wall process noise — SWD-565
2. PE Tw0 MAP and trajectory residual toward $T_w^{\mathrm{ss}}(\theta)$ — SWD-566
3. Tests, THEORY, CalVer, changelog, App sync — SWD-567

## Open items
- None.

## Tracker
- Provider: jira
- Task: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564)
- Sub-tasks: [SWD-565](https://marcusknielsen.atlassian.net/browse/SWD-565), [SWD-566](https://marcusknielsen.atlassian.net/browse/SWD-566), [SWD-567](https://marcusknielsen.atlassian.net/browse/SWD-567)
- Relates: [SWD-554](https://marcusknielsen.atlassian.net/browse/SWD-554)
- Branch: `cursor/swd-564-wall-envelope-a891`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/689
- Classification: bug
- Workflow: fix-fast

## Next
`/test SWD-564` — Dedicated test phase on PR https://github.com/marcuskrogh/HeatingAssistant/pull/689

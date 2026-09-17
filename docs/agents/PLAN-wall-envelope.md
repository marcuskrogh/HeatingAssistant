# Implementation plan: Physical envelope for wall temperature

## Summary
- Live CD-EKF wall/mass estimates can leave any physically plausible range (e.g. ~6 °C while outdoor is >10 °C and indoor ~22 °C).
- Constrain the hidden wall node to an air–outdoor envelope in **estimation** (reduced wall process noise + posterior projection) and **system identification** (data-dependent Tw0 box + N-step open-loop penalty).

## Scope / Decisions / Constraints
- Envelope per room, per sample: `min(Ta, Tout) − 1.5 K ≤ Tw ≤ max(Ta, Tout) + 8 K`. Missing outdoor collapses onto air. Absolute safety box remains −30…60 °C.
- Live path: wall diffusion is `0.1 ×` air `σ_w` (same per-room q-scale); after each CD-EKF update, project wall nodes. Covariance is not Joseph-reset after the clip.
- PE: Tw0 bounds from the first sample of each dataset segment; quadratic envelope penalty on N-step open-loop wall paths with gradient through `sx`.
- Same projection on sysid replay EKF and leading-window initial-state EKF.
- Out of scope: extra RC nodes, measuring walls, changing the room-plot series style, retuning MPC weights.

## Classification
- Class: bug
- Confidence: high
- Why: reconstructed wall temperature is wrong; expected physical envelope is knowable.

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
- Rationale: Defect in estimator/PE; formulation of the envelope is recorded in MODEL.md; localized engine change, cheapest review that still covers risk.

## Inputs
- Research: none
- Model: docs/agents/MODEL-wall-envelope.md
- Sandbox: none

## Pass criteria
- After a live CD-EKF step, each room’s wall estimate lies in that step’s air–outdoor envelope.
- SDE wall diffusion equals `WALL_PROCESS_NOISE_FRACTION ×` air diffusion (same q-scale).
- PE `t_wall_init` box for a segment is the envelope of that segment’s first air/outdoor sample (intersected with the safety box).
- N-step PE SSE increases when a simulated wall node is outside the envelope; in-envelope walls add no penalty.
- The overnight case Tw=6 °C, Ta=22 °C, Tout=11 °C is clipped/penalised (not treated as feasible).

## Work packages
1. EKF wall envelope projection and reduced wall process noise — SWD-565
2. PE Tw0 envelope bounds and trajectory penalty — SWD-566
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

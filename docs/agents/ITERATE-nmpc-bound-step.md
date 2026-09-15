# Iterate: NMPC schedule bounds at the predicted sample time

## Prior work
- Task: [SWD-545](https://marcusknielsen.atlassian.net/browse/SWD-545)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/683 (`75cd35f4`)
- Spec context: docs/agents/PLAN-nmpc-schedule-horizon.md

## Problem
- After SWD-545, scheduled comfort/off bands are on the horizon, but the OCP still looks reactive at a period change.
- MeanOCP / linear QP penalise air **after** each step (state at `now+(k+1)·dt`). `_comfort_bounds_fast` uses trajectory sample `k` (`now+k·dt`). Room plots already draw the band at wall-clock `now+(k+1)·dt` (trajectory `k+1`).
- Live `_schedule_control_context` builds `n_fast` samples; Tuning preview already builds `n_fast+1`.

## Clarifications
- Comfort periods are projected (`compute_control_trajectory` / `control_params_at`). The remaining defect is sample alignment, not a missing schedule payload.

## Pass criteria
- OCP/QP `t_min`/`t_max` at step `k` use the schedule at `now+(k+1)·dt`.
- The first predicted sample that lands on a comfort start (or comfort-to-comfort change) already has that period’s corridor.
- Live control trajectory length is `n_fast+1`, matching preview.
- Off-period frost floor and u-hold (no later comfort → pin `u=0`) stay as SWD-545.
- Spec-lock tests fail if the bound is still one sample late.

## Out of scope
- Schedule UI; frost live trip; window-open override; retuning `rho`.

## Work packages
1. Shift horizon output bounds (and linear `q` scale) to trajectory index `k+1`; live `n_fast+1` — SWD-549
2. Tests, CalVer, changelog, App sync — SWD-550

## Tracker
- Task: [SWD-548](https://marcusknielsen.atlassian.net/browse/SWD-548)
- Relates: [SWD-545](https://marcusknielsen.atlassian.net/browse/SWD-545)
- Sub-tasks: [SWD-549](https://marcusknielsen.atlassian.net/browse/SWD-549), [SWD-550](https://marcusknielsen.atlassian.net/browse/SWD-550)
- Branch: `swd-548-nmpc-bound-step`

## Next
`/test SWD-548` — Dedicated testing phase, then harden and code review

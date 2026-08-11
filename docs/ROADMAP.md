# Roadmap: Estimation history hole (plots + control OK)

## Destination

Parameter-estimation observation memory stays continuous whenever control and
room temperatures are advancing; horizon load uses the same durable store.

## Notes

- Room view temperature plots stayed continuous during the incident.
- Controller / MPC had no problem.
- Only the persisted memory used for parameter estimation / EKF loading showed
  a multi-hour hole.
- Plot history writers: ticker + MQTT tags + control.
- ID history writer: completed `run_control_cycle` only
  (`_take_identification_sample` → `id_history` JSONL).
- `resolve_history(horizon_hours)` reads the in-memory buffer only; explicit
  `window_start` / `window_end` merges `id_history` JSONL.
- Research (SWD-319): both load-path defect and write-path asymmetry are
  code-proven; today’s H-write vs H-load needs on-device JSONL check (see
  `docs/agents/RESEARCH-estimation-history-hole.md`).

## Route

| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Discriminate: id_history JSONL missing rows vs horizon load ignoring disk | research | — | Done | [SWD-319](https://marcusknielsen.atlassian.net/browse/SWD-319) |
| 2 | Fix `resolve_history(horizon_hours)` to merge id_history JSONL | define | — | To Do | [SWD-320](https://marcusknielsen.atlassian.net/browse/SWD-320) |
| 3 | Align ID sample write with plot cadence (durable-first append) | define | SWD-319 | To Do | [SWD-318](https://marcusknielsen.atlassian.net/browse/SWD-318) |
| 4 | Surface ID append / buffer–disk lag on System Status | define | SWD-320, SWD-318 | To Do | [SWD-317](https://marcusknielsen.atlassian.net/browse/SWD-317) |

## Cleared so far

- App process-down / total ticker death — ruled out by continuous room plots
- Controller / MPC failure — ruled out by operator confirmation
- Plot-history persistence (SWD-281 path) — working for this incident
- [SWD-319 research](https://marcusknielsen.atlassian.net/browse/SWD-319) — load-path defect + write asymmetry code-proven; incident discriminator needs `/data` JSONL

## Not yet specified

- Exact root cause of *today’s* window (JSONL missing vs horizon load-only) —
  operator check in RESEARCH brief

## Out of scope

- Controller / MPC behaviour changes
- CD-EKF math for long gaps (uncertainty growth is expected given a hole)
- Plot-history persistence redesign

## Tracker

- Provider: jira (`SWD`)
- Story (map): [SWD-316](https://marcusknielsen.atlassian.net/browse/SWD-316)
- Tasks: SWD-319, SWD-320, SWD-318, SWD-317
- Research artifact: docs/agents/RESEARCH-estimation-history-hole.md

## Next

`/define SWD-320` — fix `resolve_history(horizon_hours)` to merge `id_history` JSONL (code-proven load defect).

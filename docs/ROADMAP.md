# Roadmap: Estimation history hole (plots + control OK)

## Destination

Parameter-estimation observation memory stays continuous whenever control and
room temperatures are advancing; horizon load uses the same durable store.

## Notes

- Room view temperature plots stayed continuous during the incident.
- Controller / MPC had no problem.
- Research (SWD-319): load-path defect + write asymmetry code-proven.
- SWD-320 shipped: horizon always merges JSONL (Option A).
- SWD-318 define: Option B — ID samples on ticker + `update_tag` + control;
  durable-first append. PLAN `docs/agents/PLAN-id-sample-plot-cadence.md`.

## Route

| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Discriminate: id_history JSONL missing rows vs horizon load ignoring disk | research | — | Done | [SWD-319](https://marcusknielsen.atlassian.net/browse/SWD-319) |
| 2 | Fix `resolve_history(horizon_hours)` to merge id_history JSONL | define | — | Done | [SWD-320](https://marcusknielsen.atlassian.net/browse/SWD-320) |
| 3 | Align ID sample write with plot cadence (durable-first append) | define | SWD-319 | In Review | [SWD-318](https://marcusknielsen.atlassian.net/browse/SWD-318) |
| 4 | Surface ID append / buffer–disk lag on System Status | define | SWD-320, SWD-318 | To Do | [SWD-317](https://marcusknielsen.atlassian.net/browse/SWD-317) |

## Cleared so far

- App process-down / total ticker death — ruled out by continuous room plots
- Controller / MPC failure — ruled out by operator confirmation
- Plot-history persistence (SWD-281 path) — working for this incident
- [SWD-319 research](https://marcusknielsen.atlassian.net/browse/SWD-319) — load-path defect + write asymmetry code-proven
- [SWD-320](https://marcusknielsen.atlassian.net/browse/SWD-320) shipped — horizon JSONL merge
- SWD-318 implement: Option B writers + durable-first on PR #603.

## Not yet specified

- Exact root cause of *today’s* window (JSONL missing vs horizon load-only) —
  operator check in RESEARCH brief

## Out of scope

- Controller / MPC behaviour changes
- CD-EKF math for long gaps
- Plot-history persistence redesign

## Tracker

- Provider: jira (`SWD`)
- Story (map): [SWD-316](https://marcusknielsen.atlassian.net/browse/SWD-316)
- Tasks: SWD-319, SWD-320, SWD-318, SWD-317
- Research: docs/agents/RESEARCH-estimation-history-hole.md
- Plan (SWD-320): docs/agents/PLAN-resolve-history-horizon-jsonl.md
- Plan (SWD-318): docs/agents/PLAN-id-sample-plot-cadence.md

## Next

`/review-fix SWD-318` — focused review on PR #603 (ID sample plot cadence).

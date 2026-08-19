# Roadmap: Hierarchical nonlinear OCP + P tracking

## Destination
A two-rate controller. A nonlinear OCP on a slow grid (study start: **1 h**) produces the nominal path `(T_ref(t), u_ref(t))`. Between solves, a P-controller with feedforward `u = clip(u_ref(t) + K_p (T_ref(t) − T_filtered))` tracks that path. One heater per room. A failed OCP keeps the last path. Five hours of consecutive failures (including timeouts) set every heater to `u = 0` and raise a persistent notification. The linearised QP is not in the happy path. No production deploy until an offline study picks a feasible OCP period.

## Notes
- NMPC already absorbs nominal solar, outdoor, and price; P only rejects residuals.
- Interpolate `(T_ref, u_ref)` to each P step; do not zero-order-hold for a whole hour.
- NMPC runs in the App process, not Home Assistant Core (`SWD-254`).
- First cut: one heater per room. Inter-room `R_ij` lives in the OCP only.
- Failed solve includes timeout. Watchdog shut-off is `u = 0` (free, bounded).

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Formulate hierarchical NMPC + P-FF, hold/fail/watchdog | model | — | To Do | [SWD-393](https://marcusknielsen.atlassian.net/browse/SWD-393) |
| 2 | Offline NMPC period + closed-loop P eval (may kill hourly) | sandbox | SWD-393 | To Do | [SWD-394](https://marcusknielsen.atlassian.net/browse/SWD-394) |
| 3 | Production NMPC + P, single heater, last-plan hold, 5 h → off + notify | define | SWD-394 | To Do | [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395) |

## Cleared so far
- Linearised QP can command max heat while the nonlinear forecast overheats; that planner is out of the happy path.
- Tracker is P + `u_ref`, not PI/LQR/linear MPC. Error is `T_ref − T_filtered`.
- OCP miss → last path. Five hours of misses → heaters off (`u = 0`) + persistent notification.

## Not yet specified
- OCP period (1 h is the study default; 15 min and 2 h as brackets).
- Horizon length, solver, per-solve timeout, `K_p`.
- P cadence relative to today’s 15 min ticker.

## Out of scope
- Split-range / several heaters in one room.
- Linearised QP as a fallback.
- I-term or D-term on the fast loop.
- Parameter-estimation changes.
- NMPC on the Home Assistant Core event loop.

## Tracker
- Provider: jira (`SWD`)
- Story (map): [SWD-392](https://marcusknielsen.atlassian.net/browse/SWD-392)
- Tasks: [SWD-393](https://marcusknielsen.atlassian.net/browse/SWD-393), [SWD-394](https://marcusknielsen.atlassian.net/browse/SWD-394), [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395)
- Delivery branch (no explore/model PR): `cursor/swd-395-nmpc-p-tracker-46be`

## Next
`/model SWD-393` — lock the hierarchical OCP + P-FF math so the sandbox can score sample intervals.

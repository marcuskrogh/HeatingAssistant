# Roadmap: Hierarchical nonlinear OCP + P tracking

## Destination
A two-rate controller. A nonlinear OCP on a slow grid (study start: **1 h**) produces the nominal path `(T_ref(t), u_ref(t))`. Between solves, a P-controller with feedforward `u = clip(u_ref(t) + K_p (T_ref(t) − T_filtered))` tracks that path. One heater per room. A failed OCP keeps the last path. Five hours of consecutive failures (including timeouts) set every heater to `u = 0` and raise a persistent notification. The linearised QP is not in the happy path. No production deploy until an offline study picks a feasible OCP period.

## Notes
- NMPC already absorbs nominal solar, outdoor, and price; P only rejects residuals.
- On each slow interval: `u_ref` is `u*`; `T_ref` is the mean air path under that `u*` (not linear interpolation).
- EKF uses the actual P command; P uses `T_hat`. `K_p` is on the heater (one gain, heat and cool).
- NMPC is SciPy in the App process, not Home Assistant Core (`SWD-254`).
- First cut: one heater per room. Inter-room `R_ij` lives in the OCP only.
- Failed solve includes timeout. Watchdog shut-off is `u = 0` (free, bounded).
- Defaults: `T_s` = 15 min, `T_H` = 36 h, both configurable. `T_NMPC` study start 1 h.

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Formulate hierarchical NMPC + P-FF, hold/fail/watchdog | model | — | Done | [SWD-393](https://marcusknielsen.atlassian.net/browse/SWD-393) |
| 2 | Offline NMPC period + closed-loop P eval (may kill hourly) | sandbox | SWD-393 | To Do | [SWD-394](https://marcusknielsen.atlassian.net/browse/SWD-394) |
| 3 | Production NMPC + P, single heater, last-plan hold, 5 h → off + notify | define | SWD-394 | To Do | [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395) |

## Cleared so far
- Linearised QP can command max heat while the nonlinear forecast overheats; that planner is out of the happy path.
- Tracker is P + `u_ref`, not PI/LQR/linear MPC. Error is `T_ref − T_hat`.
- OCP miss → last path. Five hours of misses → heaters off (`u = 0`) + persistent notification.
- [SWD-393 model](https://marcusknielsen.atlassian.net/browse/SWD-393) — hierarchical mean OCP + P-FF. Artifact `docs/agents/MODEL-nmpc-p-ff.md`.

## Not yet specified
- Feasible `T_NMPC` (sandbox brackets: 15 min, 1 h, 2 h).
- SciPy flavour, timeout, iteration cap, numeric default `K_p`.

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
- Delivery branch (no explore/model/sandbox PR): `cursor/swd-395-nmpc-p-tracker-46be`
- Model: `docs/agents/MODEL-nmpc-p-ff.md`

## Next
`/sandbox SWD-394` — offline NMPC period + closed-loop P eval (may kill hourly).

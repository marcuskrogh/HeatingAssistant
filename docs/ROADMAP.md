# Roadmap: Parameter estimation effectiveness and guidance

## Destination
A per-room 2R2C estimator that is **robust and reliable** on household-like rooms: low hold-out **open-loop** indoor-air error (the score that matters for MPC). On synthetic data it should also recover true parameters when the extras are known. Working setup: 2R2C plant and 2R2C estimator (independent; either can change later).

## Notes
- Sensors: standard indoor climate (e.g. IKEA TIMMERFLOTE); no extra wall-temperature sensors. Window/door **contacts** may be used (product already has them).
- Parameter estimation stays per-room. Occupancy is extra air-node heat; an open window/door is extra outdoor exchange — not the same term.
- Primary score is hold-out open-loop air RMSE/MAE/R² (SWD-332). Physical θ error is a secondary check.
- Fit criterion stays **open-loop output-error**. Kalman/PED one-step likelihood lost on that score in SWD-332.
- Synthesise now; real home recordings come later as a separate check.
- 2R2C is the working hypothesis, not a lock. A 1R1C estimator on the current 2R2C plant lost badly on hold-out open-loop air RMSE (mean 4.2 °C vs 1.5 °C). Reevaluate structure only if extras still fail the val bar; do not drop to 1R1C to make \(C,R\) unique.

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Diagnose current PE and survey applicable improvements | research | — | Done | [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324) |
| 2 | Formulate PE treatment of hidden wall temperature and staged windows | model | — | Done | [SWD-325](https://marcusknielsen.atlassian.net/browse/SWD-325) |
| 3 | Offline PE combined vs separated/staged benchmark | define | SWD-325 | Done | [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326) |
| 4 | Synthesise household-like single-room traces and identify robust PE approaches | research | — | Done | [SWD-328](https://marcusknielsen.atlassian.net/browse/SWD-328) |
| 5 | Offline PE robustness analysis on synthetic household-like data | define | SWD-328 | In Review | [SWD-329](https://marcusknielsen.atlassian.net/browse/SWD-329) |
| 6 | Offline PE validation open-loop prediction accuracy | tweak | SWD-329 | In Review | [SWD-332](https://marcusknielsen.atlassian.net/browse/SWD-332) |
| 7 | Survey PE method families for grey-box 2R2C | research | — | Done | [SWD-331](https://marcusknielsen.atlassian.net/browse/SWD-331) |
| 8 | Contact-gated extra UA + occupancy disturbance (no envelope lock) | model | — | Done | [SWD-334](https://marcusknielsen.atlassian.net/browse/SWD-334) |
| 9 | Robust open-loop PE for household extras | define | SWD-334 | To Do | [SWD-335](https://marcusknielsen.atlassian.net/browse/SWD-335) |

## Cleared so far
- [SWD-324 research](https://marcusknielsen.atlassian.net/browse/SWD-324) — joint \(T_w\) in \(\theta\); unused leading-window PE; literature on hidden state, regimes, excitation. Artifact `docs/agents/RESEARCH-pe-effectiveness.md`.
- [SWD-325 model](https://marcusknielsen.atlassian.net/browse/SWD-325) — \(T_{w}(t_{0})\) as PE decision; \(T_{w}(t)\) not; 24 h box \(\pm 25\%\) width. Artifact `docs/agents/MODEL-pe-hidden-tw.md`.
- [SWD-326 define](https://marcusknielsen.atlassian.net/browse/SWD-326) — combined vs separated/staged bake-off. Artifact `docs/agents/REPORT-pe-dataset-separation.md`.
- [SWD-328 research](https://marcusknielsen.atlassian.net/browse/SWD-328) — occupancy ≠ window; extras belong in the estimator. Artifact `docs/agents/RESEARCH-pe-robustness-household.md`.
- [SWD-331 research](https://marcusknielsen.atlassian.net/browse/SWD-331) — stochastic ML for physical \(\theta\); this destination scores open-loop fit, so keep OE. Artifact `docs/agents/RESEARCH-pe-methods.md`.
- [SWD-332 analysis](https://marcusknielsen.atlassian.net/browse/SWD-332) — val open-loop RMSE. Best harness: `window_ua`+open_loop (mean 0.83 °C, −46% vs `today_combined` 1.53 °C). Night-lock occupancy and Kalman/PED are regressions. Report `docs/agents/REPORT-pe-robustness-household.md`.
- [SWD-334 model](https://marcusknielsen.atlassian.net/browse/SWD-334) — identify contact-gated \(UA_{\mathrm{open}}\) (one scalar/room); replace 24 h `internal_gain` with day-gated \(q_{\mathrm{day}}\) under the existing MAP prior; keep OE; no \(C,R\) lock. Artifact `docs/agents/MODEL-pe-contact-ua-occupancy.md`.

## Not yet specified
- Product vs harness-only for the first robust estimator — decided: product (SWD-335).
- Your home recordings as a later check.
- In-app PE guidance.
- A different RC structure if extras still miss the val bar (1R1C estimator already checked: worse free-run, not a PE shortcut).
- Numerical \(\Delta t_{\min}\) for interior gaps.
- Estimator-family bake-off (collocation OE, full CTSM) — parked; OE stays unless the val bar fails.

## Out of scope
- A multi-room plant (PE remains per-room).
- Additional sensors beyond indoor climate + existing window/door contacts.
- MPC / controller behaviour changes.
- Night-staged locking of \(C,R\) (`occupancy_tv`) — ruled out by SWD-332.
- Replacing production open-loop OE with Kalman/PED as the fit objective — ruled out by SWD-332 for this score.

## Tracker
- Provider: jira (`SWD`)
- Story (map): [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Tasks: [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324), [SWD-325](https://marcusknielsen.atlassian.net/browse/SWD-325), [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326), [SWD-328](https://marcusknielsen.atlassian.net/browse/SWD-328), [SWD-329](https://marcusknielsen.atlassian.net/browse/SWD-329), [SWD-330](https://marcusknielsen.atlassian.net/browse/SWD-330), [SWD-331](https://marcusknielsen.atlassian.net/browse/SWD-331), [SWD-332](https://marcusknielsen.atlassian.net/browse/SWD-332), [SWD-333](https://marcusknielsen.atlassian.net/browse/SWD-333), [SWD-334](https://marcusknielsen.atlassian.net/browse/SWD-334), [SWD-335](https://marcusknielsen.atlassian.net/browse/SWD-335), [SWD-336](https://marcusknielsen.atlassian.net/browse/SWD-336), [SWD-337](https://marcusknielsen.atlassian.net/browse/SWD-337), [SWD-338](https://marcusknielsen.atlassian.net/browse/SWD-338), [SWD-339](https://marcusknielsen.atlassian.net/browse/SWD-339)
- Research: `docs/agents/RESEARCH-pe-effectiveness.md`, `docs/agents/RESEARCH-pe-robustness-household.md`, `docs/agents/RESEARCH-pe-methods.md`
- Model: `docs/agents/MODEL-pe-hidden-tw.md`, `docs/agents/MODEL-pe-contact-ua-occupancy.md`
- Reports: `docs/agents/REPORT-pe-dataset-separation.md`, `docs/agents/REPORT-pe-robustness-household.md`

## Next
`/implement SWD-335` — identified contact-gated UA + PE data-coverage checklist.

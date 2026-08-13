# Roadmap: Parameter estimation effectiveness and guidance

## Destination
Parameter estimation is more effective for configured 2R2C rooms from air-node measurements only, and the product guides the user on what to do — and how — to arrive at a better model.

## Notes
- Sensors: standard indoor climate (e.g. IKEA TIMMERFLOTE); no extra wall-temperature sensors.
- Keep the 2R2C structure; improve methods, data use, experiments, and in-app guidance.
- Leading suspect, not settled cause: unmeasured wall-node \(T_w\) during fitting.
- Regime examples (nights for non-solar parameters; sun + heater off for solar gain) are ideas to evaluate, not a committed design.
- Effectiveness is the primary bar; guidance is how users reach it.

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Diagnose current PE and survey applicable improvements | research | — | Done | [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324) |

## Cleared so far
- [SWD-324 research](https://marcusknielsen.atlassian.net/browse/SWD-324) — joint \(T_w\) in \(\theta\) (leading-window reconstruction unused by PE); literature supports hidden-state handling, regime/excitation, and operator guidance. Artifact `docs/agents/RESEARCH-pe-effectiveness.md`.

## Not yet specified
- Which method changes to make (wall-node / initial state, regime-batched parameters, experiment design, estimator implementation).
- What in-app guidance looks like (actions, timing, when a model is good enough).
- Whether a `/model` step is needed before buildable slices.
- Passive data windows vs user-run identification experiments.

## Out of scope
- A different thermal model family than 2R2C.
- Additional sensors beyond standard indoor temperature/humidity.
- MPC / controller behaviour changes.

## Tracker
- Provider: jira (`SWD`)
- Story (map): [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Tasks: [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324)
- Research: `docs/agents/RESEARCH-pe-effectiveness.md`

## Next
`/explore SWD-323` — Rechart from SWD-324 evidence: graduate method and guidance Tasks (likely including `/model` on hidden \(T_w\) / staged windows).

# Roadmap: Parameter estimation effectiveness and guidance

## Destination
Parameter estimation is more effective for configured 2R2C rooms from air-node measurements only, and the product guides the user on what to do — and how — to arrive at a better model.

## Notes
- Sensors: standard indoor climate (e.g. IKEA TIMMERFLOTE); no extra wall-temperature sensors.
- Keep the 2R2C structure; improve methods, data use, experiments, and in-app guidance.
- SWD-324: production PE jointly fits wall initial temperature \(T_{w,0}\) with \(R,C\); leading-window reconstruction exists but is unused by PE. Literature supports reconstructing the hidden state with structural parameters held, regime/excitation choices, and operator guidance.
- Night vs solar windows remain ideas for `/model` / `/define`, not a committed design.
- Effectiveness is the primary bar; guidance is how users reach it.
- One delivery unit: method + guidance share [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326).

## Route
| Order | Task | Type | Blocked by | Status | Issue |
|-------|------|------|------------|--------|-------|
| 1 | Diagnose current PE and survey applicable improvements | research | — | Done | [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324) |
| 2 | Formulate PE treatment of hidden wall temperature and staged windows | model | — | To Do | [SWD-325](https://marcusknielsen.atlassian.net/browse/SWD-325) |
| 3 | Improve PE effectiveness and in-app guidance | define | SWD-325 | To Do | [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326) |

## Cleared so far
- [SWD-324 research](https://marcusknielsen.atlassian.net/browse/SWD-324) — joint \(T_w\) in \(\theta\); unused leading-window PE; literature on hidden state, regimes, excitation, guidance. Artifact `docs/agents/RESEARCH-pe-effectiveness.md`.

## Not yet specified
- Estimator equations (how \(T_w\) / \(x_0\) is reconstructed vs fitted; whether regimes are in the estimator or only in data selection) — [SWD-325](https://marcusknielsen.atlassian.net/browse/SWD-325) `/model`, not pre-answered.
- What in-app guidance looks like (actions, timing, when a model is good enough) — [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326) `/define`.
- First-slice breadth: wall-state handling only vs also regime-batched parameters — after model, on define.
- Passive data windows vs user-run identification experiments — `/define`.
- Restore prediction-error (PED) fitting vs keep simulation MSE — `/model` / `/define`.

## Out of scope
- A different thermal model family than 2R2C.
- Additional sensors beyond standard indoor temperature/humidity.
- MPC / controller behaviour changes.

## Tracker
- Provider: jira (`SWD`)
- Story (map): [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Tasks: [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324), [SWD-325](https://marcusknielsen.atlassian.net/browse/SWD-325), [SWD-326](https://marcusknielsen.atlassian.net/browse/SWD-326)
- Research: `docs/agents/RESEARCH-pe-effectiveness.md`
- Model (pending): `docs/agents/MODEL-pe-hidden-tw.md` on the SWD-326 delivery branch
- Branch: `cursor/swd-323-pe-effectiveness-747e` (charting / research; no map-only PR)
- PR: —

## Next
`/model SWD-325` — Formulate PE treatment of hidden wall temperature and staged windows: agree the 2R2C, air-only math before defining the product slice.

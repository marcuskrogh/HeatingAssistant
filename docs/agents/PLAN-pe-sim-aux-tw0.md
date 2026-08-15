# Implementation plan: PE simulation inputs, wall init, category copy

## Summary
- Parameter Estimation EKF / open-loop **Heating Input** and **Disturbances** charts must plot from identification history (stored dataset or selected window), not Home Assistant Recorder.
- On **Simulate**, compute the optimal wall initial temperature for that window, apply it to the free-run / EKF reconstruction, and show it in the Tw0 field.
- Category click-guides become short dataset-requirement descriptions (no numbered store/apply recipes).

## Scope / Decisions / Constraints
**In**
- Aux series (this room’s heater power [W], outdoor temp [°C], this room’s solar gain [W]) from the same ID-history records used for PE / simulate.
- Refresh those charts when a stored dataset is loaded or the recent / custom window changes, not only after a completed simulate.
- On EKF reconstruction and open-loop Simulate: wall-only optimisation of Tw0 on the history being simulated; apply it; persist so the UI field updates. Honor a locked Tw0.
- Stored datasets must not depend on leading live history (today they skip `_history_with_leading` and fall through a prefix-EKF path that estimates the wall at the wrong time).
- Diagnostic Tw0 prior mean = first measured air temperature (not air/outdoor midpoint); weaker MAP floor than PE so the window can move Tw0.
- Category guides: one or two sentences on what the dataset must contain. No `<ol>`, no save/Use/Apply instructions. Keep Open UA N/A copy.
- Tests, cache-bust on touched PE JS, CalVer, App package sync.

**Out**
- Changing PE structural-parameter optimisation (C, R, α, UA_open) except as needed to apply Tw0 on simulate.
- Changing category coverage thresholds or which samples enter the fit.
- Room-view DISTURBANCES / heating plots (not the PE page).
- Redesigning Saved Datasets / Use / recommended-estimation flow.

**Decisions**
- Aux plots source of truth = ID history (`u`, `d_outdoor`, `d_solar`), converted to watts with this room’s heat-source `max_power × power_scale` (and efficiency when present).
- Simulate always recomputes Tw0 unless the Tw0 field is locked.
- Class is **bug** (wrong display + wrong/unused IC); category copy is a bundled tweak.

**Constraints**
- Do not break existing PE coverage / recommended-estimation behaviour.
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.

## Classification
- Class: bug
- Confidence: high
- Why: heater/disturbance charts empty and open-loop IC wrong/unused are defects with known expected behaviour; copy is a small bundled delta

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: localized PE page + sysid services; no new layers or schema migration

## Inputs
- Research: none
- Model: `docs/agents/MODEL-pe-hidden-tw.md` (Tw0 is a per-window decision; simulate must apply an optimal IC)
- Prior: SWD-343 category guides (PR #615); SWD-321 disturbance points

## Acceptance criteria
1. Loading a stored dataset, or selecting a recent / custom PE window, shows heater power and outdoor/solar disturbances on the EKF and open-loop aux charts from that ID history even when HA Recorder has no samples for the range.
2. Pressing EKF reconstruction or open-loop Simulate computes an optimal Tw0 for the simulated history, applies it (predicted wall starts at that value), and writes it into the wall-temperature field unless Tw0 is locked.
3. Category click panels describe only what the dataset must contain; no numbered steps and no store/apply instructions. Open UA N/A still explains missing contacts.
4. Focused tests cover aux series from ID history, Tw0 apply on simulate (including dataset path), and guide copy. Existing PE coverage tests stay green. CalVer + App sync.

## Work packages
1. Plot PE heater/disturbances from ID history (SWD-345)
2. Compute and apply optimal Tw0 on Simulate (SWD-346)
3. Simplify PE category descriptions (SWD-347)
4. Tests, cache-bust, CalVer, App sync (SWD-348)

## Open items
- None — user report plus screenshots settle expected behaviour.

## Tracker
- Provider: jira
- Task: [SWD-344](https://marcusknielsen.atlassian.net/browse/SWD-344)
- Sub-tasks: [SWD-345](https://marcusknielsen.atlassian.net/browse/SWD-345), [SWD-346](https://marcusknielsen.atlassian.net/browse/SWD-346), [SWD-347](https://marcusknielsen.atlassian.net/browse/SWD-347), [SWD-348](https://marcusknielsen.atlassian.net/browse/SWD-348)
- Relates: [SWD-343](https://marcusknielsen.atlassian.net/browse/SWD-343)
- Branch: `cursor/swd-344-pe-sim-aux-tw0-bcb7`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/616
- Classification: bug
- Workflow: fix-fast

## Next
`/review-fix SWD-344` — Review and auto-fix per Workflow binding

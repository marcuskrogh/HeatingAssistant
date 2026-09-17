# Implementation plan: Physical wall EKF process noise

Canonical copy: project store `docs/wall-temp-reconstruction-plan.md`. Findings: store `docs/wall-temp-reconstruction.md`.

## Summary
- Room-view Wall dives toward ~5 °C on heater pulses while indoor air stays ~22 °C and outdoor is ~11–18 °C.
- Not integrator substeps. The CD-EKF uses the same `sigma_w` on the hidden wall as on air, so `K_w ≈ 1` and heating innovations dump into Tw.
- Scale wall diffusion so the same Watt-level process noise on air and wall gives `σ_Tw = σ_Ta · min(C_a/C_w, 1)`. Keep production on a single NMPC sample grid.

## Scope / Decisions / Constraints
**In**
- `HouseThermalSDE._build_sigma_matrix`: wall block uses capacitance-scaled `sigma_w`; air, filter, and offset blocks unchanged.
- Regression: 5 kW pulse, Ta held at 22 °C, Tout 14 °C → EKF Tw stays in the air–outdoor envelope (not ~5 °C).
- Update `test_sigma_is_scaled_identity`.
- TUNING.md: `sigma_w` is air intensity; wall is scaled by `C_a/C_w`.
- Dual tree, CalVer, changelog.

**Out**
- Changing `n_int_steps`, NMPC timing, or PE origin stride.
- Hard Tw clamps, PE Tw0 bound rewrite, re-enabling offset states.
- MQTT elapsed-`dt` EKF (separate).

**Decisions**
- Equal-Watt scaling, not a new UI knob.
- Clip `C_a/C_w` at 1 so a tiny wall node is not noisier than air.

## Classification
- Class: bug
- Confidence: high
- Why: live Wall is wrong; open-loop Tw and reduced `Q_wall` show the correct envelope

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
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship

## Pass criteria
- With default 2R2C / `sigma_w=0.1`, a 5 kW pulse while air is measured at 22 °C and Tout is 14 °C does not send EKF Tw below outdoor minus 2 K.
- Wall `σ` diagonal is `sigma_w * min(C_a/C_w, 1)` times the room Q-scale; air `σ` stays `sigma_w`.
- `n_int_steps` and NMPC `fast_substeps` stay as they are (no fake slow grid).

## Tracker
- Provider: jira
- Task: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564)
- Relates: [SWD-554](https://marcusknielsen.atlassian.net/browse/SWD-554)
- Classification: bug
- Workflow: fix-fast

## Next
`/test SWD-564` — heat-pulse spec lock and controller EKF suite on https://github.com/marcuskrogh/HeatingAssistant/pull/690

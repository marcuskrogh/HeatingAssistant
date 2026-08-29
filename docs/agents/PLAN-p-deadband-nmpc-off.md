# Implementation plan: P deadband when NMPC is near zero

## Summary
- Nonlinear model predictive control (NMPC) can set the feedforward
  `u_ref ≈ 0` for energy (night, wide comfort). The fast proportional (P)
  law still tracks `T_ref` and keeps heating or cooling.
- Gate a **temperature** deadband on P **only while** `|u_ref|` is below a
  near-zero threshold. Small NMPC preheat stays unconstrained. Static band;
  15-minute on/off chatter is accepted.

## Scope / Decisions / Constraints
**In**
- Fast P on an accepted NMPC path (`p_command` / `_p_command_vector`).
- House-level live Controller Tuning knobs (no NMPC rebuild):
  - `p_deadband` default **1.0 °C** (kelvin width around `T_ref`)
  - `u_ref_gate` default **0.02** (heater fraction)
- Law:

```
if |u_ref| < u_ref_gate and |T_ref − T_hat| <= p_deadband:
    u = 0
else:
    u = clip(u_ref + K_p (T_ref − T_hat), u_min, u_max)
```

- Inside the band, command **exact 0** (not residual `u_ref`).
- Heat and cool both use absolute `|u_ref|` and `|T_ref − T_hat|`.
- Tests, CalVer, changelog, App package sync. Dual tree.

**Out**
- NMPC solver, cost, accept/reject, timing.
- No-plan `comfort_fallback_command`.
- Extra hysteresis (enter/leave at different errors).
- Always-on deadband on `|u_ref + K_p e|` (would swallow small preheat).
- Per-heater copies of these knobs (not next to `p_gain`).
- Tuning preview NMPC re-solve (P-only; preview stays the slow plan).

**Decisions**
- Class is a **tweak**: intentional P-law energy delta on the existing
  tracker; NMPC-off stay-off is the new expected behaviour.
- Deadband is specified in °C so retuning `K_p` does not move the night
  threshold.
- Both knobs are live Tuning fields, same persist path as other MPC
  live weights.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional P-law delta; NMPC and no-plan fallback unchanged

## Workflow
- Template: delta-fast
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
- Chain: implement → test → harden → review-fix → ship
- Rationale: localized P-law + two Tuning fields; unit tests cover off /
  track / preheat; test and harden are the floor

## Inputs
- Research: none
- Model: `docs/agents/MODEL-nmpc-p-ff.md` (P + `u_ref` tracker)
- Sandbox: none
- Prior: SWD-395 hierarchical NMPC+P; Relates SWD-395

## Acceptance criteria
1. `|u_ref| < u_ref_gate` and `|T_ref − T_hat| <= p_deadband` → applied
   `u = 0`.
2. `|u_ref| < u_ref_gate` and `|T_ref − T_hat| > p_deadband` → usual P-law
   (including residual `u_ref`).
3. `|u_ref| >= u_ref_gate` (including small preheat) → usual P-law even
   when `|T_ref − T_hat|` is inside `p_deadband`.
4. Defaults 1.0 °C and 0.02; both persist via `update_controller_tuning`
   and appear on Controller Tuning as live fields.
5. `comfort_fallback_command` and the NMPC NLP are unchanged.
6. Fast suite passes. CalVer and App package synced.

## Work packages
1. Gated P deadband + live tuning keys (`nmpc_p.py`, facade, const, persist)
2. Controller Tuning knobs, tests, CalVer, changelog, App sync

## Open items
- None. UI labels can follow existing Tuning style
  (e.g. “P deadband (NMPC off)”, “NMPC-off gate”).

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-437](https://marcusknielsen.atlassian.net/browse/SWD-437)
- Sub-tasks: [SWD-438](https://marcusknielsen.atlassian.net/browse/SWD-438),
  [SWD-439](https://marcusknielsen.atlassian.net/browse/SWD-439)
- Branch: `cursor/swd-437-p-deadband-b77a`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/637
- Classification: tweak
- Workflow: delta-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/637

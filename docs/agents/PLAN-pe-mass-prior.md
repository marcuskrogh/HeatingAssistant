# Implementation plan: PE thermal-mass bounds and prior toward selected room size

## Summary
- Envelope thermal mass \(C\) often rides the open-loop PE upper bound
  (~500 MJ/K) when estimating a living-room-sized space, even after PE
  category coverage (SWD-335 / SWD-343 / SWD-344). The global box is ~55×
  the large-room size preset (9 MJ/K), and the default log-\(C\) MAP
  (\(\lambda=0.01\)) cannot compete with summed simulation SSE.
- Box each room’s \(C\) around the **initially selected** thermal mass
  (config / size preset) and add a MAP weight on \(\log C\) toward that
  prior, in the same family as the heater-scale prior that already fights
  the “C huge / R huge” ridge.

## Scope / Decisions / Constraints
**In**
- Production `KalmanMLEstimator.estimate()` log-mass bounds: per room,
  \(C \in [C_0 / 5,\; 5 C_0]\) intersected with the global
  \([10\,\mathrm{kJ/K},\; 500\,\mathrm{MJ/K}]\) safety box. \(C_0\) is the
  configured room `thermal_mass` (size preset or override) already used as
  `_log_mass_prior`.
- MAP term on \(\log C\): extra weight `_MASS_PRIOR_WEIGHT` (same structure
  as `_ALPHA_PRIOR_WEIGHT`) so a weakly identified window stays near the
  selected size instead of walking the ridge to the cap.
- Physics-informed warm start clips \(C\) into that same per-room box.
- Tests: helper bounds; degenerate / weakly excited window does not return
  the global 500 MJ/K cap; existing excited-data “moves off a wrong prior”
  behaviour still holds. CalVer + App package sync.

**Out**
- Changing \(R_{\mathrm{ext}}\), \(\alpha\), solar, splits, \(T_{w,0}\), or
  \(UA_{\mathrm{open}}\) bound families (except \(C\) warm-start clipping).
- Locking \(C\) from a night fragment (rejected in SWD-334 / SWD-335).
- Day-gated occupancy / new PE categories / UI size-preset copy.
- Guaranteeing a good open-loop fit on household data — this slice stops
  bound-slam of \(C\); remaining misfit is a later iterate.

**Decisions**
- **Yes, regularise toward the initially selected room size.** That prior
  already exists; it is too weak. Do not replace it with a new mean.
- **Yes, tighten PE limits for \(C\).** Relative factor 5 covers two size
  presets either way (living-room 9 MJ/K → 1.8–45 MJ/K) and blocks the
  55× run to 500 MJ/K. Global box remains the last-resort clip.
- Keep default \(\lambda=0.01\). Do not raise `_T_WALL_MIN_LAM`-style floor
  on \(C\) (that re-pins identification on excited windows). Extra MAP
  weight + relative box is the pair.
- Factor 5 and weight 16 are the production numbers (weight between
  excited-\(\alpha\) 4 and unexcited-\(\alpha\) 25).

**Constraints**
- Open-loop simulation MSE stays the objective. 2R2C unchanged.
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Dev-surface keys only in tracker / PLAN / PR — not in product UI copy.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional PE behaviour delta (tighter \(C\) box + stronger
  size-preset MAP); not a defect with a known correct numeric \(C\), and
  not a new product surface

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: formulation reuses existing MAP + L-BFGS-B box; localized
  estimator change. Efficiency-first: no model side path, not feature-heavy.

## Inputs
- Research: `docs/agents/RESEARCH-pe-effectiveness.md` (C/R ridge; light
  \(\lambda\) vs summed SSE)
- Model: `docs/agents/MODEL-pe-contact-ua-occupancy.md` (do not lock \(C,R\);
  keep OE + light MAP family)
- Prior: `_ALPHA_PRIOR_WEIGHT` / `_T_WALL_MIN_LAM` in
  `heatingassistant/engine/estimation/constants.py`; size presets in
  `heatingassistant/app/static/js/config/config-presets.js`

## Acceptance criteria
1. For a living-room prior \(C_0=9\times10^6\,\mathrm{J/K}\), PE log-mass
   upper bound is \(5 C_0\) (45 MJ/K), not 500 MJ/K.
2. On a weakly excited / degenerate window, estimated \(C\) does not return
   the global 500 MJ/K cap and stays inside \([C_0/5,\; 5 C_0]\).
3. On an excited window with a wrong prior, estimated \(C\) still moves a
   meaningful fraction off the prior (existing
   `test_default_prior_is_responsive_to_excited_data`).
4. Locked `thermal_mass` still pins \(C\) (lb = ub) even if the lock is
   outside the relative box.
5. CalVer bumped; App package synced.

## Work packages
1. Relative \(C\) box + MAP toward selected room size (SWD-350)
2. Tests, CalVer, App sync (SWD-351)

## Open items
- None for this slice. If fits remain poor after \(C\) stays interior,
  occupancy / excitation is a later iterate (SWD-335 deferred day-\(q\)).

## Tracker
- Provider: jira
- Story: — (Relates SWD-335, SWD-344; SWD-323 map already Done)
- Task: SWD-349
- Sub-tasks: SWD-350, SWD-351
- Branch: `cursor/swd-349-pe-mass-prior-6368`
- PR: —
- Classification: tweak
- Workflow: delta-fast

## Next
`/implement SWD-349` — Build per PLAN.md workflow binding (same branch/PR)

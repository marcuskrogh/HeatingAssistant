# Implementation plan: Low-pass solar gain

## Summary
- Cloud cover and irradiance forecasts still step the modelled solar gain in
  one sample (Kasten–Czeplak on a new `c`, or a new GHI slot).
- Apply a first-order low-pass to the **watt** solar-gain signal after
  geometry/intensity, so history, NOW, and the horizon move gradually.
- Keep per-step intensity selection (GHI vs cloud vs clear) unchanged
  (SWD-462 / SWD-432). The filter lags the watts, not the source choice.

## Scope / Decisions / Constraints
**In**
- Discrete EMA `α = 1 − exp(−Δt / τ)` with default `τ = 1800 s` (same
  order as the unused cloud-cover smoother), `Δt` = controller `dt`.
  τ is site-wide (`solar_gain_smoothing_tau_s`) and editable under
  Environment → Solar model (UI minutes; stored seconds). `τ ≤ 0` is
  identity.
- Seed on the first sample (no startup lag). Clamp watts to `≥ 0`.
- Walk the k = 0…N schedule causally; persist only k = 0 between `compute`
  / `solve_nmpc` cycles so the next NOW continues the live filter.
- Isolated `_forecast_solar` calls (tests) do not persist, so first k = 0
  still matches instantaneous cloud/GHI (SWD-462).
- Horizon k ≥ 1 is smoothed even on the first call (forecast cloud/GHI
  steps are the point).
- THEORY §3.4 note, CalVer `2026.09.5`, changelog, App package sync.

**Out**
- Filtering cloud cover instead of watts (already exists unused; does not
  catch GHI jumps).
- Extra thermal RC state for solar (wall node already stores heat).
- Changing Kasten–Czeplak, window geometry, or SHGC.
- Per-room τ (one site-wide constant).

**Decisions**
- Class is a **tweak**: intentional small behaviour delta; not a defect.
- Filter the solar **signal** as requested. Geometric sunrise already ramps
  over hours; τ = 30 min mainly rounds hourly weather/GHI steps.
- Explicit `solar_gains=` override still replaces k = 0 after the forecast
  (test/injection seam).

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional smoothing of an existing disturbance; not a
  regression against a specified instantaneous law

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
- Chain: architect → implement → test → restructure → review → ship
- Rationale: one-module disturbance smoothing; tests pin EMA vs
  instantaneous. Test/harden stay on (catalog floor). Localized focused
  review. First-order filter is already used for emitters; no model side
  path.

## Inputs
- User: instantaneous solar vs cloud scaling; LPF so cloud/poor
  predictions change smoothly
- `docs/THEORY.md` §3.4; SWD-462 cloud attenuation; SWD-432 GHI `None`
- Neighbour: `smooth_cloud_cover_step`, emitter `τ_em`

## Pass criteria
1. After a seeded clear-sky k = 0, a following persisted compute with
   overcast cloud does **not** jump k = 0 all the way to the instantaneous
   overcast watts in one `dt`.
2. First (unpersisted) k = 0 with cloud cover still matches instantaneous
   cloud-scaled gain (SWD-462 seed).
3. A GHI `None` horizon step still uses cloud/clear as the **observation**
   (not `ghi_now` leak); the published watt is the EMA of that chain.
4. `τ = 0` is identity with the instantaneous schedule.
5. Fast suite passes. CalVer 2026.09.5; App package synced.
6. Environment Solar model field round-trips minutes → `solar_gain_smoothing_tau_s`.

## Work packages
1. Low-pass solar gain on the forecast/history path (SWD-488)
2. Tests, THEORY, CalVer, changelog, App sync (SWD-489)

## Open items
- None.

## Tracker
- Provider: jira
- Task: SWD-487
- Sub-tasks: SWD-488, SWD-489
- Relates: SWD-462
- Branch: cursor/swd-487-solar-gain-lpf-51da
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/658
- Classification: tweak
- Workflow: delta-fast

## Next
`/ship SWD-487` — Merge PR #658 after review CLEAN

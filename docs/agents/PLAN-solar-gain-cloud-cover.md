# Implementation plan: Solar gain history unattenuated on cloudy days

## Summary
- DISTURBANCES Solar Gain history peaks near clear-sky (~4.6 kW) on a cloudy
  day, while the dashed forecast is ~1 kW, with V-shaped drops just before NOW.
- Spec (`docs/THEORY.md` §3.4, README solar notes): default intensity is
  clear-sky GHI scaled by weather cloud cover (Kasten–Czeplak). Optional GHI
  only when `solar_radiation_entity` is configured.
- App path often leaves `cloud_cover_now` unset, so k=0 (history / NOW) is
  unattenuated while k≥1 uses `cloud_forecast`. Ratio ~4× matches overcast
  factor 0.25.
- Stray GHI from a default `solar_radiation` tag can zero or spike k=0.

## Scope / Decisions / Constraints
**In**
- Always derive `cloud_cover_now` from percent, weather condition, or
  `cloud_forecast[0]`. Persist a cloud series from current cover when there is
  no forecast list.
- Publish weather `condition` on the MQTT weather tag (HA state string).
- k=0 solar uses that cover when GHI is absent (same cloud-scaled model as
  the horizon).
- Read GHI only when `solar_radiation_tag` is configured; ignore BAD tags.
- Keep Kasten–Czeplak `1 − 0.75 c^3.4` as specified in THEORY.
- Tests, CalVer `2026.08.39`, changelog, App package sync.

**Out**
- Replacing Kasten–Czeplak with a linear `(1 − c)` factor.
- Requiring a pyranometer / Open-Meteo GHI sensor.
- Rewriting window geometry, SHGC, or solar scale identification.

**Decisions**
- Class is a **bug**: expected behaviour is already in THEORY; history does
  not follow it.
- GHI still takes precedence when a solar-radiation entity is actually
  configured (THEORY Step 9). Cloud cover is not applied on top of GHI.

## Classification
- Class: bug
- Confidence: high
- Why: historical solar is unattenuated clear-sky while the specified default
  is cloud-cover scaled; expected behaviour is knowable from THEORY

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
- Chain: implement → test → harden → review-fix → ship
- Rationale: contained disturbance/solar wiring; unit tests pin k=0 vs
  forecast. Test and harden stay on (catalog floor). Localized, so focused
  single review.

## Inputs
- Screenshot: DISTURBANCES Solar Gain ~4.6 kW history vs ~1 kW forecast,
  V-drop before NOW (2026-08-30…09-01)
- `docs/THEORY.md` §3.4 Cloud cover correction + optional GHI Step 9
- README troubleshooting **Solar gain looks wrong**

## Acceptance criteria
1. With a weather forecast that has cloud cover but no current percent,
   k=0 solar is attenuated (not clear-sky).
2. Weather condition `cloudy` (no `cloud_coverage`) yields `cloud_cover_now`
   from the condition table.
3. Unconfigured solar-radiation tag does not inject GHI (including 0).
4. Configured GHI still overrides cloud for that step (THEORY Step 9).
5. Fast suite passes. CalVer 2026.08.39; App package synced.

## Work packages
1. Cloud-cover now + k=0 attenuation; no stray GHI (SWD-463)
2. Tests, CalVer, changelog, App sync (SWD-464)

## Open items
- None

## Tracker
- Provider: jira
- Task: SWD-462
- Sub-tasks: SWD-463, SWD-464
- Branch: cursor/solar-gain-cloud-cover-059e
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/650
- Classification: bug
- Workflow: fix-fast

## Next
`/test SWD-462` — Dedicated tests per Workflow binding (same branch/PR)

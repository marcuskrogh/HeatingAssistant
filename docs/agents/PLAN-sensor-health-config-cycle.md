# Implementation plan: Overview health from current sensors only

## Summary
- Overview OVERALL HEALTH (and System Status) still lists BAD tags that are not
  in the current config — leftover MQTT `tag_statuses` from sensors that were
  removed, or from inbound bindings that were never dropped when config changed.
- SWD-385 overlay/prune still keys off inbound bindings plus persisted quality,
  so a historical BAD can survive after the sensor is gone, and a recovered
  configured sensor can stay BAD across cycles.
- Each control cycle must rebuild the health set from the live config, drop
  anything else, and re-read current values so only present faults remain.

## Scope / Decisions / Constraints
**In**
- App health: `evaluate_system_health` sensor module, `HeatingRuntime.system_health`,
  `_inbound_tag_names` / `_prune_unbound_tag_quality` (or a replacement
  “configured sensors this cycle” helper).
- Persist prune of `tag_statuses` / `tag_timestamps` to that same set.
- Re-evaluate remaining tags from the HA catalog and/or current tag values
  each cycle (usable value → GOOD; configured but unavailable → BAD).
- Overview OVERALL HEALTH and System Status `issue_summary` (same payload).
- Tests, CalVer, changelog, App package sync.

**Out**
- MQTT broker / discovery / credentials.
- ID-history health card (SWD-317) — still card-only, not overall quality.
- Changing fusion/averaging of room temperatures.
- New Overview UI chrome beyond using the corrected health payload.

**Decisions**
- “Configured sensors” = this cycle’s room temperature tags, window tags, and
  environment tags (`outdoor_temp_tag`, `weather_tag`, `solar_radiation_tag`,
  `price_tag`). Not leftover inbound MQTT bindings that are absent from that
  config.
- Unconfigured / historical tags never appear in health, even if `state.json`
  still holds an old BAD.
- A configured sensor with a usable live value does not keep a prior BAD.
- A configured sensor that is currently unavailable still warns (live fault).
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.

**Constraints**
- Product copy must not include tracker keys.

## Classification
- Class: bug
- Confidence: high
- Why: health warning is wrong after config change; expected set is the current
  configured sensors only

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
- Rationale: localized quality/persistence defect; tests lock the config-set
  rule; test/harden are the floor

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Prior: SWD-385 stale BAD overlay; SWD-300 System Status quality enum

## Pass criteria
- A persisted BAD for a tag that is not in the current room/environment config
  does not appear in `system_health` modules/issue_summary and does not keep
  overall quality at warning/error by itself.
- After a room temperature tag is removed from config, the next health
  evaluation no longer names that tag, without requiring an App restart.
- A configured tag whose catalog/live value is usable is GOOD on that cycle
  even if `tag_statuses` previously stored BAD.
- A configured tag with no usable live value still contributes a sensors
  warning on that cycle.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. Rebuild configured-sensor health each cycle — SWD-511
2. Tests, CalVer, changelog, App sync — SWD-512

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-510
- Sub-tasks: SWD-511, SWD-512
- Branch: swd-510-sensor-health-config-cycle
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/668
- Classification: bug
- Workflow: fix-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/668


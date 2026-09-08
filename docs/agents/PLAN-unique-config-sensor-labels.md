# Implementation plan: Unique config sensor labels

## Summary
- Config entity chips and the entity picker currently show Home Assistant
  `friendly_name`, which is just `TempPV` for device-bound sensors.
- Use the combined device + entity display name so sensors are uniquely
  distinguishable. Entity IDs stay in the tooltip and picker secondary line.
- Stored configuration remains entity IDs.

## Scope / Decisions / Constraints
**In**
- Shared config entity helper in `heatingassistant/app/static/js/config/config-ui.js`
  (room temperature/window sensors, system entities, heat-source driven entity).
- Thin-bridge entity catalog names so Ingress chips receive the same unique
  display name via `friendly_name`.
- Tests, CalVer, changelog, App package sync.

**Out**
- Dashboard pages outside config.
- Persisted config values (still entity IDs).
- Home Assistant registry data itself.

**Decisions**
- Class is **tweak**: small intentional UI label delta; stored config is correct.
- Label is device name + entity name; skip empty parts; do not duplicate if the
  entity name already starts with the device name; fall back to `friendly_name`,
  then entity ID.
- Entity ID is tooltip + picker secondary text only — not on the chip.
- Longer labels may ellipsize; tooltip still has the entity ID.

**Constraints**
- Dual tree: edit `heatingassistant/` and `custom_components/`, then
  `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional UI label change; expected behaviour is known; not a defect in stored config

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
- Rationale: localized one-concern UI delta; test/harden stay on; focused review is enough

## Inputs
- Research: none
- Model: none
- Sandbox: none

## Pass criteria
- Two selected sensors that share entity name `TempPV` but sit on different
  devices show different chip labels that include the device name.
- Chip tooltip still exposes the entity ID.
- Saved configuration still stores entity IDs, not display names.
- Entity picker primary label uses the same unique display name; search still
  matches name and entity ID.

## Work packages
1. SWD-517 — Unique display-name helper used by chips and picker; catalog publishes
   device + entity names
2. SWD-518 — Harness coverage, CalVer, changelog, App package sync

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-516
- Sub-tasks: SWD-517, SWD-518
- Branch: swd-516-unique-config-sensor-labels
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/669
- Classification: tweak
- Workflow: delta-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/669 (`8170dd4d`)

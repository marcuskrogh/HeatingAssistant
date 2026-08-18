# Implementation plan: Stale BAD tag quality on System Status

## Summary
- System Status stays WARNING with `BAD quality` on configured room-temperature
  tags even when those Home Assistant sensors show valid measurements.
- MQTT is connected and the HA entity catalog is populated; the warning comes
  from persisted MQTT `tag_statuses`, not from live HA state.
- Refresh inbound tag quality from the catalog when it is newer than a retained
  `entity_unavailable` BAD, prune leftover statuses, and republish inbound
  tags once Home Assistant has started.

## Scope / Decisions / Constraints
**In**
- App: overlay inbound tag values/statuses from the HA entity catalog when the
  catalog timestamp is newer than the tag payload (or the tag has no timestamp)
- App: ignore retained MQTT `BAD` / `entity_unavailable` payloads older than
  the current catalog when that catalog row is usable
- App: persist `tag_timestamps`; prune quality maps to current bindings
- App: System Status sensor quality considers current inbound tags only
- App: `hass_states` uses catalog state when a binding stub is unknown
- Thin bridge: republish inbound bound entities on `EVENT_HOMEASSISTANT_STARTED`
- Tests, CalVer, App package sync

**Out**
- Changing fusion averaging rules for live `BAD` that is newer than the catalog
- ID-history health card behaviour (SWD-317)
- MQTT broker / discovery credential work

**Decisions**
- MQTT tag/in remains the live source of truth when its timestamp is newer than
  the catalog (real later unavailability still warns)
- Catalog overlay exists so bind-time retained `BAD` cannot stick after HA has
  published usable entity states
- Health does not warn on unbound leftover `tag_statuses`

## Classification
- Class: bug
- Confidence: high
- Why: status warning is wrong while HA measurements are valid; expected
  behaviour is known

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
  - sandbox: none
- Chain: implement → review-fix → ship
- Rationale: localized quality/persistence defect with a known expected result

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Prior: SWD-300 System Status quality enum; SWD-271 HA entity catalog

## Acceptance criteria
- Persisted `BAD` on `living_room_temp_1` / `living_room_temp_2` clears when a
  newer catalog snapshot has usable numeric states for the bound entities
- A retained MQTT `BAD` older than that catalog is ignored
- A later MQTT `BAD` (timestamp after the catalog) still warns
- Unbound leftover `BAD` tags do not affect overall quality
- After HA started, the thin bridge republishes inbound tag/in from current HA
  state
- Focused tests pass; App package in sync; CalVer bump

## Work packages
1. Catalog overlay + prune persisted BAD tag quality — SWD-386
2. Republish inbound tags when HA has started — SWD-387
3. Tests, CalVer, App sync — SWD-388

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-385
- Sub-tasks: SWD-386, SWD-387, SWD-388
- Branch: cursor/swd-385-tag-quality-stale-bad-77cb
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/620
- Classification: bug
- Workflow: fix-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/620

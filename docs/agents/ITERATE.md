# Iterate: Restart required as Settings repair, not an Update card

## Prior work
- Task: [SWD-352](https://marcusknielsen.atlassian.net/browse/SWD-352)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/618
- Spec context: `docs/agents/PLAN-app-update-path.md`

## Problem
- After updating the App to 2026.08.7, Settings still shows HeatingAssistant
  under **Updates** (`1 update`) with restart-required text.
- The card stays after a normal Home Assistant Core restart.
- The user wants the same separate top-of-Settings section other apps use.

## Clarifications
- Home Assistant Settings has two dashboard cards: **Repairs**
  (`ha-config-repairs`) and **Updates** (`ha-config-updates`).
- HACS (and other integrations) create a fixable `restart_required` repair via
  `issue_registry.async_create_issue` plus `repairs.py` (`homeassistant.restart`).
  That is the separate section.
- SWD-352 published an MQTT Update entity, so the prompt landed in Updates and
  looked like another HeatingAssistant software update. Retained MQTT discovery
  plus a stamp that is not cleared on a normal Core restart kept it there.

## Acceptance criteria
- App start tombstones `homeassistant/update/heatingassistant_restart/config`
  (empty retained payload) so leftover Update cards disappear.
- The thin integration creates a fixable **Restart required** repair when the
  on-disk `manifest.json` version differs from the loaded `VERSION`.
- The repair is deleted when versions match (after Core restart).
- Repair fix flow calls `homeassistant.restart` (HACS path).
- Native `update.py` stays unregistered (would put a card back in Updates).
- Tests cover create/delete and the MQTT tombstone. CalVer bump + App sync.

## Out of scope
- Prebuilt registry images / dynamic download percent.
- Persistent notifications.
- Auto Core restart from `run.sh`.

## Work packages
1. Settings repair + tombstone MQTT update (SWD-357)
2. Tests, CalVer, App sync (SWD-358)

## Tracker
- Task: SWD-356
- Relates: SWD-352
- Sub-tasks: SWD-357, SWD-358

## Next
`/review-fix SWD-356` — Review and auto-fix (single pass)

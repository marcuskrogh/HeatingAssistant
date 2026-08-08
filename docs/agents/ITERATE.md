# Iterate: Streamline config UX — searchable HA entity picker + Environment recommendations

## Prior work
- Task: SWD-270
- Also: SWD-267 (Ingress entity picker free-text + MQTT bindings)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/558 (v2.0.10)
- Spec context: docs/agents/ITERATE.md, docs/agents/MQTT-TOPICS.md

## Problem
The configuration pipeline is cumbersome for first-time setup:

1. Under Ingress, entity selectors only see App-synthetic / already-bound entities.
   Users must remember and type full HA entity IDs (SWD-267 workaround).
2. Environment config expands every sensor option. Solar irradiance is not a
   practical integration path. Weather alone is the low-friction outdoor signal
   (forecast + outdoor temp for the location); a dedicated outdoor temp sensor
   is optional. Electricity price should be recommended alongside weather.

## Acceptance criteria
1. All entity selectors can search HA entities (friendly name + entity ID)
   without requiring users to memorize IDs. Manual ID entry remains as fallback.
2. Environment page: electricity price and weather forecast are recommended and
   expanded (price on top with weather).
3. Outdoor temperature sensor is collapsed/expandable as an optional extension
   below the weather forecast entity.
4. Solar irradiance option is removed from the Environment UI (cleared on save).
5. Regression tests cover entity catalog MQTT path + Environment field
   ordering/collapse behavior.
6. Version bump to **2.0.11**.

## Out of scope
- Removing backend solar-radiation engine support (UI-only removal for now).
- Full HA websocket custom-panel parity outside Ingress.

## Work packages
1. Thin HA bridge publishes a retained MQTT entity catalog for picker domains.
2. App consumes catalog and merges into Ingress `hass_states` for all pickers.
3. Environment UX reorder / collapse / remove solar.
4. Tests + version 2.0.11 + sync App package.

## Tracker
- Task: SWD-271
- Relates: SWD-270
- Branch: `cursor/swd-271-config-ux-entity-picker-7676`
- Status: In Progress

## Next
`/review-fix SWD-271` — Review and auto-fix (single pass)

# Iterate: App rejects --options-path (startup crash-loop)

## Prior work
- Task: SWD-262
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/546 (merge `8713980`)
- Spec context: docs/agents/PLAN-haos-app-mqtt.md / prior ITERATE.md

## Problem
HAOS App crash-loops after thin-integration sync:

```
__main__.py: error: unrecognized arguments: --options-path /data/options.json
```

`heating_assistant/run.sh` passes `--options-path`, `--data-dir`, and `--ha-runtime`, but
`heatingassistant.app` argparse only knows `--host` / `--port` / `--data-dir` / `--ha-runtime`.

## Acceptance criteria
1. `python3 -m heatingassistant.app` accepts `--options-path` (default `/data/options.json`).
2. Supervisor options (`instance_id`, MQTT broker/port/credentials) load from that file and merge into App config without wiping durable `config.json` fields (rooms, schedules, bindings, …).
3. The `run.sh` argv shape starts without argparse failure (CLI parse smoke test).
4. Version bump so Supervisor offers Update.

## Out of scope
- Changing Supervisor options schema beyond existing MQTT/instance fields.
- Broader MQTT broker client wiring beyond loading options into runtime config.

## Work packages
1. Accept `--options-path` + merge Supervisor options into config
2. Version bump 2.0.2 + regression test
3. Sync App package / handoff

## Tracker
- Task: SWD-263
- Relates: SWD-262
- Branch: `cursor/swd-263-options-path-01f0`
- PR: (pending)

## Next
`/review-fix SWD-263` — Review and auto-fix (single pass)

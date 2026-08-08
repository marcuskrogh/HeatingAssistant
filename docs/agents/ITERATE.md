# Iterate: MQTT still disconnected after mqtt:need — retry discovery + SSL

## Prior work
- Task: SWD-270
- Also: SWD-269 (soft MQTT / local KPIs), SWD-271 (config UX, shipped between)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/558 (v2.0.10)
- Spec context: docs/agents/ITERATE.md, docs/agents/MQTT-TOPICS.md

## Problem
After updating to v2.0.10/2.0.11, Ingress still shows:

`API connected - N entities · MQTT disconnected`

MPC LOAD and NEXT CONTROL populate (local App control cycle from SWD-269), but
room CURRENT / MODEL FIT stay `—`, heating power and daily energy stay at 0, and
plots stay empty because MQTT tags never arrive.

Remaining gaps after SWD-270:

1. Supervisor MQTT discovery runs **once** at process start. When
   `/services/mqtt` returns "Service not enabled" (common until `mqtt:need` is
   fully provisioned), the App creates an anonymous Paho client and never
   re-discovers credentials.
2. Stock options keep non-blank `mqtt_broker` / `mqtt_port`, so discovery only
   fills username/password and **ignores** Supervisor host/port/`ssl`.
3. `mqtt_ssl` is parsed but never applied to `PahoMqttBus`.
4. Health/status lack a clear last discovery/connect error for operators.

## Acceptance criteria
1. When MQTT is disconnected and credentials were blank / sourced from
   Supervisor, App retries Supervisor `/services/mqtt` discovery with backoff
   and applies credentials without requiring an App rebuild.
2. When credentials are discovered from Supervisor (blank option creds), also
   apply Supervisor host/port/ssl for the provisioned MQTT endpoint.
3. `PahoMqttBus` applies TLS when `mqtt_ssl` is true; reconnects after
   credential/endpoint updates.
4. `/api/health` (and state) expose mqtt connect/discovery diagnostics
   (connected, source, last error, username present — password redacted).
5. Regression tests cover retry/reconfigure, supervisor endpoint+ssl merge, and
   diagnostics.
6. Version bump to **2.0.12**.

## Out of scope
- Changing Mosquitto addon config itself.
- Full offline message queue persistence.

## Work packages
1. Discovery merge: when filling blank creds from Supervisor, also take
   host/port/ssl; improve error logging for "Service not enabled".
2. PahoMqttBus: TLS support + `reconfigure()` for auth/endpoint updates.
3. Runtime/main: background discovery retry while disconnected; persist
   discovered settings; expose diagnostics.
4. Tests + version 2.0.12 + sync App package.

## Tracker
- Task: SWD-273
- Relates: SWD-270
- Branch: `cursor/swd-273-mqtt-discovery-retry-f56e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/560

## Next
`/review-fix SWD-273` — Review and auto-fix (single pass)

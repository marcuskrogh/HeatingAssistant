# Iterate: Ingress 502 Bad Gateway after MQTT update

## Prior work
- Prior ship: https://github.com/marcuskrogh/HeatingAssistant/pull/553 (v2.0.7 — real MQTT + live panel sync)
- Spec context: heatingassistant/mqtt/paho_bus.py
- Relates: SWD-267 / SWD-255

## Problem
After updating to v2.0.7, opening HeatingAssistant Ingress shows:

- `502: Bad Gateway`
- Dialog: "The app seems to not be ready, it might still be starting. Do you want to try again?"

Root cause: `PahoMqttBus.__init__` blocked on MQTT connect and **raised TimeoutError**
when Mosquitto was slow, unreachable, or auth-failing. `run.sh` always passes
`--ha-runtime`, and `main()` created the bus **before** binding the Ingress HTTP
server — so any MQTT connect failure prevented the App from listening and
Supervisor Ingress returned 502.

## Acceptance criteria
1. App HTTP/Ingress starts even when the MQTT broker is unreachable or slow.
2. MQTT connects asynchronously and retries in the background (subscriptions
   applied on connect; retained bindings/status republished on connect).
3. Runtime start does not crash the process if initial MQTT publishes fail.
4. Regression tests cover non-blocking MQTT bus construction and App start
   without a live broker.
5. Version bump so Supervisor offers Update (**2.0.8**).

## Out of scope
- Changing default broker hostname or MQTT auth UX.
- Full offline message queue persistence across App restarts.

## Work packages
1. Non-blocking `PahoMqttBus` + reconnect/republish on connect
2. Runtime start best-effort MQTT publish + health `mqtt_connected`
3. Tests + version bump 2.0.8 + sync App package

## Tracker
- Task: SWD-268
- Relates: SWD-267 / PR #553
- Branch: `cursor/swd-268-mqtt-ingress-502-72da`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/554

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/554
- Version: **2.0.8** — Ingress binds without waiting on Mosquitto; MQTT reconnects in background.

## Next
Done

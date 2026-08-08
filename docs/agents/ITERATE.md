# Iterate: Ingress features empty / Controller Tuning 502 after MQTT non-blocking start

## Prior work
- Task: SWD-268
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/554 (v2.0.8 — Ingress binds without waiting on Mosquitto)
- Also: PR #553 (v2.0.7 — real MQTT + live panel sync)
- Spec context: docs/agents/ITERATE.md (prior), heatingassistant/app/runtime.py, heatingassistant/mqtt/paho_bus.py

## Problem
After Ingress boots ("API connected - 13 entities", LIVE), almost no features work:

1. KPIs stuck at 0 / Model Fit shows `—`
2. Temperature plots empty (Filtered / Measured / Forecast)
3. Living Room CURRENT temperature shows `—`
4. Controller Tuning **Apply Changes** shows `Error: 502 Bad Gateway: 502: Bad Gateway`

Root causes (post SWD-268):

1. **Mutating APIs hard-require MQTT publish** — `update_config` / `run_control_cycle` await `PahoMqttBus.publish`, which waits then raises when disconnected. HTTP only catches `ValueError` → Ingress 502 on Apply while GETs still succeed.
2. **Panel never surfaces `mqtt_connected`** — "API connected" only means `GET /api/state` worked; data plane can be down.
3. **HA bridge publishes `tag/.../in` without retain** — App that connects late (or reconnects) misses the bind-time snapshot until the next HA state change.
4. **App stubs `/api/history` and omits several KPI sensors** — plots stay blank and Model Fit / MPC Load / Daily Energy cannot populate even when temps flow.

## Acceptance criteria
1. Controller Tuning Apply (and other config writes) persist locally and return JSON success even when MQTT is disconnected; MQTT publishes are best-effort and retry on reconnect.
2. Panel status shows MQTT connected/disconnected (from health/state), not only "API connected - N entities".
3. Thin HA bridge publishes input tags with retain so App receives last-known values on (re)subscribe.
4. `/api/history` returns in-memory room temperature / power / setpoint series so temperature plots populate once live values exist.
5. App synthesizes missing dashboard KPI entities that can be derived from runtime (`mpc_performance`, and non-null placeholders where appropriate).
6. Regression tests cover: config/service write with disconnected MQTT; retained tag/in publish; history non-empty after tag updates; mqtt status exposed.
7. Version bump so Supervisor offers Update (**2.0.9**).

## Out of scope
- Full EKF/MPC coordinator parity (real model-fit residual, solar irradiance forecasts, long-term HA recorder history).
- Changing default broker hostname or MQTT auth UX.

## Work packages
1. Soft MQTT publishes on mutating paths + fail-fast when disconnected
2. Retain `tag/.../in` from thin HA bridge
3. In-memory `/api/history` + shim query passthrough
4. Synthesize `mpc_performance` / energy / solar stubs + MQTT status in panel
5. Tests + version bump 2.0.9 + sync App package

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/557
- Version: **2.0.9** — soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status in panel.

## Next
Done

# Iterate: Climate heat-pump actuation missing after thin bridge

## Prior work
- Task: SWD-279
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/568 (v2.0.18)
- Spec context: docs/agents/PLAN-haos-app-mqtt.md (heaters commanded correctly)

## Problem
Dashboard shows planned power ≈ −3.5 kW (max cooling) while measured power
stays ≈ −1 W and the heat pump does not cool. MQTT/API report connected.

Root causes:
1. Thin bridge `_write_entity` only handled `switch`/`number` — climate
   writes were a silent no-op despite MQTT docs claiming climate support.
2. App published raw MPC **fractions** on tag/out; fraction →
   `(hvac_mode, temperature)` (old `climate_hp_command`) was never ported
   after SWD-262 deleted coordinator actuation.
3. `heating_power_measured` summed fractions and labeled them as **W**, so
   u=−1 displayed as −1 W.

## Acceptance criteria
1. Climate-bound heat pumps receive `climate.set_hvac_mode` +
   `climate.set_temperature` for cooling/heating fractions (incl. u≈−1).
2. Measured room heating power reports thermal watts via
   `display_smooth_thermal_power` (not raw fraction).
3. `number` heaters write `round(fraction*100)`; `switch` uses
   `fraction > 0.5` (signed fractions must not turn switches on).
4. Climate entity feedback (`current_temperature`, `hvac_modes`) reaches
   the App via a `{output_tag}_state` inbound binding.
5. Regression tests; version **2.0.19**.

## Out of scope
- Full actuation watchdog / fast setpoint re-apply between MPC ticks.
- Restoring fat HA climate platforms.

## Work packages
1. App `actuation.py` + publish-time domain command conversion.
2. Thin bridge climate writes + climate attribute publish; switch truthy fix.
3. Measured/total power from thermal watts; HP cooling config passthrough.
4. Version 2.0.19 + tests + tracker.

## Tracker
- Task: SWD-280
- Relates: SWD-262, SWD-259
- Branch: `cursor/swd-280-climate-actuation-c648`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/570

## Review-fix
CLEAN — version lock tests bumped to 2.0.19; climate `{output_tag}_state`
feedback no longer re-triggers MPC (write→state→write thrash guard).

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/570
- Merge: `7106752`
- Version: **2.0.19**

## Next
Done — rebuild/update App on HAOS to v2.0.19; confirm climate entity receives
cool/heat_cool + lowered setpoint when Planned Power is strongly negative,
and measured power reports kilowatts (not −1 W). `/iterate` if still wrong
after rebuild.

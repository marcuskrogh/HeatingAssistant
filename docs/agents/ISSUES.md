# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-299 | Bug | [Bug] Identification KPIs (model fit / R² / RMSE / Estimated) not populating Overview or System Identification index | To Do | — | docs/agents/PLAN-identification-kpis.md | `/implement SWD-299` — https://github.com/marcuskrogh/HeatingAssistant/pull/593 |
| SWD-298 | Bug | [Bug] Door/window sensors do not turn off heaters after configured debounce — App missing window override | Done | — | docs/agents/PLAN-window-heater-override.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/592 |
| SWD-297 | Bug | [Bug] Applied / measured solar gain stuck at 0 — App hass_states hardcodes solar_gain_measured | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/590 |
| SWD-296 | Bug | [Bug] Sysid Apply Parameters not restored — defaults on reload + panel jumps to overview | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/588 |
| SWD-289 | Task | [Define] Restore system identification page — end App sysid no-ops + fix panel chart imports | Done | — | docs/agents/PLAN-sysid-services.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/586 |
| SWD-293 | Sub-task | Fix sysid panel ES-module imports/exports + cache bust | Done | SWD-289 | docs/agents/PLAN-sysid-services.md | — |
| SWD-292 | Sub-task | Restore engine sysid modules deleted in SWD-262 | Done | SWD-289 | docs/agents/PLAN-sysid-services.md | — |
| SWD-294 | Sub-task | Wire P0 compute services + publish sysid/open-loop sensors | Done | SWD-289 | docs/agents/PLAN-sysid-services.md | — |
| SWD-290 | Sub-task | Wire Apply/persist + parameter_history on controller_config | Done | SWD-289 | docs/agents/PLAN-sysid-services.md | — |
| SWD-295 | Sub-task | Wire DatasetStore create/delete under App data_dir | Done | SWD-289 | docs/agents/PLAN-sysid-services.md | — |
| SWD-291 | Sub-task | Regression tests + version 2.0.27 + App package sync | Done | SWD-289 | docs/agents/PLAN-sysid-services.md | — |
| SWD-288 | Bug | [Bug] Climate card setpoints reset to default — overview and room view cannot change target or comfort band | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/584 |
| SWD-286 | Bug | [Bug] Room temperature plot ignores schedule comfort_offset — constraints stay at room default | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/582 |
| SWD-287 | Bug | [Bug] Expanded schedule on Schedules detail collapses on its own during reconfiguration | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/582 |
| SWD-285 | Bug | [Bug] Controller Tuning preview ignores unapplied params — only works after Apply | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/580 |
| SWD-284 | Bug | [Bug] Room view Price plot missing historical data — App never publishes electricity_price sensor | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/578 |
| SWD-283 | Bug | [Bug] Large whitespace between Save Current Window inputs on mobile | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/576 |
| SWD-282 | Bug | [Bug] Solar gain plot stuck at zero despite High exposure — App room build drops aperture | Done | — | docs/agents/BUG.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/574 |
| SWD-281 | Task | [Iterate] App update clears room-plot / ID history — persist under /data like original integration | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/572 |
| SWD-280 | Task | [Iterate] Climate heat-pump actuation missing after thin bridge — planned cooling never reaches HA entity | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/570 |
| SWD-279 | Task | [Iterate] Plot forecasts still flat — JSON-safe attrs, weather.get_forecasts, linearised from estimated output | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/568 |
| SWD-278 | Task | [Iterate] Incomplete plot forecasts — wire outdoor/solar/price into MPC compute + MQTT attrs | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/567 |
| SWD-277 | Task | [Iterate] Plot samples too dense + empty forecasts — gate history to update_interval and expose MPC trajectories | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/566 |
| SWD-276 | Task | [Iterate] KPIs/plots flat overnight — App has no wall-clock history/control ticker | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/565 |
| SWD-275 | Task | [Iterate] MQTT rc=5 — SUPERVISOR_TOKEN missing without with-contenv entrypoint | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/564 |
| SWD-274 | Task | [Iterate] MQTT rc=5 — SUPERVISOR_TOKEN missing without hassio_api | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/563 |
| SWD-273 | Task | [Iterate] MQTT still disconnected after mqtt:need — one-shot discovery + ignored SSL/endpoint | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/560 |
| SWD-271 | Task | [Iterate] Streamline config UX — searchable HA entity picker + Environment recommendations | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/559 |
| SWD-270 | Task | [Iterate] Ingress shows MQTT disconnected — App missing Supervisor Mosquitto credentials | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/558 |
| SWD-269 | Task | [Iterate] Ingress features empty / Controller Tuning 502 after MQTT non-blocking start | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/557 |
| SWD-268 | Task | [Iterate] Ingress 502 Bad Gateway after MQTT update — App not ready | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/554 |
| SWD-267 | Task | [Iterate] Ingress entity picker only shows App sensors — cannot wire HA room temperatures | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/551 |
| SWD-266 | Task | [Iterate] Ingress panel LOAD ERROR — bare module path fails dynamic import | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/550 |
| SWD-265 | Task | [Iterate] Ingress UI stuck on Loading App API | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/549 |
| SWD-264 | Task | [Iterate] Ingress UI 404 — static assets missing from pip install | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/548 |
| SWD-263 | Task | [Iterate] App rejects --options-path and crash-loops on start | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/547 |
| SWD-262 | Task | [Iterate] Finish HAOS App: Ingress parity, thin-only tree, port clash | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/546 |
| SWD-255 | Task | HAOS App + thin MQTT integration (compute isolation) | Done | — | docs/agents/PLAN-haos-app-mqtt.md | Done |
| SWD-258 | Sub-task | App packaging skeleton (sync + version lock) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-259 | Sub-task | Thin MQTT integration (entity↔tag bridge) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-260 | Sub-task | MQTT contract (topics, bindings, status) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-261 | Sub-task | Move compute into App (parity + persistence) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-257 | Sub-task | Port dashboard to App Ingress | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-256 | Sub-task | E2E hardening (load isolation + regressions) | Done | SWD-255 | docs/agents/PLAN-haos-app-mqtt.md | — |
| SWD-254 | Bug | [Bug] Remove dual-mode nonlinear MPC (revert to pre-SWD-240) — HA hang | Done | — | docs/agents/BUG-swd-254-remove-nonlinear-mpc.md | Done |
| SWD-238 | Story | Dual-mode MPC (linear / non-linear) | To Do | — | — | Dual-mode removed from main by SWD-254; optional Story closeout |
| SWD-248 | Task | [Bug] stop NMPC hang (executor, timeout, SciPy horizon cap) | Done | — | — | Done — superseded by SWD-254; PR #542 closed |

## Log
- 2026-08-10 — `/ship` SWD-299 implement: publish `*_model_fit_quality` + `*_parameter_confidence` in App hass_states; port fit helpers; v2.0.31; PR #593; Next `/review-fix SWD-299`.
- 2026-08-10 — `/define` SWD-299: identification KPIs (model fit / R² / RMSE / Estimated) not populating Overview or System Identification index — App never publishes `*_model_fit_quality` / `*_parameter_confidence` after SWD-262; PLAN `docs/agents/PLAN-identification-kpis.md`; branch `cursor/swd-299-identification-kpis-3a87`; Next `/implement SWD-299`.
- 2026-08-10 — shipped SWD-298 via PR #592: App door/window heater override after debounce; v2.0.30; review-fix CLEAN. Rebuild App on HAOS so open sensors shut heaters after `window_open_debounce`.
- 2026-08-10 — `/ship` SWD-298 implement: App window override + timers + disabled_sources/Q inflation; v2.0.30; PR #592; Next `/review-fix SWD-298`.
- 2026-08-10 — `/define` SWD-298: door/window sensors do not turn off heaters after debounce — App missing window override deleted in SWD-262; PLAN `docs/agents/PLAN-window-heater-override.md`; branch `cursor/swd-298-window-heater-override-1125`; Next `/implement SWD-298`.
- 2026-08-10 — shipped SWD-297 via PR #590 (`6288e22`): publish applied solar_gain_measured; v2.0.29; review-fix CLEAN. Rebuild App on HAOS so DISTURBANCES Solar Gain left of NOW tracks daytime dynamics.
- 2026-08-10 — `/ship` SWD-297: implement measured solar_gain_measured from applied solar forecast; v2.0.29; PR #590; Next `/review-fix SWD-297`.
- 2026-08-10 — `/define` SWD-297: applied/measured solar gain stuck at 0 while forecast is correct — `hass_states` hardcodes `solar_gain_measured`; BUG `docs/agents/BUG.md`; branch `cursor/swd-297-measured-solar-gain-zero-f475`; Next `/implement SWD-297`.
- 2026-08-10 — shipped SWD-296 via PR #588 (`6ccce63`): restore sysid Apply params + panel route; v2.0.28; review-fix CLEAN. Rebuild App on HAOS so Apply Parameters stick and the panel keeps the current page across remounts.
- 2026-08-10 — SWD-296 review-fix CLEAN on PR #588; restore sysid Apply params + panel route; v2.0.28; CI green. Next: merge + rebuild HAOS.
- 2026-08-10 — SWD-296 In Review + PR #588: restore sysid Apply params + panel route; v2.0.28; Next `/review-fix SWD-296`.
- 2026-08-10 — `/bug`+implement SWD-296: sysid Apply Parameters not restored + panel overview remount; restore `estimated_params` on engine rebuild, publish thermal attrs on `temperature_filtered`, sessionStorage route restore; v2.0.28; branch `cursor/swd-296-sysid-params-overview-5009`.
- 2026-08-10 — shipped SWD-289 via PR #586 (`197c307`): restore system identification (panel imports + App sysid ownership); v2.0.27; review-fix CLEAN. Rebuild App on HAOS so EKF / open-loop / automatic identification work.
- 2026-08-10 — SWD-289 review-fix CLEAN on PR #586; panel imports + App sysid ownership; v2.0.27; shipping closeout.
- 2026-08-10 — `/ship` SWD-289: implement → review-fix → closeout for system identification restore.
- 2026-08-10 — `/define` SWD-289: restore system identification page (panel import gaps + end App sysid no-ops deferred from SWD-281); PLAN `docs/agents/PLAN-sysid-services.md`; Sub-tasks SWD-293/292/294/290/295/291; branch `cursor/swd-289-sysid-services-851a`; Next `/implement SWD-289`.
- 2026-08-10 — shipped SWD-288 via PR #584 (`a084c5a`): climate setpoint persistence; v2.0.26; review-fix CLEAN. Rebuild App on HAOS so TARGET / COMFORT BAND stick on Overview and room view.
- 2026-08-10 — SWD-288 review-fix CLEAN on PR #584; shipping closeout (v2.0.26).
- 2026-08-10 — SWD-288 In Review + PR #584: climate setpoint persistence; v2.0.26; Next `/review-fix SWD-288`.
- 2026-08-10 — `/bug`+implement SWD-288: climate card setpoints reset — App climate services were no-ops; wire set_temperature/turn_on/off + panel set_room_setpoint; v2.0.26; branch `cursor/swd-288-climate-setpoint-reset-d3ac`.
- 2026-08-09 — shipped SWD-286/SWD-287 via PR #582 (`bc6b090`): schedule comfort constraints + expand collapse; v2.0.25; rebuild App on HAOS so Night Mode ±3 shows on room plot and expanded schedules stay open.
- 2026-08-09 — SWD-286/SWD-287 In Review + PR #582: schedule comfort constraints + expand collapse; v2.0.25; Next `/review-fix`.
- 2026-08-09 — `/bug`+implement SWD-286/SWD-287: schedule comfort_offset on plot/controller + schedules expand collapse; v2.0.25; branch `cursor/swd-286-schedule-comfort-constraints-7e7d`.
- 2026-08-09 — shipped SWD-285 via PR #580 (`093e547`): Controller Tuning preview uses unapplied draft params; v2.0.24; review-fix CLEAN. Rebuild App on HAOS so Preview reflects draft weights before Apply.
- 2026-08-09 — SWD-285 review-fix CLEAN on PR #580; shipping closeout (v2.0.24).
- 2026-08-09 — `/bug`+implement SWD-285: Controller Tuning preview ignores unapplied params — restore App `preview_tuning_forecast`; v2.0.24; branch `cursor/swd-285-tuning-preview-unapplied-ebd3`.
- 2026-08-09 — shipped SWD-284 via PR #578 (`afcbea7`): publish electricity_price + day-ahead history backfill; v2.0.23; review-fix CLEAN. Rebuild App on HAOS so room Price shows left of NOW.
- 2026-08-09 — SWD-284 review-fix CLEAN on PR #578; shipping closeout (v2.0.23).
- 2026-08-09 — `/ship` SWD-284: room view Price plot missing historical data — App never publishes electricity_price; v2.0.23; branch `cursor/swd-284-price-history-a08d`.
- 2026-08-09 — SWD-284 In Review + PR #578: publish electricity_price + day-ahead history backfill; v2.0.23; Next `/review-fix SWD-284`.
- 2026-08-09 — `/bug` SWD-284: room view Price plot missing historical data — App never publishes `electricity_price` synthetic; synthesize from day-ahead attrs; v2.0.23; branch `cursor/swd-284-price-history-a08d`.
- 2026-08-09 — shipped SWD-283 via PR #576 (`f117f93`): Save Current Window mobile flex gap; v2.0.22; review-fix CLEAN. Rebuild App on HAOS so identification save fields stack tightly on phone.
- 2026-08-09 — SWD-283 review-fix CLEAN on PR #576; shipping closeout (v2.0.22).
- 2026-08-09 — `/ship` SWD-283: large whitespace between Save Current Window inputs on mobile — `flex: 1 1 220px` became height under column layout; v2.0.22; branch `cursor/fix-sysid-save-row-mobile-gap-6a4c`.
- 2026-08-09 — shipped SWD-282 via PR #574 (`38846fb`): Option A solar exposure aperture wired in App room build; v2.0.21; review-fix CLEAN. Rebuild App on HAOS so DISTURBANCES Solar Gain shows dynamics.
- 2026-08-09 — SWD-282 review-fix CLEAN on PR #574; shipping closeout (v2.0.21).
- 2026-08-09 — `/ship` SWD-282: solar gain flat zero despite High exposure — `_build_house_model` dropped aperture; v2.0.21; branch `cursor/swd-282-solar-exposure-aperture-3296`.
- 2026-08-09 — shipped SWD-281 via PR #572 (`11325cc`): persist plot/ID history under `/data`; v2.0.20; review-fix CLEAN. Rebuild App on HAOS so room plots survive updates.
- 2026-08-09 — SWD-281 review-fix CLEAN on PR #572; shipping closeout (v2.0.20).
- 2026-08-09 — SWD-281 In Review + PR #572: persist plot/ID history under `/data`; v2.0.20; Next `/review-fix SWD-281`.
- 2026-08-09 — `/iterate` SWD-281 from SWD-279 deferred scope: App update clears room-plot / ID history — persist under `/data`; v2.0.20; branch `cursor/swd-281-history-persistence-32e0`.
- 2026-08-09 — shipped SWD-280 via PR #570 (`7106752`): climate HP actuation + thermal measured power; v2.0.19; review-fix CLEAN. Rebuild App on HAOS so planned cooling reaches the climate entity.
- 2026-08-09 — SWD-280 review-fix CLEAN on PR #570; shipping closeout (v2.0.19).
- 2026-08-09 — SWD-280 In Review + PR #570: climate HP actuation + thermal measured power; v2.0.19; Next `/review-fix SWD-280`.
- 2026-08-09 — `/iterate` SWD-280: climate HP actuation missing after thin bridge — planned −3.5 kW cooling never reaches HA entity; measured −1 W was raw fraction; v2.0.19; branch `cursor/swd-280-climate-actuation-c648`.
- 2026-08-09 — shipped SWD-279 via PR #568 (`d894c79`): JSON-safe attrs, weather.get_forecasts, EKF bridge; v2.0.18; review-fix CLEAN. Rebuild App on HAOS for Price Forecast / Disturbances / Linearised from Filtered.
- 2026-08-09 — SWD-279 review-fix CLEAN on PR #568; shipping closeout (v2.0.18).
- 2026-08-09 — SWD-279 In Review + PR #568: JSON-safe attrs, weather.get_forecasts, EKF bridge; v2.0.18; Next `/review-fix SWD-279`.
- 2026-08-09 — `/iterate` SWD-279 from SWD-278: plot forecasts still flat — JSON-safe attrs, weather.get_forecasts, linearised from estimated output; v2.0.18; branch `cursor/swd-279-forecast-bridge-attrs-4b6c`.
- 2026-08-09 — shipped SWD-278 via PR #567: outdoor/solar/price into MPC + MQTT attrs; v2.0.17; review-fix CLEAN. Rebuild App on HAOS for Disturbances + day-ahead Price Forecast.
- 2026-08-09 — SWD-278 In Review + PR #567: outdoor/solar/price into MPC + MQTT attrs; v2.0.17; Next `/review-fix SWD-278`.
- 2026-08-09 — `/iterate` SWD-278 from SWD-277: incomplete outdoor/solar/price/linearised forecasts — MQTT attrs + stop zeroing solar; v2.0.17; branch `cursor/swd-278-forecast-disturbances-f56e`.
- 2026-08-09 — shipped SWD-277 via PR #566: history gated to update_interval + MPC forecasts; v2.0.16; review-fix CLEAN. Rebuild App on HAOS for ~15 min plot cadence and Forecast / Planned Power.
- 2026-08-09 — SWD-277 review-fix CLEAN: room_slug keys, price_tag, power capacity meta, forecast lock; Next `/ship SWD-277`.
- 2026-08-09 — SWD-277 In Review + PR #566: history gated to update_interval + MPC forecast payload; v2.0.16; Next `/review-fix SWD-277`.
- 2026-08-09 — `/iterate` SWD-277 from SWD-276: plot samples too dense + empty forecasts — gate history to update_interval + expose MPC trajectories; v2.0.16; branch `cursor/swd-277-plot-cadence-forecasts-f56e`.
- 2026-08-09 — shipped SWD-276 via PR #565: wall-clock history/control ticker; v2.0.15; review-fix CLEAN. Rebuild App on HAOS so quiet-period plots keep sampling.
- 2026-08-09 — SWD-276 In Review + PR #565: wall-clock history/control ticker; v2.0.15; Next `/review-fix SWD-276`.
- 2026-08-09 — `/iterate` SWD-276 from SWD-275: MQTT ok but KPIs/plots flat overnight — no wall-clock history/control ticker; v2.0.15; branch `cursor/swd-276-wall-clock-ticker-f56e`.
- 2026-08-08 — shipped SWD-275 via PR #564: with-contenv entrypoint for SUPERVISOR_TOKEN; v2.0.14; review-fix CLEAN. Rebuild App on HAOS to pick up token export.
- 2026-08-08 — SWD-275 In Review + PR #564: with-contenv entrypoint for SUPERVISOR_TOKEN; v2.0.14; Next `/review-fix SWD-275`.
- 2026-08-08 — `/iterate` SWD-275 from SWD-274: SUPERVISOR_TOKEN still missing after hassio_api — run.sh lacked with-contenv; v2.0.14; branch `cursor/swd-275-with-contenv-token-f56e`.
- 2026-08-08 — shipped SWD-274 via PR #563: hassio_api + homeassistant_api for SUPERVISOR_TOKEN; v2.0.13; review-fix CLEAN (`supervisor_token_present`). Rebuild App on HAOS to pick up token injection.
- 2026-08-08 — SWD-274 review-fix CLEAN on PR #563: hassio_api confirmed necessary (user still on v2.0.12); health exposes `supervisor_token_present`; packaging flags OK. Next `/ship SWD-274`.
- 2026-08-08 — SWD-274 In Review + PR #563: hassio_api + homeassistant_api for SUPERVISOR_TOKEN; v2.0.13; Next `/review-fix SWD-274`.
- 2026-08-08 — `/iterate` SWD-274 from SWD-273: MQTT rc=5 not authorised — SUPERVISOR_TOKEN missing without `hassio_api`; v2.0.13; branch `cursor/swd-274-hassio-api-token-f56e`.
- 2026-08-08 — shipped SWD-273 via PR #560 (`a536861`): retry Supervisor MQTT discovery + SSL endpoint; v2.0.12; review-fix CLEAN (ssl CI, retry stop-on-creds, no explicit-cred overwrite, HTTP diagnostics, result=error path).
- 2026-08-08 — SWD-273 review-fix CLEAN on PR #560: ssl CI expectation, retry stop-on-creds, no explicit-cred overwrite, HTTP diagnostics, result=error path; shipping.
- 2026-08-08 — SWD-273 In Review + PR #560: retry Supervisor MQTT discovery + SSL endpoint; v2.0.12; Next `/review-fix SWD-273`.
- 2026-08-08 — `/iterate` SWD-273 from SWD-270: MQTT still disconnected after mqtt:need — retry discovery + apply host/port/ssl + TLS + diagnostics; v2.0.12; branch `cursor/swd-273-mqtt-discovery-retry-f56e`.

- 2026-08-08 — shipped SWD-271 via PR #559 (`1306eab`): searchable HA entity catalog + Environment UX; v2.0.11; review-fix CLEAN (catalog flag + weather outdoor °C fallback).
- 2026-08-08 — `/iterate` SWD-271 from SWD-270: streamline config UX — MQTT HA entity catalog for searchable pickers; Environment recommends price+weather, collapses outdoor temp, removes solar irradiance; v2.0.11; branch `cursor/swd-271-config-ux-entity-picker-7676`.
- 2026-08-08 — shipped SWD-270 via PR #558 (`b8201de`): mqtt:need + Supervisor MQTT credential discovery; v2.0.10; review-fix CLEAN.
- 2026-08-08 — SWD-270 review-fix CLEAN: credential pair rule, durable secret fallback, honest `mqtt_source`, redact `mqtt_password` from HTTP JSON; Next Done / ship via PR #558.
- 2026-08-08 — SWD-270 In Review + PR #558: mqtt:need + Supervisor MQTT credential discovery; v2.0.10; Next `/review-fix SWD-270`.
- 2026-08-08 — `/iterate` SWD-270 from SWD-269: MQTT disconnected because Mosquitto rejects anonymous and App lacked `mqtt:need` + Supervisor credential discovery; v2.0.10; branch `cursor/swd-270-mqtt-supervisor-creds-65c0`.
- 2026-08-08 — shipped SWD-269 via PR #557: soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status; v2.0.9; review-fix CLEAN (energy gap + bare 503 handlers fixed forward).
- 2026-08-08 — SWD-269 In Review + PR #557: soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status; v2.0.9; Next `/review-fix SWD-269`.
- 2026-08-08 — `/iterate` SWD-269 from SWD-268: empty KPIs/plots + Controller Tuning 502 when MQTT publish hard-fails; soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status in panel; v2.0.9; branch `cursor/swd-269-empty-kpi-tuning-502-65c0`.
- 2026-08-08 — shipped SWD-268 via PR #554: non-blocking MQTT so Ingress binds without Mosquitto; v2.0.8.
- 2026-08-08 — SWD-268 review-fix CLEAN (focused); should-fix message-dispatch deadlock fixed forward.
- 2026-08-08 — SWD-268 In Review + PR #554: non-blocking MQTT so Ingress binds without Mosquitto; v2.0.8; Next `/review-fix SWD-268`.
- 2026-08-08 — `/iterate` SWD-268: Ingress 502 after v2.0.7 MQTT ship (PR #553); Paho connect raised before HTTP bind; v2.0.8; branch `cursor/swd-268-mqtt-ingress-502-72da`.
- 2026-08-08 — shipped KPI MQTT live sync via PR #553 (v2.0.7): App connects to Mosquitto + panel live state sync; KPIs/room temps populate. (Mirror previously labeled this SWD-268 before the Ingress-502 iterate claimed that key in Jira.)
- 2026-08-08 — shipped SWD-267 via PR #551: typed HA entity IDs + auto MQTT bindings; v2.0.6.
- 2026-08-08 — SWD-267 PR #551: free-text HA entity IDs + auto MQTT bindings; v2.0.6; In Review.
- 2026-08-08 — `/iterate` SWD-267: Ingress entity picker only shows App sensors; free-text entity IDs + auto MQTT bindings; v2.0.6; branch `cursor/swd-267-ha-entity-wiring-5d31`.
- 2026-08-08 — shipped SWD-266 via PR #550: Ingress BASE_PATH from script URL fixes dynamic import LOAD ERROR; v2.0.5.
- 2026-08-08 — `/iterate` SWD-266 from SWD-265: bare `BASE_PATH` breaks dynamic `import()` under Ingress; v2.0.5; PR #550.
- 2026-08-08 — shipped SWD-265 via PR #549: Ingress base href + relative asset/API paths fix loading stall; v2.0.4.
- 2026-08-07 — `/iterate` SWD-262 from SWD-255: port 8100 (PLC clash), Ingress industrial panel App shim, thin-only custom_components; branch `cursor/swd-262-finish-haos-app-01f0`.
- 2026-08-07 — shipped SWD-255 via PR #545 (merge `973a2c5`): HAOS App + thin MQTT integration; Sub-tasks SWD-258/259/260/261/257/256 Done.
- 2026-08-07 — `/ship` SWD-255 implement: App packaging, MQTT bridge, engine compute, Ingress shell, hardening tests (39 passed). Moving to review-fix.
- 2026-08-07 — `/define` SWD-255: HAOS App + thin MQTT integration plan approved; Sub-tasks SWD-258/259/260/261/257/256; branch `cursor/swd-255-haos-app-mqtt-01f0`; Next `/implement SWD-255`.
- 2026-08-06 — shipped SWD-254 via PR #544 (merge `5fa0ac6`): product tree restored to `30814c4`; dual-mode nonlinear MPC removed from main.
- 2026-08-06 — SWD-254 implement: product tree matches `30814c4` (excl. SWD-254 docs); pytest 1750 passed / 6 skipped. PR #544.
- 2026-08-06 — SWD-254: restore tree to `30814c4` (pre PR #539 / SWD-240) to remove dual-mode nonlinear MPC after HA Core hang. Branch `cursor/swd-254-remove-nonlinear-mpc-2550`. Dual-mode artifacts (SWD-239/240/246/247/253 docs) removed with the revert.
